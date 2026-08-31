from AlgorithmImports import *
from datetime import time


class TastytradeOptionFeeModel(FeeModel):
    """$0.65/contract/fill approximates the screenshot's $25.61/trade."""

    def GetOrderFee(self, parameters):
        return OrderFee(CashAmount(0.65 * abs(parameters.Order.Quantity), "USD"))


class QqqTastytradeCallSpread(QCAlgorithm):
    """QQQ 21/7-delta, 35-DTE call-spread strategy from the screenshots."""

    CONTRACTS = 10
    TARGET_DTE, MIN_DTE, MAX_DTE = 35, 0, 42
    LONG_DELTA, SHORT_DELTA = 0.21, 0.07
    ENTRY_VIX_MIN, ENTRY_VIX_MAX, VIX_EXIT = 8.0, 27.0, 28.0
    STOP_RETURN, PROFIT_RETURN = -0.84, 4.20
    DECISION_TIME = time(15, 45)
    CAPITAL_LIMIT = 12_330.0

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
        self.vix = self.AddIndex("VIX", Resolution.Minute).Symbol
        self.SetBenchmark(self.underlying)

        # Tastytrade's test treats each debit spread as one defined-risk unit.
        # Disable LEAN's leg-level/position-group margin checks and enforce the
        # screenshot's $12,330 capital limit explicitly before every entry.
        self.portfolio.set_positions(SecurityPositionGroupModel.NULL)
        self.Portfolio.MarginCallModel = MarginCallModel.Null

        self.positions = {}
        self.next_position_id = 1
        self.last_decision_date = None
        self.entry_count = self.stop_count = self.profit_count = 0
        self.vix_exit_count = self.expiry_exit_count = self.rejected_count = 0

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

        # Require a valid debit at executable quotes.
        estimated_debit = float(long_call.AskPrice) - float(short_call.BidPrice)
        if (float(long_call.AskPrice) <= 0 or float(short_call.BidPrice) < 0 or
                estimated_debit <= 0):
            self.rejected_count += 1
            return
        estimated_capital = estimated_debit * 100 * self.CONTRACTS
        reserved_capital = sum(
            p["capital_at_risk"] for p in self.positions.values()
            if p["state"] in ("ENTERING", "OPEN", "EXITING")
        )
        if reserved_capital + estimated_capital > self.CAPITAL_LIMIT:
            self.rejected_count += 1
            return

        pid = self.next_position_id
        self.next_position_id += 1
        self.positions[pid] = {
            "long": long_call.Symbol, "short": short_call.Symbol,
            "expiry": expiry, "state": "ENTERING", "entry_cashflow": 0.0,
            "entry_long_filled": 0, "entry_short_filled": 0,
            "entry_debit": None, "entry_date": self.Time.date(),
            "entry_vix": vix, "exit_reason": None,
            "capital_at_risk": estimated_capital,
            "exit_long_filled": 0, "exit_short_filled": 0,
        }
        # Submit both contracts as one defined-risk order. Separate asynchronous
        # orders can make LEAN margin-check the short call as temporarily naked.
        entry_legs = [
            Leg.create(long_call.Symbol, 1),
            Leg.create(short_call.Symbol, -1),
        ]
        self.combo_market_order(entry_legs, self.CONTRACTS,
                                tag=f"TT:{pid}:ENTRY")

    def CheckExits(self, vix):
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
        exit_legs = [
            Leg.create(p["long"], -1),
            Leg.create(p["short"], 1),
        ]
        self.combo_market_order(exit_legs, self.CONTRACTS,
                                tag=f"TT:{pid}:EXIT:{reason}{suffix}")

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
                self.rejected_count += 1
                del self.positions[pid]
            elif p["state"] == "EXITING":
                # Keep tracking the spread and retry its exit next session.
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
        elif action == "EXIT" and is_short_leg:
            p["exit_short_filled"] += abs(qty)

        if (p["state"] == "ENTERING" and
                p["entry_long_filled"] >= self.CONTRACTS and
                p["entry_short_filled"] >= self.CONTRACTS):
            debit = p["entry_cashflow"] / self.CONTRACTS
            if debit <= 0:
                self.CloseSpread(pid, "INVALID_ENTRY")
            else:
                p["entry_debit"], p["state"] = debit, "OPEN"
                p["capital_at_risk"] = debit * 100 * self.CONTRACTS
                self.entry_count += 1

        if (p["state"] == "EXITING" and
                p["exit_long_filled"] >= self.CONTRACTS and
                p["exit_short_filled"] >= self.CONTRACTS):
            reason = p["exit_reason"]
            if reason == "STOP": self.stop_count += 1
            elif reason == "PROFIT": self.profit_count += 1
            elif reason == "VIX": self.vix_exit_count += 1
            else: self.expiry_exit_count += 1
            del self.positions[pid]

    def OnEndOfAlgorithm(self):
        self.SetRuntimeStatistic("TT Entries", str(self.entry_count))
        self.SetRuntimeStatistic("TT Open Spreads", str(len(self.positions)))
        self.SetRuntimeStatistic("TT Exits S/P/V/X",
                                 f"{self.stop_count}/{self.profit_count}/"
                                 f"{self.vix_exit_count}/{self.expiry_exit_count}")
        self.Log("TASTYTRADE QQQ FINAL | "
                 f"entries={self.entry_count} | stops={self.stop_count} | "
                 f"profits={self.profit_count} | vix_exits={self.vix_exit_count} | "
                 f"expiry_exits={self.expiry_exit_count} | "
                 f"rejected={self.rejected_count} | open={len(self.positions)}")
