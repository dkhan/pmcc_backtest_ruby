"""QuantConnect/LEAN port of FinLab's aggressive US ETF rotation strategy.

Source rules:
https://finlab.finance/en/blog/us-etf-rotation-strategy

This is a local port, not an original QuantConnect file published by FinLab.
It uses the prior completed daily bar and trades just after the next month
opens, avoiding a same-close look-ahead assumption.
"""

from AlgorithmImports import *


class FinLabMonthlyLeveragedEtfRotation(QCAlgorithm):
    RISK_TICKERS = ("TQQQ", "TECL")
    DEFENSIVE_TICKERS = ("IEF", "GLD", "SHY")
    STOP_LOSS = 0.10

    def Initialize(self):
        self.SetStartDate(2016, 6, 1)
        self.SetEndDate(2026, 6, 12)
        self.SetCash(100_000)
        self.SetBenchmark("QQQ")

        tickers = ("QQQ",) + self.RISK_TICKERS + self.DEFENSIVE_TICKERS
        self.symbols = {}
        for ticker in tickers:
            security = self.AddEquity(
                ticker,
                Resolution.Daily,
                dataNormalizationMode=DataNormalizationMode.Adjusted,
            )
            security.SetFeeModel(ConstantFeeModel(0))
            self.symbols[ticker] = security.Symbol

        self.qqq = self.symbols["QQQ"]
        self.stop_ticket = None
        self.pending_entry_symbol = None

        # The first tradable session of each month uses data through the prior
        # completed session. This is the executable counterpart to FinLab's
        # monthly close-price backtest convention.
        self.Schedule.On(
            self.DateRules.MonthStart(self.qqq),
            self.TimeRules.AfterMarketOpen(self.qqq, 1),
            self.Rebalance,
        )

    def Rebalance(self):
        history = self.History(
            list(self.symbols.values()), 220, Resolution.Daily
        )
        if history.empty:
            return

        closes = {}
        for ticker, symbol in self.symbols.items():
            try:
                series = history.loc[symbol]["close"].dropna()
            except KeyError:
                self.Debug(f"Missing history for {ticker} on {self.Time:%Y-%m-%d}")
                return
            if len(series) < 201:
                return
            closes[ticker] = series

        qqq = closes["QQQ"]
        qqq_above_sma200 = qqq.iloc[-1] > qqq.iloc[-200:].mean()
        qqq_return126 = qqq.iloc[-1] / qqq.iloc[-127] - 1.0
        risk_on = qqq_above_sma200 and qqq_return126 > 0.0

        candidates = self.RISK_TICKERS if risk_on else self.DEFENSIVE_TICKERS
        scores = {ticker: self.MomentumScore(closes[ticker]) for ticker in candidates}
        target_ticker = max(scores, key=scores.get)
        target = self.symbols[target_ticker]

        self.CancelStop()

        current = next(
            (
                symbol
                for symbol in self.symbols.values()
                if self.Portfolio[symbol].Invested
            ),
            None,
        )

        if current == target:
            # FinLab resets its percentage stop with each monthly resample.
            self.PlaceStop(target, self.Portfolio[target].Quantity)
            return

        self.Liquidate(tag="Monthly rotation")
        self.pending_entry_symbol = target
        self.SetHoldings(target, 1.0, tag=f"Monthly rotation to {target_ticker}")

    @staticmethod
    def MomentumScore(close):
        """Match posted code: pct_change(63) minus pct_change(21)."""
        return_value_63 = close.iloc[-1] / close.iloc[-64] - 1.0
        return_value_21 = close.iloc[-1] / close.iloc[-22] - 1.0
        return return_value_63 - return_value_21

    def OnOrderEvent(self, order_event):
        if order_event.Status != OrderStatus.Filled:
            return

        if (
            self.pending_entry_symbol is not None
            and order_event.Symbol == self.pending_entry_symbol
            and order_event.FillQuantity > 0
        ):
            symbol = self.pending_entry_symbol
            self.pending_entry_symbol = None
            self.PlaceStop(symbol, self.Portfolio[symbol].Quantity)

        if self.stop_ticket is not None and order_event.OrderId == self.stop_ticket.OrderId:
            self.stop_ticket = None

    def PlaceStop(self, symbol, quantity):
        if quantity <= 0:
            return
        stop_price = self.Securities[symbol].Price * (1.0 - self.STOP_LOSS)
        self.stop_ticket = self.StopMarketOrder(
            symbol,
            -quantity,
            stop_price,
            tag="10% monthly-reset stop",
        )

    def CancelStop(self):
        if self.stop_ticket is None:
            return
        if self.stop_ticket.Status not in (OrderStatus.Filled, OrderStatus.Canceled):
            self.stop_ticket.Cancel("Monthly stop reset")
        self.stop_ticket = None

