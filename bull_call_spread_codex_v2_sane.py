from AlgorithmImports import *
from datetime import time


class TastytradeOptionFeeModel(FeeModel):
    """$0.65/contract/fill approximates the screenshot's $25.61/trade."""

    def GetOrderFee(self, parameters):
        return OrderFee(CashAmount(0.65 * abs(parameters.Order.Quantity), "USD"))


class QqqTastytradeCallSpread(QCAlgorithm):
    """QQQ 21/7-delta, 35-DTE call-spread strategy from the screenshots."""

    TRADE_LOG_HEADER = (
        f"{'OPENED':<10} | {'CLOSED':<10} | {'PX OPEN':>7} | "
        f"{'PX CLOSE':>8} | {'L/S STRIKES':<11} | {'L/S DELTAS':<11} | {'EXP':<10} | "
        f"{'DTE':>3} | {'NUM':>3} | {'DEBIT':>5} | {'COST':>8} | "
        f"{'EQUITY':>10} | {'EQ%':>6} | {'VALUE@CLOSE':>11} | "
        f"{'P/L':>7} | {'REASON':<11} | MULT"
    )

    ENTRY_EQUITY_FRACTION = 0.007
    TARGET_DTE, MIN_DTE, MAX_DTE = 35, 0, 42
    LONG_DELTA, SHORT_DELTA = 0.21, 0.07
    MAX_DELTA_DISTANCE = 0.03
    ENTRY_VIX_MIN, ENTRY_VIX_MAX, VIX_EXIT = 8.0, 27.0, 28.0
    STOP_RETURN, PROFIT_RETURN = -0.84, 4.20
    DECISION_TIME = time(15, 45)
    DECISION_CUTOFF = time(15, 55)
    CONTRACT_MULTIPLIER = 100

    def Initialize(self):
        self.SetStartDate(2013, 1, 2)
        self.SetEndDate(2025, 6, 30)
        self.SetCash(25_000)
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
        self.vix = self.AddIndex("VIX", Resolution.Minute).Symbol
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
        self.partial_entry_count = self.entry_recovery_count = 0
        self.exit_recovery_count = self.residual_leg_count = 0
        self.invalid_quote_count = self.insufficient_cash_count = 0
        self.delta_rejection_count = 0
        self.expiration_failure_count = 0
        self.trade_log_rows = []
        self.trade_log_header_printed = False

    def OnSecuritiesChanged(self, changes):
        for security in changes.AddedSecurities:
            if security.Type == SecurityType.Option:
                security.SetFeeModel(TastytradeOptionFeeModel())
                security.set_buying_power_model(BuyingPowerModel.NULL)
                # The source backtester does not simulate American early
                # assignment of an individual short leg.
                security.SetOptionAssignmentModel(NullOptionAssignmentModel())

    def OnData(self, slice):
        # Evaluate once near the close: tradable quotes/Greeks with EOD behavior.
        if self.Time.time() < self.DECISION_TIME:
            return
        if self.last_decision_date == self.Time.date():
            return
        if self.Time.time() > self.DECISION_CUTOFF:
            self.last_decision_date = self.Time.date()
            return
        chain = slice.OptionChains.get(self.option)
        vix_security = self.Securities[self.vix]
        if chain is None or not vix_security.HasData or vix_security.Price <= 0:
            return

        self.last_decision_date = self.Time.date()
        vix = float(vix_security.Price)
        self.CheckExits(vix)
        if (self.Time.weekday() == 4 and
                self.ENTRY_VIX_MIN <= vix <= self.ENTRY_VIX_MAX):
            self.OpenFridaySpread(chain, vix)

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

        # Nearest-delta selection is not enough when a chain has incomplete or
        # unreliable Greeks. Do not let a distant contract produce an
        # unintended spread or drive the sizing ratchet.
        long_delta = float(long_call.Greeks.Delta)
        short_delta = float(short_call.Greeks.Delta)
        if (abs(long_delta - self.LONG_DELTA) > self.MAX_DELTA_DISTANCE or
                abs(short_delta - self.SHORT_DELTA) > self.MAX_DELTA_DISTANCE):
            self.delta_rejection_count += 1
            self.rejected_count += 1
            return

        # Reject mechanically invalid/stale markets, without adding a strategy
        # debit-ratio or liquidity filter.
        long_bid, long_ask = float(long_call.BidPrice), float(long_call.AskPrice)
        short_bid, short_ask = float(short_call.BidPrice), float(short_call.AskPrice)
        estimated_debit = long_ask - short_bid
        spread_width = float(short_call.Strike - long_call.Strike)
        if (not all(map(lambda x: x == x and abs(x) != float("inf"),
                        (long_bid, long_ask, short_bid, short_ask))) or
                long_bid <= 0 or long_ask < long_bid or short_bid <= 0 or
                short_ask < short_bid or estimated_debit <= 0 or
                estimated_debit >= spread_width or
                not self.Securities[long_call.Symbol].IsTradable or
                not self.Securities[short_call.Symbol].IsTradable):
            self.invalid_quote_count += 1
            self.rejected_count += 1
            return
        # Find the largest whole number whose net premium is strictly less than
        # the configured share of current equity. Option quotes are per share and each US equity
        # option contract controls 100 shares.
        current_equity = float(self.Portfolio.TotalPortfolioValue)
        # Include estimated opening fees in the cash-affordability ceiling.
        entry_fee_per_spread = 2.0 * 0.65
        cost_per_spread = (estimated_debit * self.CONTRACT_MULTIPLIER +
                           entry_fee_per_spread)
        entry_budget = current_equity * self.ENTRY_EQUITY_FRACTION
        percentage_sized_contracts = int(
            (entry_budget - 1e-9) // cost_per_spread
        )
        # Ratchet sizing: the percentage calculation can raise quantity, but a
        # later equity decline or more expensive spread can never lower it.
        # Only a successfully filled entry updates contract_high_water below.
        desired_contracts = max(percentage_sized_contracts,
                                self.contract_high_water)
        available_cash = max(0.0, float(self.Portfolio.CashBook["USD"].Amount))
        affordable_contracts = int(available_cash // cost_per_spread)
        contracts = min(desired_contracts, affordable_contracts)
        if contracts < 1:
            self.insufficient_cash_count += 1
            self.rejected_count += 1
            return

        pid = self.next_position_id
        self.next_position_id += 1
        self.positions[pid] = {
            "long": long_call.Symbol, "short": short_call.Symbol,
            "long_strike": float(long_call.Strike),
            "short_strike": float(short_call.Strike),
            "long_delta": long_delta,
            "short_delta": short_delta,
            "expiry": expiry, "state": "ENTERING", "entry_cashflow": 0.0,
            "entry_long_filled": 0, "entry_short_filled": 0,
            "entry_debit": None, "entry_date": self.Time.date(),
            "entry_underlying": float(self.Securities[self.underlying].Price),
            "entry_equity": current_equity,
            "entry_dte": (expiry.date() - self.Time.date()).days,
            "entry_vix": vix, "exit_reason": None,
            "contracts": contracts,
            "long_open_qty": 0, "short_open_qty": 0,
            "entry_fees": 0.0, "exit_fees": 0.0,
            "exit_cashflow": 0.0,
            "exit_long_filled": 0, "exit_short_filled": 0,
        }
        # Submit both contracts as one defined-risk order. Separate asynchronous
        # orders can make LEAN margin-check the short call as temporarily naked.
        entry_legs = [
            Leg.create(long_call.Symbol, 1),
            Leg.create(short_call.Symbol, -1),
        ]
        self.combo_market_order(entry_legs, contracts,
                                tag=f"TT:{pid}:ENTRY")

    def CheckExits(self, vix):
        for pid, position in list(self.positions.items()):
            if position["state"] != "OPEN":
                continue
            long_security = self.Securities[position["long"]]
            short_security = self.Securities[position["short"]]

            long_qty = position["long_open_qty"]
            short_qty = position["short_open_qty"]
            dte = (position["expiry"].date() - self.Time.date()).days

            # Expiration/exercise events are generated by LEAN rather than by
            # our tagged combo order. Remove an internal record once both legs
            # have already been settled so it can't order an expired symbol.
            if long_qty == 0 and short_qty == 0:
                # Zero quantities are closed only by recorded fills. Never
                # manufacture theoretical settlement P/L.
                if position["exit_long_filled"] or position["exit_short_filled"]:
                    self.FinishExit(pid)
                continue

            # An expired contract can remain in Securities briefly while being
            # non-tradable. LEAN will settle it; never submit another order.
            if not long_security.IsTradable or not short_security.IsTradable:
                if dte <= 1:
                    self.expiration_failure_count += 1
                    self.Error(f"TT:{pid}: expiring spread is not tradable")
                continue
            if not long_security.HasData or not short_security.HasData:
                continue
            # Executable liquidation value, not an optimistic midpoint.
            close_credit = (float(long_security.BidPrice) -
                            float(short_security.AskPrice))
            debit = position["entry_debit"]
            spread_return = (close_credit - debit) / debit
            reason = None
            if vix >= self.VIX_EXIT:
                reason = "VIX"
            elif spread_return <= self.STOP_RETURN:
                reason = "STOP"
            elif spread_return >= self.PROFIT_RETURN:
                reason = "PROFIT"
            # Close no later than the session before expiration. This is a
            # safety exit, not an additional profit/loss signal.
            elif dte <= 1:
                reason = "EXPIRY"
            if reason:
                self.CloseSpread(pid, reason, spread_return)

    def CloseSpread(self, pid, reason, spread_return=None):
        p = self.positions[pid]
        if p["state"] not in ("OPEN", "ENTERING"):
            return
        p["state"], p["exit_reason"] = "EXITING", reason
        suffix = "" if spread_return is None else f":R={spread_return:.4f}"
        long_qty, short_qty = p["long_open_qty"], p["short_open_qty"]
        if long_qty <= 0 or short_qty >= 0 or long_qty != -short_qty:
            self.residual_leg_count += 1
            self.RecoverPosition(pid, "EXIT")
            return
        exit_legs = [Leg.create(p["long"], -1), Leg.create(p["short"], 1)]
        self.combo_market_order(exit_legs, long_qty,
                                tag=f"TT:{pid}:EXIT:{reason}{suffix}")

    def RecoverPosition(self, pid, phase):
        """Flatten only this spread's tracked residual legs."""
        p = self.positions.get(pid)
        if p is None:
            return
        recovery_state = f"RECOVERING_{phase}"
        if p["state"] == recovery_state:
            return
        p["state"] = recovery_state
        if phase == "ENTRY":
            self.entry_recovery_count += 1
        else:
            self.exit_recovery_count += 1
        if p["long_open_qty"]:
            self.MarketOrder(p["long"], -p["long_open_qty"],
                             tag=f"TT:{pid}:RECOVERY_{phase}")
        if p["short_open_qty"]:
            self.MarketOrder(p["short"], -p["short_open_qty"],
                             tag=f"TT:{pid}:RECOVERY_{phase}")

    def FinishExit(self, pid):
        p = self.positions.get(pid)
        if p is None or p["long_open_qty"] != 0 or p["short_open_qty"] != 0:
            return
        reason = p["exit_reason"] or "RECOVERY"
        self.RecordTradeLog(p, reason)
        if reason == "STOP": self.stop_count += 1
        elif reason == "PROFIT": self.profit_count += 1
        elif reason == "VIX": self.vix_exit_count += 1
        else: self.expiry_exit_count += 1
        del self.positions[pid]

    @staticmethod
    def OrderEventFee(event):
        try:
            return float(event.OrderFee.Value.Amount)
        except Exception:
            return 0.0

    def OnOrderEvent(self, event):
        order = self.Transactions.GetOrderById(event.OrderId)
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
                if p["long_open_qty"] or p["short_open_qty"]:
                    self.partial_entry_count += 1
                else:
                    self.rejected_count += 1
                    del self.positions[pid]
                    return
                self.rejected_count += 1
                self.RecoverPosition(pid, "ENTRY")
            elif p["state"] == "EXITING":
                # Flatten residual legs immediately instead of waiting until
                # the next session (which could permit expiration exercise).
                self.RecoverPosition(pid, "EXIT")
            return
        if event.Status not in (OrderStatus.PartiallyFilled, OrderStatus.Filled):
            return
        action, qty, price = parts[2], int(event.FillQuantity), float(event.FillPrice)
        fee = self.OrderEventFee(event)
        is_long_leg = event.Symbol == p["long"]
        is_short_leg = event.Symbol == p["short"]
        if action == "ENTRY" and is_long_leg:
            p["entry_long_filled"] += abs(qty)
            p["entry_cashflow"] += qty * price
            p["long_open_qty"] += qty
            p["entry_fees"] += fee
        elif action == "ENTRY" and is_short_leg:
            p["entry_short_filled"] += abs(qty)
            p["entry_cashflow"] += qty * price
            p["short_open_qty"] += qty
            p["entry_fees"] += fee
        elif action == "EXIT" and is_long_leg:
            p["exit_long_filled"] += abs(qty)
            p["exit_cashflow"] += qty * price
            p["long_open_qty"] += qty
            p["exit_fees"] += fee
        elif action == "EXIT" and is_short_leg:
            p["exit_short_filled"] += abs(qty)
            p["exit_cashflow"] += qty * price
            p["short_open_qty"] += qty
            p["exit_fees"] += fee
        elif action.startswith("RECOVERY_") and (is_long_leg or is_short_leg):
            p["exit_cashflow"] += qty * price
            p["exit_fees"] += fee
            if is_long_leg:
                p["long_open_qty"] += qty
                p["exit_long_filled"] += abs(qty)
            else:
                p["short_open_qty"] += qty
                p["exit_short_filled"] += abs(qty)

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

        if p["state"] == "RECOVERING_ENTRY" and not (
                p["long_open_qty"] or p["short_open_qty"]):
            del self.positions[pid]
            return
        if p["state"] in ("EXITING", "RECOVERING_EXIT") and not (
                p["long_open_qty"] or p["short_open_qty"]):
            self.FinishExit(pid)

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
        net_pnl = gross_pnl - position["entry_fees"] - position["exit_fees"]
        multiple = close_value / debit if debit > 0 else 0.0
        reason_text = {
            "STOP": "SL", "PROFIT": "TP", "VIX": "VIX",
            "EXPIRY": "EXP", "INVALID_ENTRY": "INV",
            "RECOVERY": "REC",
        }.get(reason, str(reason).lower())
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
            f"{net_pnl:7.2f} | "
            f"{reason_text:<11} | "
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
        self.SetRuntimeStatistic("TT Open Spreads", str(len(self.positions)))
        self.SetRuntimeStatistic("TT Partial/Recovery",
                                 f"{self.partial_entry_count}/"
                                 f"{self.entry_recovery_count}/"
                                 f"{self.exit_recovery_count}")
        self.SetRuntimeStatistic("TT Invalid/Cash/Expiry",
                                 f"{self.invalid_quote_count}/"
                                 f"{self.insufficient_cash_count}/"
                                 f"{self.expiration_failure_count}")
        self.SetRuntimeStatistic("TT Delta Rejects",
                                 str(self.delta_rejection_count))
        self.SetRuntimeStatistic("TT Exits S/P/V/X",
                                 f"{self.stop_count}/{self.profit_count}/"
                                 f"{self.vix_exit_count}/{self.expiry_exit_count}")
        self.Log("TASTYTRADE QQQ FINAL | "
                 f"entries={self.entry_count} | contracts={self.total_contracts_entered} | "
                 f"stops={self.stop_count} | "
                 f"profits={self.profit_count} | vix_exits={self.vix_exit_count} | "
                 f"expiry_exits={self.expiry_exit_count} | "
                 f"rejected={self.rejected_count} | open={len(self.positions)}")
