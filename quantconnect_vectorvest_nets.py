"""QuantConnect/LEAN approximation of the two systems in the VectorVest video.

Video: https://www.youtube.com/watch?v=IpZDhoQTvys

The video relies on proprietary VectorVest series (Confirmed Up/Down, GLB/RT
Kicker, and Relative Timing). Those series do not exist in QuantConnect. This
algorithm therefore uses documented, parameterized proxies:

* Confirmed Up/Down: QQQ above/below a configurable 50-session EMA.
* RT: RSI(14) / 50, preserving VectorVest's approximate 0..2 scale.
* GLB/RT Kicker: RT crossing above 1.0 while the trend is Confirmed Up.

Signals use completed daily bars and all trades execute at the next market
open. Set the QuantConnect project parameter ``strategy`` to ``qqq`` or
``turbo``. The default is ``turbo``. CAGR and maximum drawdown are printed at
the end of the backtest.
"""

from AlgorithmImports import *


class VectorVestNetsApproximation(QCAlgorithm):
    STARTING_CASH = 100_000

    def Initialize(self):
        self.strategy = (self.GetParameter("strategy") or "turbo").lower()
        if self.strategy not in ("qqq", "turbo"):
            raise ValueError("strategy must be 'qqq' or 'turbo'")

        # Match the periods displayed in the video. QuantConnect's UI may be
        # used to override these dates for robustness/out-of-sample tests.
        if self.strategy == "qqq":
            self.SetStartDate(2020, 2, 14)
            self.SetEndDate(2024, 2, 15)
        else:
            self.SetStartDate(2016, 2, 8)
            self.SetEndDate(2024, 2, 7)
        self.SetCash(self.STARTING_CASH)

        self.qqq = self.AddEquity(
            "QQQ", Resolution.Daily,
            dataNormalizationMode=DataNormalizationMode.Adjusted,
        ).Symbol
        self.qld = self.AddEquity(
            "QLD", Resolution.Daily,
            dataNormalizationMode=DataNormalizationMode.Adjusted,
        ).Symbol
        self.SetBenchmark(self.qqq)

        for symbol in (self.qqq, self.qld):
            self.Securities[symbol].SetFeeModel(ConstantFeeModel(0))
            # Holdings stay at 100%; this only gives LEAN enough temporary
            # buying power for paired next-open sell/buy tickets.
            self.Securities[symbol].SetLeverage(2)

        self.trend_period = self.ReadIntParameter("trend-period", 50, 5, 250)
        self.rsi_period = self.ReadIntParameter("rsi-period", 14, 2, 100)
        self.trend_average = self.EMA(
            self.qqq, self.trend_period, Resolution.Daily
        )
        self.rsi = self.RSI(
            self.qqq,
            self.rsi_period,
            MovingAverageType.Wilders,
            Resolution.Daily,
        )

        self.rt_window = RollingWindow[float](16)
        self.close_window = RollingWindow[float](3)
        self.pending_target = None
        self.qqq_entry_price = None
        self.qld_entry_price = None
        self.last_regime = None
        self.entry_count = 0

        self.peak_equity = self.STARTING_CASH
        self.max_drawdown = 0.0
        self.start_value = self.STARTING_CASH
        self.start_time = None

        self.SetWarmUp(max(self.trend_period, self.rsi_period + 15) + 10,
                       Resolution.Daily)

    def ReadIntParameter(self, name, default, minimum, maximum):
        text = self.GetParameter(name)
        value = default if not text else int(text)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value

    def OnData(self, data):
        if self.qqq not in data.Bars:
            return

        close = float(data.Bars[self.qqq].Close)
        self.close_window.Add(close)

        if self.rsi.IsReady:
            # VectorVest RT is approximately bounded by 0 and 2. RSI/50 gives
            # the same range, but is only a proxy for the proprietary series.
            self.rt_window.Add(float(self.rsi.Current.Value) / 50.0)

        self.TrackDrawdown()
        if self.IsWarmingUp or not self.IndicatorsReady():
            return

        if self.start_time is None:
            self.start_time = self.Time
            self.start_value = float(self.Portfolio.TotalPortfolioValue)

        if self.pending_target is not None:
            return

        if self.strategy == "qqq":
            self.RunQqqSystem()
        else:
            self.RunTurboSystem()

        self.last_regime = "up" if self.ConfirmedUp() else "down"

    def IndicatorsReady(self):
        return (
            self.trend_average.IsReady
            and self.rsi.IsReady
            and self.close_window.IsReady
            and self.rt_window.IsReady
        )

    def ConfirmedUp(self):
        return self.close_window[0] > float(self.trend_average.Current.Value)

    def ConfirmedDown(self):
        return self.close_window[0] < float(self.trend_average.Current.Value)

    def Rt(self, offset=0):
        return float(self.rt_window[offset])

    def RtAverage(self, period, offset=0):
        return sum(self.Rt(i + offset) for i in range(period)) / period

    def RunQqqSystem(self):
        """Public-data approximation of the video's QQQ ETF system."""
        invested = self.Portfolio[self.qqq].Invested
        rt_kicker = self.Rt(0) > 1.0 and self.Rt(1) <= 1.0

        if invested and self.qqq_entry_price:
            qqq_return = (
                float(self.Securities[self.qqq].Price) / self.qqq_entry_price - 1.0
            )
            if qqq_return >= 0.99:
                self.QueueNextOpen(None, "QQQ 99% profit exit")
                return
            if qqq_return <= -0.15:
                self.QueueNextOpen(None, "QQQ 15% loss exit")
                return

        if not invested and self.ConfirmedUp() and rt_kicker:
            self.QueueNextOpen(self.qqq, "QQQ GLB/RT-kicker proxy entry")
        elif invested and self.ConfirmedDown():
            self.QueueNextOpen(None, "QQQ Confirmed-Down proxy exit")

    def RunTurboSystem(self):
        """Approximate the NETS Turbo rules displayed at 12:24 in the video."""
        qqq_invested = self.Portfolio[self.qqq].Invested
        qld_invested = self.Portfolio[self.qld].Invested
        current_regime = "up" if self.ConfirmedUp() else "down"
        entered_up = current_regime == "up" and self.last_regime != "up"

        # QLD exits at the next open following a 30% gain, 5% loss, or C/Dn.
        if qld_invested:
            qld_close = float(self.Securities[self.qld].Price)
            if self.qld_entry_price:
                qld_return = qld_close / self.qld_entry_price - 1.0
                if qld_return >= 0.30:
                    self.QueueNextOpen(None, "QLD 30% profit exit")
                    return
                if qld_return <= -0.05:
                    self.QueueNextOpen(None, "QLD 5% loss exit")
                    return
            if self.ConfirmedDown():
                self.QueueNextOpen(None, "QLD Confirmed-Down proxy exit")
            return

        # On entering Confirmed Up, close QQQ and buy QLD when RT <= 1.40.
        # The published rule is <=, not >=. A stopped QLD position is not
        # automatically replaced until a new Confirmed-Up situation begins.
        if qqq_invested:
            if self.ConfirmedDown():
                return
            if self.Rt() <= 1.40:
                self.QueueNextOpen(self.qld, "Turbo QLD entry at RT proxy <= 1.40")
            else:
                self.QueueNextOpen(None, "Turbo QQQ exit on Confirmed Up")
            return

        if not qld_invested and entered_up and self.Rt() <= 1.40:
            self.QueueNextOpen(self.qld, "Turbo QLD entry at RT proxy <= 1.40")
            return

        # The slide requires a 10-day RT average to cross above the 15-day
        # average, RT <= 1.12, and a Confirmed-Down condition.
        crossed_up = (
            self.RtAverage(10, 1) <= self.RtAverage(15, 1)
            and self.RtAverage(10, 0) > self.RtAverage(15, 0)
        )
        if self.ConfirmedDown() and crossed_up and self.Rt() <= 1.12:
            self.QueueNextOpen(self.qqq, "Turbo QQQ RT-average crossover entry")

    def QueueNextOpen(self, target, reason):
        """Liquidate current holdings and establish target at the next open."""
        self.pending_target = target if target is not None else "cash"
        tag = reason[:100]
        for symbol in (self.qqq, self.qld):
            if self.Portfolio[symbol].Invested and symbol != target:
                self.MarketOnOpenOrder(
                    symbol,
                    -self.Portfolio[symbol].Quantity,
                    False,
                    tag,
                )

        if target is not None and not self.Portfolio[target].Invested:
            quantity = self.CalculateOrderQuantity(target, 1.0)
            if quantity:
                self.MarketOnOpenOrder(target, quantity, False, tag)

    def OnOrderEvent(self, order_event):
        if order_event.Status in (OrderStatus.Invalid, OrderStatus.Canceled):
            self.Error(
                f"Order {order_event.OrderId} {order_event.Status}: "
                f"{order_event.Message}"
            )
            if not self.Transactions.GetOpenOrders():
                self.pending_target = None
            return
        if order_event.Status != OrderStatus.Filled:
            return

        if order_event.Symbol == self.qld and order_event.FillQuantity > 0:
            self.qld_entry_price = float(order_event.FillPrice)
            self.entry_count += 1
        elif order_event.Symbol == self.qld and order_event.FillQuantity < 0:
            self.qld_entry_price = None
        if order_event.Symbol == self.qqq and order_event.FillQuantity > 0:
            self.qqq_entry_price = float(order_event.FillPrice)
            self.entry_count += 1
        elif order_event.Symbol == self.qqq and order_event.FillQuantity < 0:
            self.qqq_entry_price = None

        open_orders = self.Transactions.GetOpenOrders()
        if not open_orders:
            self.pending_target = None

    def TrackDrawdown(self):
        equity = float(self.Portfolio.TotalPortfolioValue)
        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity > 0:
            drawdown = 1.0 - equity / self.peak_equity
            self.max_drawdown = max(self.max_drawdown, drawdown)

    def OnEndOfAlgorithm(self):
        self.TrackDrawdown()
        end_value = float(self.Portfolio.TotalPortfolioValue)
        first = self.start_time or self.StartDate
        years = max((self.EndDate - first).days / 365.2425, 1.0 / 365.2425)
        cagr = (end_value / self.start_value) ** (1.0 / years) - 1.0

        self.Log("=" * 55)
        self.Log("VectorVest NETS approximation results")
        self.Log(f"Strategy: {self.strategy}")
        self.Log(f"Trend proxy:   QQQ EMA({self.trend_period})")
        self.Log(f"RT proxy:      RSI({self.rsi_period}) / 50")
        self.Log(f"Entries:       {self.entry_count}")
        self.Log(f"Initial value: ${self.start_value:,.2f}")
        self.Log(f"Final value:   ${end_value:,.2f}")
        self.Log(f"CAGR:          {cagr:.2%}")
        self.Log(f"Max drawdown:  {self.max_drawdown:.2%}")
        if self.strategy == "turbo":
            self.Log("Video reference: about 42.3% CAGR through 2024")
        self.Log("Signals are public-data proxies, not VectorVest indicators.")
        self.Log("=" * 55)
