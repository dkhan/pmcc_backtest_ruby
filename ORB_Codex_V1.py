from AlgorithmImports import *
from datetime import time
from math import floor


class CustomPerShareFeeModel(FeeModel):
    """Paper commission: USD 0.0005 per share on every fill."""

    def __init__(self, fee_per_share):
        self.fee_per_share = fee_per_share

    def GetOrderFee(self, parameters):
        fee = parameters.Order.AbsoluteQuantity * self.fee_per_share
        return OrderFee(CashAmount(fee, "USD"))


class ExactQqqOrbPaperReplication(QCAlgorithm):
    STARTING_CASH = 25_000
    RISK_FRACTION = 0.01
    EXPOSURE_CAP = 4.0
    TARGET_R = 10.0

    def Initialize(self):
        self.SetStartDate(2016, 1, 1)
        self.SetEndDate(2023, 2, 17)
        self.SetCash(self.STARTING_CASH)
        self.SetTimeZone(TimeZones.NewYork)

        self.security = self.AddEquity(
            "QQQ",
            Resolution.Minute,
            dataNormalizationMode=DataNormalizationMode.Raw,
            fillForward=False,
            extendedMarketHours=False,
        )
        self.symbol = self.security.Symbol
        self.security.SetSlippageModel(ConstantSlippageModel(0))
        self.security.SetFeeModel(CustomPerShareFeeModel(0.0005))

        # The paper's 4x rule is enforced explicitly in position sizing. LEAN's
        # independent margin checks must not reject or partially liquidate a
        # position that already satisfies that paper rule.
        self.security.SetBuyingPowerModel(BuyingPowerModel.Null)
        self.Portfolio.MarginCallModel = MarginCallModel.Null

        self.consolidator = TradeBarConsolidator(timedelta(minutes=5))
        self.consolidator.DataConsolidated += self.OnFiveMinuteBar
        self.SubscriptionManager.AddConsolidator(self.symbol, self.consolidator)

        self.current_date = None
        self.traded_today = False
        self.opening_bar = None

        self.entry_ticket = None
        self.stop_ticket = None
        self.target_ticket = None
        self.close_ticket = None
        self.correction_ticket = None

        self.planned_stop = None
        self.entry_fill_price = None
        self.entry_fill_quantity = 0
        self.initial_risk_dollars = 0.0
        self.trade_fees = 0.0
        self.exit_pending = False
        self.exit_reason = None

        self.trade_count = 0
        self.long_count = 0
        self.short_count = 0
        self.win_count = 0
        self.stop_count = 0
        self.target_count = 0
        self.eod_count = 0
        self.rejected_entry_count = 0
        self.intended_entry_count = 0
        self.entry_sizing_drift_count = 0
        self.double_fill_count = 0
        self.doji_count = 0
        self.realized_r_values = []

        self.Schedule.On(
            self.DateRules.EveryDay(self.symbol),
            self.TimeRules.BeforeMarketClose(self.symbol, 20),
            self.SubmitMarketOnCloseExit,
        )
        self.Schedule.On(
            self.DateRules.EveryDay(self.symbol),
            self.TimeRules.BeforeMarketClose(self.symbol, 1),
            self.PrepareMarketOnCloseFill,
        )
        self.SetBenchmark(self.symbol)
        self.Debug(
            "ORB V1 | QQQ | 2016-01-01..2023-02-17 | risk=1% | "
            "exposure_cap=4x | target=10R | fee=$0.0005/share | slippage=0"
        )

    def OnFiveMinuteBar(self, sender, bar):
        if bar.Time.time() != time(9, 30):
            return

        if self.current_date != bar.Time.date():
            self.current_date = bar.Time.date()
            self.traded_today = False
            self.opening_bar = None

        self.opening_bar = bar
        if self.traded_today or self.Portfolio[self.symbol].Invested:
            return
        if bar.Open == bar.Close:
            self.doji_count += 1
            self.traded_today = True
            return

        direction = 1 if bar.Close > bar.Open else -1
        self.intended_entry_count += 1

        # Submit an unconditional market order as soon as the opening range is
        # complete at 09:35 ET. Unlike the former limit order, it cannot silently
        # select only days that revisit the opening-range close.
        entry_estimate = round(float(bar.Close), 2)
        self.planned_stop = round(
            float(bar.Low if direction > 0 else bar.High), 2
        )
        risk_per_share = abs(entry_estimate - self.planned_stop)
        if risk_per_share <= 0:
            self.rejected_entry_count += 1
            self.traded_today = True
            return

        equity = float(self.Portfolio.TotalPortfolioValue)
        risk_sized = floor(equity * self.RISK_FRACTION / risk_per_share)
        exposure_sized = floor(equity * self.EXPOSURE_CAP / entry_estimate)
        shares = int(min(risk_sized, exposure_sized))
        if shares <= 0:
            self.rejected_entry_count += 1
            self.traded_today = True
            return

        quantity = direction * shares
        self.traded_today = True
        self.exit_pending = False
        self.exit_reason = None
        self.trade_fees = 0.0

        side = "LONG" if direction > 0 else "SHORT"
        tag = (
            f"ENTRY_{side} | estimate={entry_estimate:.2f} | "
            f"stop={self.planned_stop:.2f} | shares={shares}"
        )
        self.entry_ticket = self.MarketOrder(
            self.symbol,
            quantity,
            asynchronous=True,
            tag=tag,
        )

    def OnData(self, data):
        # Order events for a slice are processed before OnData. Waiting until
        # here lets all same-slice sibling events arrive before state is reset.
        if self.exit_pending:
            holding = int(self.Portfolio[self.symbol].Quantity)
            if holding == 0:
                self.FinalizeCompletedTrade()
            elif self.correction_ticket is None and self.Securities[self.symbol].Exchange.ExchangeOpen:
                self.double_fill_count += 1
                self.correction_ticket = self.MarketOrder(
                    self.symbol, -holding, tag="CORRECT_DOUBLE_EXIT_FILL"
                )

    def OnOrderEvent(self, orderEvent):
        order_id = orderEvent.OrderId
        order = self.Transactions.GetOrderById(order_id)
        tag = order.Tag if order is not None else ""

        # MarketOrder can fill synchronously inside the call that returns its
        # ticket. In that callback self.entry_ticket has not been assigned yet,
        # so route immutable ENTRY tags as well as known ticket IDs.
        is_entry = tag.startswith("ENTRY_") or (
            self.entry_ticket is not None and
            order_id == self.entry_ticket.OrderId
        )
        if is_entry:
            if orderEvent.Status == OrderStatus.Filled:
                if self.entry_fill_price is None:
                    self.HandleEntryFill(orderEvent)
            elif orderEvent.Status in (OrderStatus.Invalid, OrderStatus.Canceled):
                self.rejected_entry_count += 1
                self.ClearOrderState()
            return

        if orderEvent.Status != OrderStatus.Filled:
            return

        self.trade_fees += abs(float(orderEvent.OrderFee.Value.Amount))

        reason = self.ExitReasonFor(order_id, tag)
        if reason is not None:
            if self.exit_pending:
                # A sibling filled before its cancellation was processed.
                # Preserve state so OnData can flatten the residual position.
                self.double_fill_count += 1
                return
            self.exit_pending = True
            self.exit_reason = reason
            self.RecordExitFill(orderEvent, reason)
            self.CancelExitSiblings(order_id)
            return

        if self.correction_ticket is not None and order_id == self.correction_ticket.OrderId:
            return

    def HandleEntryFill(self, orderEvent):
        self.trade_fees += abs(float(orderEvent.OrderFee.Value.Amount))
        self.entry_fill_price = float(orderEvent.FillPrice)
        self.entry_fill_quantity = int(orderEvent.FillQuantity)
        shares = abs(self.entry_fill_quantity)
        if shares == 0 or self.planned_stop is None:
            self.rejected_entry_count += 1
            self.Liquidate(self.symbol, tag="FLATTEN_INVALID_ENTRY_CONTEXT")
            self.ClearOrderState()
            return

        direction = 1 if self.entry_fill_quantity > 0 else -1
        valid_stop_side = (
            self.entry_fill_price > self.planned_stop
            if direction > 0
            else self.entry_fill_price < self.planned_stop
        )
        risk_per_share = abs(self.entry_fill_price - self.planned_stop)
        if not valid_stop_side or risk_per_share <= 0:
            self.rejected_entry_count += 1
            self.Liquidate(self.symbol, tag="FLATTEN_INVALID_FILLED_R")
            self.ClearOrderState()
            return

        # Quantity must be chosen before LEAN reveals the next minute's open.
        # Record the uncommon case where that opening-price gap means the
        # pre-submission estimate exceeds either paper sizing constraint.
        equity = float(self.Portfolio.TotalPortfolioValue)
        max_risk_shares = floor(equity * self.RISK_FRACTION / risk_per_share)
        max_exposure_shares = floor(
            equity * self.EXPOSURE_CAP / self.entry_fill_price
        )
        if shares > min(max_risk_shares, max_exposure_shares):
            self.entry_sizing_drift_count += 1

        self.initial_risk_dollars = shares * risk_per_share
        target = round(
            self.entry_fill_price + direction * self.TARGET_R * risk_per_share,
            2,
        )
        exit_quantity = -self.entry_fill_quantity

        self.stop_ticket = self.StopMarketOrder(
            self.symbol,
            exit_quantity,
            self.planned_stop,
            tag="EXIT_STOP",
        )
        self.target_ticket = self.LimitOrder(
            self.symbol,
            exit_quantity,
            target,
            tag="EXIT_TARGET_10R",
        )

        self.trade_count += 1
        if direction > 0:
            self.long_count += 1
        else:
            self.short_count += 1

    def SubmitMarketOnCloseExit(self):
        if (not self.Portfolio[self.symbol].Invested or self.exit_pending or
                self.entry_fill_price is None or self.close_ticket is not None):
            return
        quantity = int(self.Portfolio[self.symbol].Quantity)
        self.close_ticket = self.MarketOnCloseOrder(
            self.symbol, -quantity, tag="EXIT_EOD"
        )

    def PrepareMarketOnCloseFill(self):
        """Make the pending MOC the sole live exit during the closing auction."""
        if self.close_ticket is None:
            return
        if self.close_ticket.Status in (
            OrderStatus.Filled, OrderStatus.Canceled, OrderStatus.Invalid
        ):
            return
        for ticket in (self.stop_ticket, self.target_ticket):
            if ticket is not None and ticket.Status not in (
                OrderStatus.Filled, OrderStatus.Canceled, OrderStatus.Invalid
            ):
                ticket.Cancel("Bracket canceled before MOC closing-auction fill")

    def ExitReasonFor(self, order_id, tag=""):
        if tag == "EXIT_STOP":
            return "STOP"
        if tag == "EXIT_TARGET_10R":
            return "TARGET_10R"
        if tag == "EXIT_EOD":
            return "EOD"
        if self.stop_ticket is not None and order_id == self.stop_ticket.OrderId:
            return "STOP"
        if self.target_ticket is not None and order_id == self.target_ticket.OrderId:
            return "TARGET_10R"
        if self.close_ticket is not None and order_id == self.close_ticket.OrderId:
            return "EOD"
        return None

    def CancelExitSiblings(self, filled_order_id):
        for ticket in (self.stop_ticket, self.target_ticket, self.close_ticket):
            if ticket is not None and ticket.OrderId != filled_order_id:
                ticket.Cancel(f"Sibling canceled after {self.exit_reason}")

    def RecordExitFill(self, orderEvent, reason):
        exit_price = float(orderEvent.FillPrice)
        gross_pnl = (
            (exit_price - self.entry_fill_price) * self.entry_fill_quantity
        )
        net_pnl = gross_pnl - self.trade_fees
        realized_r = (
            net_pnl / self.initial_risk_dollars
            if self.initial_risk_dollars > 0 else 0.0
        )
        self.realized_r_values.append(realized_r)
        if net_pnl > 0:
            self.win_count += 1
        if reason == "STOP":
            self.stop_count += 1
        elif reason == "TARGET_10R":
            self.target_count += 1
        else:
            self.eod_count += 1

    def FinalizeCompletedTrade(self):
        self.ClearOrderState()
        self.opening_bar = None

    def ClearOrderState(self):
        self.entry_ticket = None
        self.stop_ticket = None
        self.target_ticket = None
        self.close_ticket = None
        self.correction_ticket = None
        self.planned_stop = None
        self.entry_fill_price = None
        self.entry_fill_quantity = 0
        self.initial_risk_dollars = 0.0
        self.trade_fees = 0.0
        self.exit_pending = False
        self.exit_reason = None

    def OnEndOfAlgorithm(self):
        total = self.trade_count
        long_share = self.long_count / total if total else 0.0
        short_share = self.short_count / total if total else 0.0
        win_rate = self.win_count / total if total else 0.0
        average_r = (
            sum(self.realized_r_values) / len(self.realized_r_values)
            if self.realized_r_values else 0.0
        )
        summary = self.Statistics.Summary

        self.SetRuntimeStatistic("ORB Trades", str(total))
        self.SetRuntimeStatistic("ORB Win Rate", f"{win_rate:.2%}")
        self.SetRuntimeStatistic(
            "ORB Long / Short", f"{long_share:.1%} / {short_share:.1%}"
        )
        self.SetRuntimeStatistic("ORB Average R", f"{average_r:.3f}")
        self.SetRuntimeStatistic(
            "ORB Session Check",
            str(total + self.doji_count + self.rejected_entry_count),
        )

        self.Log(
            "ORB V1 FINAL | "
            f"trades={total} (paper~1795) | "
            f"long={self.long_count} ({long_share:.2%}, paper~51%) | "
            f"short={self.short_count} ({short_share:.2%}, paper~49%) | "
            f"wins={self.win_count} | win_rate={win_rate:.2%} (paper~24%) | "
            f"avg_R={average_r:.4f} (paper~0.13R)"
        )
        self.Log(
            "ORB V1 EXITS | "
            f"stop={self.stop_count} | target={self.target_count} | "
            f"eod={self.eod_count} | rejected_entries={self.rejected_entry_count} | "
            f"intended_entries={self.intended_entry_count} | "
            f"entry_sizing_drifts={self.entry_sizing_drift_count} | "
            f"double_fill_repairs={self.double_fill_count} | dojis={self.doji_count}"
        )
        self.Log(
            "ORB V1 RECONCILIATION | "
            f"trades_plus_dojis_plus_rejected="
            f"{total + self.doji_count + self.rejected_entry_count} | "
            f"non_doji_signals={self.intended_entry_count} | "
            f"open_position={self.Portfolio[self.symbol].Invested}"
        )
        self.Log(
            "ORB V1 PERFORMANCE | "
            f"net_profit={summary['Net Profit']} (paper~676%) | "
            f"cagr={summary['Compounding Annual Return']} (paper~33%) | "
            f"sharpe={summary['Sharpe Ratio']} (paper~1.13) | "
            f"drawdown={summary['Drawdown']} (paper~22%)"
        )
