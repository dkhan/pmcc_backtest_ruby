from AlgorithmImports import *
from datetime import date
from math import ceil


class TastytradeOptionFeeModel(FeeModel):
    """$0.65/contract/fill approximates the screenshot's $25.61/trade."""

    FEE_PER_CONTRACT = 0.65

    def GetOrderFee(self, parameters):
        fee = self.FEE_PER_CONTRACT * abs(parameters.Order.Quantity)
        return OrderFee(CashAmount(fee, "USD"))


class QqqTastytradeCallSpread(QCAlgorithm):
    """QQQ 21/7-delta, 35-DTE call-spread strategy from the screenshots."""

    TRADE_LOG_HEADER = (
        f"{'OPENED':<10} | {'CLOSED':<10} | {'PX OPEN':>7} | "
        f"{'PX CLOSE':>8} | {'L/S STRIKES':<11} | {'L/S DELTAS':<11} | {'EXP':<10} | "
        f"{'DTE':>3} | {'NUM':>3} | {'DEBIT':>5} | {'COST':>8} | "
        f"{'EQUITY':>10} | {'EQ%':>6} | {'VALUE@CLOSE':>11} | "
        f"{'P/L':>7} | {'RSN':<4} | MULT"
    )

    # Optional portfolio modes. Both are disabled by default.
    HOLD_IDLE_CASH_IN_QQQ = False
    USE_VIX_RULES = False

    # Set to None to use ENTRY_EQUITY_FRACTION and the non-decreasing
    # contract ratchet. Any positive integer keeps every entry at that fixed
    # number of contracts and ignores percentage sizing.
    FIXED_CONTRACTS = 10
    ENTRY_EQUITY_FRACTION = 0.01
    TARGET_DTE, MIN_DTE, MAX_DTE = 35, 0, 42
    LONG_DELTA, SHORT_DELTA = 0.21, 0.07
    ENTRY_VIX_MIN, ENTRY_VIX_MAX, VIX_EXIT = 8.0, 27.0, 28.0
    STOP_RETURN, PROFIT_RETURN = -0.84, 4.20
    BACKTEST_END = date(2025, 6, 30)

    def Initialize(self):
        self.SetStartDate(2013, 1, 2)
        self.SetEndDate(2025, 6, 30)
        self.SetCash(12_330)
        self.SetTimeZone(TimeZones.NewYork)

        equity = self.AddEquity("QQQ", Resolution.Minute,
                                dataNormalizationMode=DataNormalizationMode.Raw)
        self.underlying = equity.Symbol
        option = self.AddOption("QQQ", Resolution.Minute)
        option.SetFilter(lambda u: u.IncludeWeeklys().Strikes(-60, 60)
                         .Expiration(self.MIN_DTE, self.MAX_DTE))
        # Since LEAN's February 2026 pricing-model update, Crank-Nicolson is
        # available only in the legacy QuantLib namespace. QQQ options are
        # American-style, so use the current supported binomial model.
        option.PriceModel = OptionPriceModels.binomial_cox_ross_rubinstein()
        self.option = option.Symbol
        self.vix = (
            self.AddIndex("VIX", Resolution.Minute).Symbol
            if self.USE_VIX_RULES else None
        )
        self.SetBenchmark(self.underlying)

        # Tastytrade's test treats each debit spread as one defined-risk unit.
        # Disable LEAN's leg-level/position-group margin checks; the algorithm
        # explicitly enforces the configured equity-per-entry sizing rule.
        self.portfolio.set_positions(SecurityPositionGroupModel.NULL)
        self.Portfolio.MarginCallModel = MarginCallModel.Null

        self.positions = {}
        self.next_position_id = 1
        self.last_decision_date = None
        self.entry_count = self.stop_count = self.profit_count = 0
        self.total_contracts_entered = 0
        self.contract_high_water = 0
        self.vix_exit_count = self.expiry_exit_count = self.rejected_count = 0
        self.trade_log_rows = []
        self.trade_log_header_printed = False
        self.current_chain = None
        self.current_vix = None
        self.exercise_repairs = 0

        # Expiration protection runs every trading session. Normal entries and
        # signal exits run only on the last QQQ trading day of each week, which
        # is Friday normally and Thursday when Friday is a market holiday.
        # BeforeMarketClose is exchange-aware, including shortened sessions.
        self.Schedule.On(
            self.DateRules.EveryDay(self.underlying),
            self.TimeRules.BeforeMarketClose(self.underlying, 15),
            self.CloseExpiringPositions,
        )
        self.Schedule.On(
            self.DateRules.WeekEnd(self.underlying),
            self.TimeRules.BeforeMarketClose(self.underlying, 15),
            self.WeeklyTradeDecision,
        )
        self.Schedule.On(
            self.DateRules.On(
                self.BACKTEST_END.year,
                self.BACKTEST_END.month,
                self.BACKTEST_END.day,
            ),
            self.TimeRules.BeforeMarketClose(self.underlying, 15),
            self.FinalSpreadLiquidation,
        )

    def OnSecuritiesChanged(self, changes):
        for security in changes.AddedSecurities:
            if security.Type == SecurityType.Option:
                security.SetFeeModel(TastytradeOptionFeeModel())
                security.set_buying_power_model(BuyingPowerModel.NULL)
                # The source backtester does not simulate American early
                # assignment of an individual short leg.
                security.SetOptionAssignmentModel(NullOptionAssignmentModel())

    def OnData(self, slice):
        # Cache data for the exchange-aware scheduled events. Do not place
        # orders here: all planned trading occurs 15 minutes before the close.
        chain = slice.OptionChains.get(self.option)
        if chain is not None:
            self.current_chain = chain

        if self.USE_VIX_RULES:
            vix_security = self.Securities[self.vix]
            if vix_security.HasData and vix_security.Price > 0:
                self.current_vix = float(vix_security.Price)

    def WeeklyTradeDecision(self):
        """Run signals on the final QQQ trading session of the week."""
        if self.last_decision_date == self.Time.date():
            return
        chain = self.current_chain
        if chain is None:
            self.rejected_count += 1
            return
        if self.USE_VIX_RULES and self.current_vix is None:
            self.rejected_count += 1
            return

        self.last_decision_date = self.Time.date()
        vix = self.current_vix if self.USE_VIX_RULES else None
        self.CheckExits(vix, force_week_end_expiry=True)
        vix_entry_allowed = (
            not self.USE_VIX_RULES or
            self.ENTRY_VIX_MIN <= vix <= self.ENTRY_VIX_MAX
        )
        if vix_entry_allowed:
            self.OpenFridaySpread(chain, vix)
        self.SweepIdleCashToQqq()

    def CloseExpiringPositions(self):
        """Close contracts on their expiration session before exercise."""
        for pid, position in list(self.positions.items()):
            if position["state"] != "OPEN":
                continue
            dte = (position["expiry"].date() - self.Time.date()).days
            if dte <= 0:
                self.CloseSpread(pid, "EXPIRY")

    def FinalSpreadLiquidation(self):
        """Leave no option spread open at the end of the backtest."""
        for pid, position in list(self.positions.items()):
            if position["state"] == "OPEN":
                self.CloseSpread(pid, "END")

    def OpenFridaySpread(self, chain, vix):
        calls = [c for c in chain
                 if c.Right == OptionRight.Call
                 and c.Expiry.date() > self.Time.date()
                 and c.Greeks is not None
                 and c.Greeks.Delta is not None
                 and float(c.Greeks.Delta) > 0]
        if not calls:
            self.rejected_count += 1
            return
        expiry = min({c.Expiry for c in calls}, key=lambda x: abs(
            (x.date() - self.Time.date()).days - self.TARGET_DTE))
        expiry_calls = [c for c in calls if c.Expiry == expiry]
        long_call = min(expiry_calls,
                        key=lambda c: abs(float(c.Greeks.Delta) - self.LONG_DELTA))
        short_candidates = [c for c in expiry_calls if c.Strike > long_call.Strike]
        if not short_candidates:
            self.rejected_count += 1
            return
        short_call = min(short_candidates,
                         key=lambda c: abs(float(c.Greeks.Delta) - self.SHORT_DELTA))

        # Require a valid debit at executable quotes.
        estimated_debit = float(long_call.AskPrice) - float(short_call.BidPrice)
        if (float(long_call.AskPrice) <= 0 or float(short_call.BidPrice) < 0 or
                estimated_debit <= 0):
            self.rejected_count += 1
            return
        # Find the largest whole number whose net premium is strictly less than
        # the configured share of current equity. Option quotes are per share and each US equity
        # option contract controls 100 shares.
        current_equity = float(self.Portfolio.TotalPortfolioValue)
        cost_per_spread = estimated_debit * 100.0
        entry_budget = current_equity * self.ENTRY_EQUITY_FRACTION
        if self.FIXED_CONTRACTS is not None:
            contracts = int(self.FIXED_CONTRACTS)
        else:
            percentage_sized_contracts = int(
                (entry_budget - 1e-9) // cost_per_spread
            )
            # Ratchet sizing: the percentage calculation can raise quantity,
            # but equity declines or more expensive spreads cannot lower it.
            contracts = max(percentage_sized_contracts, self.contract_high_water)
        if contracts < 1:
            self.rejected_count += 1
            return

        estimated_entry_cost = (
            cost_per_spread * contracts +
            2 * TastytradeOptionFeeModel.FEE_PER_CONTRACT * contracts
        )
        self.RaiseCashForTrade(estimated_entry_cost)

        pid = self.next_position_id
        self.next_position_id += 1
        self.positions[pid] = {
            "long": long_call.Symbol, "short": short_call.Symbol,
            "long_strike": float(long_call.Strike),
            "short_strike": float(short_call.Strike),
            "long_delta": float(long_call.Greeks.Delta),
            "short_delta": float(short_call.Greeks.Delta),
            "expiry": expiry, "state": "ENTERING", "entry_cashflow": 0.0,
            "entry_long_filled": 0, "entry_short_filled": 0,
            "entry_debit": None, "entry_date": self.Time.date(),
            "entry_underlying": float(self.Securities[self.underlying].Price),
            "entry_equity": current_equity,
            "entry_dte": (expiry.date() - self.Time.date()).days,
            "entry_vix": vix, "exit_reason": None,
            "contracts": contracts,
            "exit_cashflow": 0.0,
            "exit_long_filled": 0, "exit_short_filled": 0,
            "fallback_submitted": False,
        }
        # Submit both contracts as one defined-risk order. Separate asynchronous
        # orders can make LEAN margin-check the short call as temporarily naked.
        entry_legs = [
            Leg.create(long_call.Symbol, 1),
            Leg.create(short_call.Symbol, -1),
        ]
        self.combo_market_order(entry_legs, contracts,
                                tag=f"TT:{pid}:ENTRY")

    def CheckExits(self, vix, force_week_end_expiry=False):
        for pid, position in list(self.positions.items()):
            if position["state"] != "OPEN":
                continue
            long_security = self.Securities[position["long"]]
            short_security = self.Securities[position["short"]]

            long_qty = int(self.Portfolio[position["long"]].Quantity)
            short_qty = int(self.Portfolio[position["short"]].Quantity)
            dte = (position["expiry"].date() - self.Time.date()).days

            # Expiration/exercise events are generated by LEAN rather than by
            # our tagged combo order. Remove an internal record once both legs
            # have already been settled so it can't order an expired symbol.
            if long_qty == 0 and short_qty == 0:
                underlying_price = float(self.Securities[self.underlying].Price)
                intrinsic_value = (
                    max(underlying_price - position["long_strike"], 0.0) -
                    max(underlying_price - position["short_strike"], 0.0)
                )
                position["exit_cashflow"] = -intrinsic_value * position["contracts"]
                self.RecordTradeLog(position, "EXPIRY")
                self.expiry_exit_count += 1
                del self.positions[pid]
                continue

            # An expired contract can remain in Securities briefly while being
            # non-tradable. LEAN will settle it; never submit another order.
            if not long_security.IsTradable or not short_security.IsTradable:
                continue
            if not long_security.HasData or not short_security.HasData:
                continue
            # Executable liquidation value, not an optimistic midpoint.
            close_credit = (float(long_security.BidPrice) -
                            float(short_security.AskPrice))
            debit = position["entry_debit"]
            spread_return = (close_credit - debit) / debit
            reason = None
            if self.USE_VIX_RULES and vix >= self.VIX_EXIT:
                reason = "VIX"
            elif spread_return <= self.STOP_RETURN:
                reason = "STOP"
            elif spread_return >= self.PROFIT_RETURN:
                reason = "PROFIT"
            # On the week's final session, also catch legacy Saturday-dated
            # expirations and holiday-shifted Friday expirations.
            elif force_week_end_expiry and dte <= 3:
                reason = "EXPIRY"
            if reason:
                self.CloseSpread(pid, reason, spread_return)

    def CloseSpread(self, pid, reason, spread_return=None):
        p = self.positions[pid]
        if p["state"] not in ("OPEN", "ENTERING"):
            return
        p["state"], p["exit_reason"] = "EXITING", reason
        suffix = "" if spread_return is None else f":R={spread_return:.4f}"
        exit_legs = [
            Leg.create(p["long"], -1),
            Leg.create(p["short"], 1),
        ]
        self.combo_market_order(exit_legs, p["contracts"],
                                tag=f"TT:{pid}:EXIT:{reason}{suffix}")

    def SubmitFallbackExit(self, pid):
        """Close each leg if LEAN cancels the combo liquidation order."""
        p = self.positions.get(pid)
        if p is None or p["fallback_submitted"]:
            return
        p["fallback_submitted"] = True
        p["state"] = "EXITING"
        reason = p["exit_reason"] or "EXIT"
        # Limit the fallback to this spread. Holdings can be aggregated when
        # two independently opened spreads happen to use the same contract.
        short_qty = -max(0, p["contracts"] - p["exit_short_filled"])
        long_qty = max(0, p["contracts"] - p["exit_long_filled"])
        # Remove the short exposure first, then sell the long contract.
        if short_qty:
            self.MarketOrder(
                p["short"], -short_qty, tag=f"TT:{pid}:EXIT:{reason}:FB"
            )
        if long_qty:
            self.MarketOrder(
                p["long"], -long_qty, tag=f"TT:{pid}:EXIT:{reason}:FB"
            )

    def RaiseCashForTrade(self, required_cash):
        """Sell only enough QQQ shares to fund the next option entry."""
        if not self.HOLD_IDLE_CASH_IN_QQQ:
            return
        cash = float(self.Portfolio.CashBook["USD"].Amount)
        shortfall = required_cash - cash
        price = float(self.Securities[self.underlying].Price)
        shares_owned = int(max(0, self.Portfolio[self.underlying].Quantity))
        if shortfall <= 0 or price <= 0 or shares_owned <= 0:
            return
        shares_to_sell = min(shares_owned, int(ceil(shortfall / price)))
        if shares_to_sell > 0:
            self.MarketOrder(
                self.underlying, -shares_to_sell,
                tag="QQQ_CASH_FOR_OPTION_ENTRY",
            )

    def SweepIdleCashToQqq(self):
        """Keep idle whole-share buying power invested in QQQ when enabled."""
        if not self.HOLD_IDLE_CASH_IN_QQQ:
            return
        cash = float(self.Portfolio.CashBook["USD"].Amount)
        price = float(self.Securities[self.underlying].Price)
        if cash <= 0 or price <= 0:
            return
        shares_to_buy = int(cash // price)
        if shares_to_buy > 0:
            self.MarketOrder(
                self.underlying, shares_to_buy,
                tag="QQQ_IDLE_CASH_SWEEP",
            )

    def OnOrderEvent(self, event):
        order = self.Transactions.GetOrderById(event.OrderId)
        # This should never be reached after the pre-expiration exits. If LEAN
        # nevertheless exercises an ITM long call, immediately sell exactly
        # the shares produced by that exercise. This preserves any intentional
        # QQQ cash-sweep holding.
        if (order is not None and order.Type == OrderType.OptionExercise and
                event.Status in (OrderStatus.PartiallyFilled, OrderStatus.Filled) and
                event.Symbol.SecurityType == SecurityType.Option and
                event.Symbol.ID.OptionRight == OptionRight.Call and
                int(event.FillQuantity) < 0 and
                float(event.FillPrice) > 0):
            multiplier = int(
                self.Securities[event.Symbol].SymbolProperties.ContractMultiplier
            )
            shares_to_offset = int(event.FillQuantity) * multiplier
            if shares_to_offset:
                self.MarketOrder(
                    self.underlying, shares_to_offset,
                    tag="QQQ_EXERCISE_REPAIR",
                )
                self.exercise_repairs += 1

        tag = None if order is None else order.Tag
        if not tag or not tag.startswith("TT:"):
            return
        parts = tag.split(":")
        try:
            pid = int(parts[1])
        except (ValueError, IndexError):
            return
        p = self.positions.get(pid)
        if p is None:
            return
        if event.Status in (OrderStatus.Invalid, OrderStatus.Canceled):
            if p["state"] == "ENTERING":
                self.rejected_count += 1
                del self.positions[pid]
            elif p["state"] == "EXITING":
                # Do not wait until the next week or allow expiration. A combo
                # cancellation immediately falls back to individual leg exits.
                if ":FB" not in tag:
                    self.SubmitFallbackExit(pid)
                else:
                    p["state"] = "OPEN"
            return
        if event.Status not in (OrderStatus.PartiallyFilled, OrderStatus.Filled):
            return
        action, qty, price = parts[2], int(event.FillQuantity), float(event.FillPrice)
        is_long_leg = event.Symbol == p["long"]
        is_short_leg = event.Symbol == p["short"]
        if action == "ENTRY" and is_long_leg:
            p["entry_long_filled"] += abs(qty)
            p["entry_cashflow"] += qty * price
        elif action == "ENTRY" and is_short_leg:
            p["entry_short_filled"] += abs(qty)
            p["entry_cashflow"] += qty * price
        elif action == "EXIT" and is_long_leg:
            p["exit_long_filled"] += abs(qty)
            p["exit_cashflow"] += qty * price
        elif action == "EXIT" and is_short_leg:
            p["exit_short_filled"] += abs(qty)
            p["exit_cashflow"] += qty * price

        if (p["state"] == "ENTERING" and
                p["entry_long_filled"] >= p["contracts"] and
                p["entry_short_filled"] >= p["contracts"]):
            debit = p["entry_cashflow"] / p["contracts"]
            if debit <= 0:
                self.CloseSpread(pid, "INVALID_ENTRY")
            else:
                p["entry_debit"], p["state"] = debit, "OPEN"
                p["entry_date"] = self.Time.date()
                p["entry_underlying"] = float(self.Securities[self.underlying].Price)
                p["entry_equity"] = float(self.Portfolio.TotalPortfolioValue)
                p["entry_dte"] = (p["expiry"].date() - self.Time.date()).days
                self.entry_count += 1
                self.total_contracts_entered += p["contracts"]
                self.contract_high_water = max(
                    self.contract_high_water, p["contracts"]
                )

        if (p["state"] == "EXITING" and
                p["exit_long_filled"] >= p["contracts"] and
                p["exit_short_filled"] >= p["contracts"]):
            reason = p["exit_reason"]
            self.RecordTradeLog(p, reason)
            if reason == "STOP": self.stop_count += 1
            elif reason == "PROFIT": self.profit_count += 1
            elif reason == "VIX": self.vix_exit_count += 1
            else: self.expiry_exit_count += 1
            del self.positions[pid]

    def OnAssignmentOrderEvent(self, assignment_event):
        """Neutralize shares from an unexpected short-call assignment."""
        symbol = assignment_event.Symbol
        if (symbol.SecurityType != SecurityType.Option or
                symbol.ID.OptionRight != OptionRight.Call):
            return
        multiplier = int(
            self.Securities[symbol].SymbolProperties.ContractMultiplier
        )
        shares_to_offset = -int(assignment_event.Quantity) * multiplier
        if shares_to_offset:
            self.MarketOrder(
                self.underlying, shares_to_offset,
                tag="QQQ_ASSIGNMENT_REPAIR",
            )
            self.exercise_repairs += 1

    def RecordTradeLog(self, position, reason):
        contracts = position["contracts"]
        debit = position["entry_debit"]
        # Closing the long produces negative signed quantity and closing the
        # short produces positive signed quantity, so negate net cash flow.
        close_value = -position["exit_cashflow"] / contracts
        trade_cost = debit * 100.0 * contracts
        entry_equity = position["entry_equity"]
        equity_fraction = trade_cost / entry_equity if entry_equity > 0 else 0.0
        gross_pnl = (close_value - debit) * 100.0 * contracts
        multiple = close_value / debit if debit > 0 else 0.0
        reason_text = {
            "STOP": "SL",
            "PROFIT": "TP",
            "VIX": "VIX",
            "EXPIRY": "EXP",
            "INVALID_ENTRY": "INV",
            "END": "END",
        }.get(reason, str(reason)[:4].upper())
        strikes = f"{position['long_strike']:.1f}/{position['short_strike']:.1f}"
        deltas = f"{position['long_delta']:.2f}/{position['short_delta']:.2f}"
        row = (
            f"{position['entry_date']:%Y-%m-%d} | "
            f"{self.Time.date():%Y-%m-%d} | "
            f"{position['entry_underlying']:7.2f} | "
            f"{float(self.Securities[self.underlying].Price):8.2f} | "
            f"{strikes:<11} | "
            f"{deltas:<11} | "
            f"{position['expiry'].date():%Y-%m-%d} | "
            f"{position['entry_dte']:3d} | "
            f"{contracts:3d} | "
            f"{debit:5.2f} | "
            f"{trade_cost:8.2f} | "
            f"{entry_equity:10.2f} | "
            f"{equity_fraction:6.2%} | "
            f"{close_value:11.2f} | "
            f"{gross_pnl:7.2f} | "
            f"{reason_text:<4} | "
            f"{multiple:.2f}"
        )
        self.trade_log_rows.append(row)
        if not self.trade_log_header_printed:
            self.Debug(self.TRADE_LOG_HEADER)
            self.trade_log_header_printed = True
        self.Debug(row)

    def OnEndOfAlgorithm(self):
        self.SetRuntimeStatistic("TT Entries", str(self.entry_count))
        self.SetRuntimeStatistic("TT Contracts", str(self.total_contracts_entered))
        self.SetRuntimeStatistic("TT Contract Floor", str(self.contract_high_water))
        self.SetRuntimeStatistic(
            "TT Sizing",
            f"FIXED {self.FIXED_CONTRACTS}"
            if self.FIXED_CONTRACTS is not None
            else f"DYNAMIC {self.ENTRY_EQUITY_FRACTION:.2%}",
        )
        self.SetRuntimeStatistic(
            "TT QQQ Cash Sweep", "ON" if self.HOLD_IDLE_CASH_IN_QQQ else "OFF"
        )
        self.SetRuntimeStatistic(
            "TT VIX Rules", "ON" if self.USE_VIX_RULES else "OFF"
        )
        self.SetRuntimeStatistic("TT Open Spreads", str(len(self.positions)))
        self.SetRuntimeStatistic("TT Exercise Repairs", str(self.exercise_repairs))
        self.SetRuntimeStatistic("TT Exits S/P/V/X",
                                 f"{self.stop_count}/{self.profit_count}/"
                                 f"{self.vix_exit_count}/{self.expiry_exit_count}")
        self.Log("TASTYTRADE QQQ FINAL | "
                 f"entries={self.entry_count} | contracts={self.total_contracts_entered} | "
                 f"stops={self.stop_count} | "
                 f"profits={self.profit_count} | vix_exits={self.vix_exit_count} | "
                 f"expiry_exits={self.expiry_exit_count} | "
                 f"rejected={self.rejected_count} | open={len(self.positions)}")
