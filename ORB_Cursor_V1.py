# region imports
from AlgorithmImports import *
# endregion
import math
from datetime import time, timedelta, date


class PerShareFeeModel(FeeModel):
    """Paper commission: $0.0005 per share on every fill (not per order)."""

    def __init__(self, fee_per_share: float) -> None:
        self._fee_per_share = fee_per_share

    def GetOrderFee(self, parameters: OrderFeeParameters) -> OrderFee:
        # LEAN passes the quantity for this specific fill event.
        fee = parameters.Order.AbsoluteQuantity * self._fee_per_share
        return OrderFee(CashAmount(fee, "USD"))


class QqqOpeningRangeBreakout(QCAlgorithm):
    """
    Five-minute Opening Range Breakout on QQQ — replication of Zarattini & Aziz (2023),
    Section 2 / Table 1 / QQQ results in Section 3 & Table 2.
    """

    STARTING_CASH = 25000.0
    BACKTEST_START = date(2016, 1, 1)
    BACKTEST_END = date(2023, 2, 17)

    def Initialize(self) -> None:
        self.SetStartDate(2016, 1, 1)
        self.SetEndDate(2023, 2, 17)
        self.SetCash(self.STARTING_CASH)
        self.SetTimeZone("America/New_York")

        self.SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)
        self.Portfolio.MarginCallModel = MarginCallModel.Null

        equity = self.AddEquity(
            "QQQ",
            Resolution.Minute,
            fillForward=False,
            extendedMarketHours=False,
        )
        self._symbol = equity.Symbol
        equity.SetDataNormalizationMode(DataNormalizationMode.Raw)
        equity.SetSlippageModel(ConstantSlippageModel(0))
        equity.SetFeeModel(PerShareFeeModel(0.0005))
        equity.SetLeverage(4.0)
        equity.SetBuyingPowerModel(BuyingPowerModel.Null)

        self._consolidator = TradeBarConsolidator(timedelta(minutes=5))
        self._consolidator.DataConsolidated += self._OnFiveMinuteBar
        self.SubscriptionManager.AddConsolidator(self._symbol, self._consolidator)

        self._or_bar = None
        self._session_date = None
        self._day_phase = "idle"  # idle | entry_pending | in_trade | closed

        self._is_long = None
        self._stop_price = None

        self._entry_ticket = None
        self._stop_ticket = None
        self._target_ticket = None
        self._moc_ticket = None
        self._exit_order_ids = set()

        self._active_trade = None
        self._finalizing_exit = False
        self._suppress_cancel_events = False
        self._entry_commission_acc = 0.0

        self._sample_log_budget = 5
        self._equity_by_date = {self.BACKTEST_START: self.STARTING_CASH}

        self._stats = {
            "completed_trades": 0,
            "long_trades": 0,
            "short_trades": 0,
            "winning_trades": 0,
            "stop_exits": 0,
            "target_exits": 0,
            "eod_exits": 0,
            "doji_skips": 0,
            "rejected_or_expired_entries": 0,
            "double_fill_corrections": 0,
            "realized_r_sum": 0.0,
            "total_commissions": 0.0,
        }

        self.Schedule.On(
            self.DateRules.EveryDay(self._symbol),
            self.TimeRules.At(9, 29),
            self._PrepareNewSession,
        )
        # Entry is valid only for the 9:35 execution window (one minute).
        self.Schedule.On(
            self.DateRules.EveryDay(self._symbol),
            self.TimeRules.At(9, 36),
            self._ExpireEntryIfStillOpen,
        )
        # Submit MOC near the exchange cutoff instead of immediately after entry.
        self.Schedule.On(
            self.DateRules.EveryDay(self._symbol),
            self.TimeRules.BeforeMarketClose(self._symbol, 16),
            self._SubmitMocIfNeeded,
        )
        # Safety net: cancel any live brackets and flatten before the session ends.
        self.Schedule.On(
            self.DateRules.EveryDay(self._symbol),
            self.TimeRules.BeforeMarketClose(self._symbol, 1),
            self._SessionEndSafetyCheck,
        )

    # ------------------------------------------------------------------ session
    def _PrepareNewSession(self) -> None:
        if self.Portfolio[self._symbol].Invested:
            self._FlattenAnomaly("Overnight position detected at session prep")
        self._ResetDayState()

    def _ResetDayState(self) -> None:
        self._or_bar = None
        self._session_date = self.Time.date()
        self._day_phase = "idle"
        self._is_long = None
        self._stop_price = None
        self._entry_ticket = None
        self._stop_ticket = None
        self._target_ticket = None
        self._moc_ticket = None
        self._exit_order_ids = set()
        self._active_trade = None
        self._finalizing_exit = False
        self._suppress_cancel_events = False
        self._entry_commission_acc = 0.0

    # ---------------------------------------------------------------- consolidator
    def _OnFiveMinuteBar(self, sender, bar: TradeBar) -> None:
        # Opening range is the first regular-session five-minute bar [9:30, 9:35).
        if bar.Time.time() != time(9, 30):
            return
        if self._session_date != self.Time.date():
            self._ResetDayState()
            self._session_date = self.Time.date()

        self._or_bar = bar

        if bar.Open == bar.Close:
            self._stats["doji_skips"] += 1
            self._day_phase = "closed"
            return

        if bar.Close > bar.Open:
            self._is_long = True
            self._stop_price = bar.Low
        else:
            self._is_long = False
            self._stop_price = bar.High

        # OR is complete at EndTime 9:35 with no future bar data. Enter immediately
        # with a zero-slippage market order that fills at the 9:35 open (next minute bar).
        self._SubmitEntryAt935()

    def _SubmitEntryAt935(self) -> None:
        if self._or_bar is None or self._day_phase not in ("idle",):
            return

        # Last trade at the OR close is the best pre-fill estimate of the 9:35 open
        # available without seeing the future [9:35, 9:36) bar.
        entry_estimate = round(self.Securities[self._symbol].Price, 2)
        stop = round(self._stop_price, 2)

        if self._is_long:
            if entry_estimate <= stop:
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
                return
            risk_per_share = entry_estimate - stop
            signed_qty = 1
        else:
            if entry_estimate >= stop:
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
                return
            risk_per_share = stop - entry_estimate
            signed_qty = -1

        if risk_per_share <= 0:
            self._stats["rejected_or_expired_entries"] += 1
            self._day_phase = "closed"
            return

        equity = self.Portfolio.TotalPortfolioValue
        risk_shares = (equity * 0.01) / risk_per_share
        leverage_shares = (4.0 * equity) / entry_estimate
        quantity = math.floor(min(risk_shares, leverage_shares))

        if quantity <= 0:
            self._stats["rejected_or_expired_entries"] += 1
            self._day_phase = "closed"
            return

        signed_qty *= quantity
        self._entry_ticket = self.MarketOrder(
            self._symbol,
            signed_qty,
            tag="ORB Entry 9:35 Open",
        )
        self._day_phase = "entry_pending"

        if self._sample_log_budget > 0:
            self._sample_log_budget -= 1
            direction = "LONG" if self._is_long else "SHORT"
            self.Log(
                f"SAMPLE {self.Time.date()} {direction} market_entry_est={entry_estimate} "
                f"stop={stop} qty={abs(signed_qty)} equity={equity:.2f}"
            )

    def _ExpireEntryIfStillOpen(self) -> None:
        if self._day_phase != "entry_pending" or self._entry_ticket is None:
            return

        status = self._entry_ticket.Status
        if status == OrderStatus.Filled:
            return

        if status == OrderStatus.PartiallyFilled:
            fill_price = round(self._entry_ticket.AverageFillPrice, 2)
            fill_qty = int(abs(self._entry_ticket.QuantityFilled))
            self._entry_ticket.Cancel("Entry window expired 9:36")
            if fill_qty > 0:
                self._CreateBracketsFromFill(
                    fill_price, fill_qty, self._is_long, self._entry_commission_acc
                )
            else:
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
            return

        if status in (OrderStatus.Submitted, OrderStatus.UpdateSubmitted, OrderStatus.New):
            self._stats["rejected_or_expired_entries"] += 1
            self._entry_ticket.Cancel("Entry window expired 9:36")
            self._day_phase = "closed"

    def _SubmitMocIfNeeded(self) -> None:
        if self._day_phase != "in_trade":
            return
        if not self.Portfolio[self._symbol].Invested:
            return
        if self._moc_ticket is not None:
            status = self._moc_ticket.Status
            if status not in (OrderStatus.Canceled, OrderStatus.Invalid):
                return

        holding_qty = self.Portfolio[self._symbol].Quantity
        if holding_qty == 0:
            return

        self._moc_ticket = self.MarketOnCloseOrder(
            self._symbol,
            -holding_qty,
            tag="EOD MOC",
        )
        self._exit_order_ids.add(self._moc_ticket.OrderId)

    def _SessionEndSafetyCheck(self) -> None:
        if self._day_phase == "in_trade" and self.Portfolio[self._symbol].Invested:
            if self._moc_ticket is None or self._moc_ticket.Status in (
                OrderStatus.Canceled,
                OrderStatus.Invalid,
            ):
                self._SubmitMocIfNeeded()

        if self._day_phase in ("closed", "idle"):
            self._CancelAllExitTickets()

        if self.Portfolio[self._symbol].Invested and self._day_phase != "in_trade":
            self._FlattenAnomaly("pre-close residual position")

    # ---------------------------------------------------------------- OnOrderEvent
    def OnOrderEvent(self, order_event: OrderEvent) -> None:
        if order_event.Status == OrderStatus.Filled:
            self._stats["total_commissions"] += self._FillFee(order_event)

        if self._suppress_cancel_events and order_event.Status in (
            OrderStatus.Canceled,
            OrderStatus.Invalid,
        ):
            return

        if order_event.Status in (OrderStatus.Canceled, OrderStatus.Invalid):
            if (
                self._entry_ticket is not None
                and order_event.OrderId == self._entry_ticket.OrderId
                and self._day_phase == "entry_pending"
            ):
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
            self._entry_ticket = None
            return

        if order_event.Status != OrderStatus.Filled:
            return

        order_id = order_event.OrderId

        if self._entry_ticket is not None and order_id == self._entry_ticket.OrderId:
            self._entry_commission_acc += self._FillFee(order_event)
            if self._entry_ticket.Status == OrderStatus.Filled:
                self._CreateBracketsFromFill(
                    round(self._entry_ticket.AverageFillPrice, 2),
                    int(abs(self._entry_ticket.QuantityFilled)),
                    self._is_long,
                    self._entry_commission_acc,
                )
            return

        if order_id in self._exit_order_ids:
            if self._active_trade is not None and not self._finalizing_exit:
                if self._stop_ticket is not None and order_id == self._stop_ticket.OrderId:
                    self._FinalizeTrade("stop", order_event)
                elif self._target_ticket is not None and order_id == self._target_ticket.OrderId:
                    self._FinalizeTrade("target", order_event)
                elif self._moc_ticket is not None and order_id == self._moc_ticket.OrderId:
                    self._FinalizeTrade("eod", order_event)
            else:
                self._HandleOrphanExitFill(order_event)

    @staticmethod
    def _FillFee(order_event: OrderEvent) -> float:
        order_fee = order_event.OrderFee
        if order_fee is not None and order_fee.Value.Amount > 0:
            return float(order_fee.Value.Amount)
        return abs(order_event.FillQuantity) * 0.0005

    def _CreateBracketsFromFill(
        self,
        fill_price: float,
        fill_qty: int,
        is_long: bool,
        entry_commission: float = 0.0,
    ) -> None:
        if self._day_phase == "in_trade":
            return

        stop = round(self._stop_price, 2)

        if is_long:
            if fill_price <= stop:
                self.MarketOrder(self._symbol, -fill_qty, tag="Invalid long setup flatten")
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
                return
            r_distance = fill_price - stop
            target_price = round(fill_price + 10.0 * r_distance, 2)
            stop_qty = -fill_qty
            target_qty = -fill_qty
        else:
            if fill_price >= stop:
                self.MarketOrder(self._symbol, fill_qty, tag="Invalid short setup flatten")
                self._stats["rejected_or_expired_entries"] += 1
                self._day_phase = "closed"
                return
            r_distance = stop - fill_price
            target_price = round(fill_price - 10.0 * r_distance, 2)
            stop_qty = fill_qty
            target_qty = fill_qty

        self._active_trade = {
            "is_long": is_long,
            "entry_price": fill_price,
            "stop_price": stop,
            "quantity": fill_qty,
            "r_distance": r_distance,
            "dollar_risk": r_distance * fill_qty,
            "entry_commission": entry_commission,
            "exit_commission": 0.0,
        }
        self._day_phase = "in_trade"

        self._stop_ticket = self.StopMarketOrder(
            self._symbol, stop_qty, stop, tag="Stop"
        )
        self._target_ticket = self.LimitOrder(
            self._symbol, target_qty, target_price, tag="Target 10R"
        )
        self._exit_order_ids.update(
            {self._stop_ticket.OrderId, self._target_ticket.OrderId}
        )

    def _FinalizeTrade(self, exit_type: str, order_event: OrderEvent) -> None:
        if self._active_trade is None or self._finalizing_exit:
            return

        self._finalizing_exit = True
        self._suppress_cancel_events = True
        try:
            self._CancelSiblingExits(order_event.OrderId)

            trade = self._active_trade
            self._active_trade = None
            self._day_phase = "closed"

            trade["exit_commission"] += self._FillFee(order_event)

            qty = trade["quantity"]
            exit_price = order_event.FillPrice
            if trade["is_long"]:
                gross_pnl = (exit_price - trade["entry_price"]) * qty
            else:
                gross_pnl = (trade["entry_price"] - exit_price) * qty

            entry_commission = trade["entry_commission"]
            if entry_commission <= 0:
                entry_commission = qty * 0.0005
            total_commission = entry_commission + trade["exit_commission"]
            net_pnl = gross_pnl - total_commission
            realized_r = (
                net_pnl / trade["dollar_risk"] if trade["dollar_risk"] > 0 else 0.0
            )

            self._stats["completed_trades"] += 1
            if trade["is_long"]:
                self._stats["long_trades"] += 1
            else:
                self._stats["short_trades"] += 1
            if net_pnl > 0:
                self._stats["winning_trades"] += 1
            if exit_type == "stop":
                self._stats["stop_exits"] += 1
            elif exit_type == "target":
                self._stats["target_exits"] += 1
            else:
                self._stats["eod_exits"] += 1
            self._stats["realized_r_sum"] += realized_r

            if self._sample_log_budget > 0:
                self._sample_log_budget -= 1
                self.Log(
                    f"SAMPLE EXIT {self.Time} type={exit_type} net_pnl={net_pnl:.2f} "
                    f"realized_r={realized_r:.4f}"
                )
        finally:
            self._suppress_cancel_events = False
            self._finalizing_exit = False

        self._EnsureFlat(f"post-{exit_type}-exit")

    def _HandleOrphanExitFill(self, order_event: OrderEvent) -> None:
        self._stats["double_fill_corrections"] += 1
        self.Log(
            f"ANOMALY {self.Time} orphan exit fill id={order_event.OrderId} "
            f"qty={order_event.FillQuantity}"
        )
        self._CancelAllExitTickets()
        self._EnsureFlat("orphan exit fill")

    def _CancelSiblingExits(self, filled_order_id: int) -> None:
        for ticket in (self._stop_ticket, self._target_ticket, self._moc_ticket):
            if ticket is None or ticket.OrderId == filled_order_id:
                continue
            if ticket.Status not in (OrderStatus.Filled, OrderStatus.Canceled):
                ticket.Cancel("Sibling exit filled")

    def _CancelAllExitTickets(self) -> None:
        self._suppress_cancel_events = True
        try:
            for ticket in (self._stop_ticket, self._target_ticket, self._moc_ticket):
                if ticket is None:
                    continue
                if ticket.Status not in (OrderStatus.Filled, OrderStatus.Canceled):
                    ticket.Cancel("Exit cleanup")
        finally:
            self._suppress_cancel_events = False

    def _EnsureFlat(self, context: str) -> None:
        holding = self.Portfolio[self._symbol]
        if holding.Invested:
            self._stats["double_fill_corrections"] += 1
            self.Log(
                f"ANOMALY {self.Time} {context}: residual qty={holding.Quantity} — flattening"
            )
            self._FlattenAnomaly(context)
        else:
            self._CancelAllExitTickets()

    def _FlattenAnomaly(self, tag: str) -> None:
        self._CancelAllExitTickets()
        qty = self.Portfolio[self._symbol].Quantity
        if qty != 0:
            self.MarketOrder(self._symbol, -qty, tag=f"Flatten:{tag}")

    # ---------------------------------------------------------------- end of day
    def OnEndOfDay(self, symbol: Symbol) -> None:
        if symbol != self._symbol:
            return
        self._equity_by_date[self.Time.date()] = self.Portfolio.TotalPortfolioValue

        if self.Portfolio[self._symbol].Invested and self._day_phase != "in_trade":
            self._FlattenAnomaly("unexpected position at EOD")

    def OnEndOfAlgorithm(self) -> None:
        self._ReportReconciliation()

    @staticmethod
    def _SummaryValue(summary, key: str, default: str = "n/a") -> str:
        if summary is None:
            return default
        if hasattr(summary, "ContainsKey") and summary.ContainsKey(key):
            return str(summary[key])
        return default

    def _ReportReconciliation(self) -> None:
        starting_equity = self.STARTING_CASH
        final_equity = self.Portfolio.TotalPortfolioValue
        total_return = (final_equity / starting_equity - 1.0) * 100.0

        sorted_dates = sorted(self._equity_by_date.keys())
        equity_series = [self._equity_by_date[d] for d in sorted_dates]

        cagr = 0.0
        arithmetic_annual_return = 0.0
        sharpe = 0.0
        max_drawdown = 0.0

        calendar_days = (self.BACKTEST_END - self.BACKTEST_START).days
        if calendar_days > 0:
            cagr = (
                (final_equity / starting_equity) ** (365.25 / calendar_days) - 1.0
            ) * 100.0

        if len(equity_series) >= 1:
            daily_returns = []
            peak = starting_equity
            for i, cur in enumerate(equity_series):
                if cur > peak:
                    peak = cur
                if peak > 0:
                    dd = (peak - cur) / peak
                    if dd > max_drawdown:
                        max_drawdown = dd
                if i > 0:
                    prev = equity_series[i - 1]
                    if prev > 0:
                        daily_returns.append((cur / prev) - 1.0)

            if daily_returns:
                mean_r = sum(daily_returns) / len(daily_returns)
                arithmetic_annual_return = mean_r * 252.0 * 100.0
                if len(daily_returns) > 1:
                    variance = sum((r - mean_r) ** 2 for r in daily_returns) / (
                        len(daily_returns) - 1
                    )
                    std_r = math.sqrt(variance)
                    if std_r > 0:
                        sharpe = (mean_r / std_r) * math.sqrt(252.0)

        stats = self._stats
        n = stats["completed_trades"]
        win_rate = (stats["winning_trades"] / n * 100.0) if n else 0.0
        long_pct = (stats["long_trades"] / n * 100.0) if n else 0.0
        short_pct = (stats["short_trades"] / n * 100.0) if n else 0.0
        avg_r = (stats["realized_r_sum"] / n) if n else 0.0

        qc_stats = self.Statistics
        qc_summary = qc_stats.Summary if hasattr(qc_stats, "Summary") else None
        lean_fees = 0.0
        try:
            if hasattr(qc_stats, "TotalPerformance"):
                lean_fees = float(
                    qc_stats.TotalPerformance.TradeStatistics.TotalFees
                )
        except Exception:
            lean_fees = 0.0

        self.Log("=" * 72)
        self.Log("QQQ 5-MIN ORB — PAPER RECONCILIATION (Zarattini & Aziz 2023)")
        self.Log("=" * 72)
        self.Log(f"Completed trade count          : {n}")
        self.Log(f"Long count / pct               : {stats['long_trades']} / {long_pct:.2f}%")
        self.Log(f"Short count / pct              : {stats['short_trades']} / {short_pct:.2f}%")
        self.Log(f"Winning trades / win rate      : {stats['winning_trades']} / {win_rate:.2f}%")
        self.Log(f"Stop exit count                : {stats['stop_exits']}")
        self.Log(f"10R target exit count          : {stats['target_exits']}")
        self.Log(f"EOD (MOC) exit count           : {stats['eod_exits']}")
        self.Log(f"Doji skip count                : {stats['doji_skips']}")
        self.Log(f"Rejected / expired entry count : {stats['rejected_or_expired_entries']}")
        self.Log(f"Double-fill correction count   : {stats['double_fill_corrections']}")
        self.Log(f"Average realized R per trade   : {avg_r:.4f}")
        self.Log(f"Starting equity                : ${starting_equity:,.2f}")
        self.Log(f"Final equity                   : ${final_equity:,.2f}")
        self.Log(f"Total return                   : {total_return:.2f}%")
        self.Log(f"CAGR (compounding annual)      : {cagr:.2f}%")
        self.Log(
            f"Arithmetic annualized return   : {arithmetic_annual_return:.2f}% "
            f"(paper narrative ~31%)"
        )
        self.Log(f"Sharpe ratio (daily, ann.)     : {sharpe:.4f} (paper target ~1.12-1.13)")
        self.Log(f"Maximum drawdown               : {max_drawdown * 100:.2f}% (paper target ~22%)")
        self.Log(f"Total commissions (tracked)    : ${stats['total_commissions']:,.2f}")
        self.Log(f"Total commissions (LEAN fees)  : ${lean_fees:,.2f}")
        if qc_summary is not None:
            self.Log(
                f"LEAN Compounding Annual Return : "
                f"{self._SummaryValue(qc_summary, 'Compounding Annual Return')}"
            )
            self.Log(
                f"LEAN Sharpe Ratio              : "
                f"{self._SummaryValue(qc_summary, 'Sharpe Ratio')}"
            )
            self.Log(
                f"LEAN Drawdown                  : "
                f"{self._SummaryValue(qc_summary, 'Drawdown')}"
            )
        self.Log("-" * 72)
        self.Log("Paper comparison targets (not hardcoded):")
        self.Log("  ~1795 trades | ~51% long / ~49% short | ~24% win rate")
        self.Log("  ~0.13R avg P&L/trade | final equity ~$192,806 | total return ~675-676%")
        self.Log("  Table 2 yearly return ~33% (CAGR) | regression alpha ~33% annualized")
        self.Log("=" * 72)

        self.SetRuntimeStatistic("Completed Trades", str(n))
        self.SetRuntimeStatistic("Win Rate", f"{win_rate:.1f}%")
        self.SetRuntimeStatistic("Avg Realized R", f"{avg_r:.3f}")
        self.SetRuntimeStatistic("Total Return", f"{total_return:.1f}%")
