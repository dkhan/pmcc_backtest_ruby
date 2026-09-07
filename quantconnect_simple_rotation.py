from AlgorithmImports import *

class SimpleRotation(QCAlgorithm):
    RISK_ASSETS = ("TQQQ", "TECL")
    SAFE_ASSETS = ("GLD",)
    COMPARISON_DAYS = 7
    STOP_LOSS = 0.105
    HISTORY_BARS = 220

    def Initialize(self):
        self.SetStartDate(2016, 6, 1)
        self.SetEndDate(2026, 6, 1)
        self.SetCash(100000)
        self.SetBenchmark("QQQ")

        tickers = ("QQQ",) + self.RISK_ASSETS + self.SAFE_ASSETS
        self.symbols = {}
        for ticker in tickers:
            security = self.AddEquity(
                ticker,
                Resolution.Daily,
                dataNormalizationMode=DataNormalizationMode.Adjusted,
            )
            security.SetFeeModel(ConstantFeeModel(0))
            security.SetLeverage(3)
            self.symbols[ticker] = security.Symbol

        self.qqq = self.symbols["QQQ"]
        self.stop_ticket = None
        self.pending_entry = None
        self.pending_stop_reset = None

        self.SetWarmUp(self.HISTORY_BARS, Resolution.Daily)
        self.Schedule.On(
            self.DateRules.MonthStart(self.qqq),
            self.TimeRules.AfterMarketOpen(self.qqq, 1),
            self.Rebalance,
        )

    def Rebalance(self):
        if self.IsWarmingUp:
            return

        symbols = list(self.symbols.values())
        if any(not self.Securities[symbol].HasData for symbol in symbols):
            return

        history = self.History(symbols, self.HISTORY_BARS, Resolution.Daily)
        if history.empty:
            return

        closes = {}
        for ticker, symbol in self.symbols.items():
            try:
                close = history.loc[symbol]["close"].dropna()
            except KeyError:
                return
            if len(close) < 201:
                return
            closes[ticker] = close

        target_ticker = self.SelectTarget(closes)
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
            self.pending_stop_reset = target
            return

        self.pending_entry = target
        self.SetHoldings(
            target,
            1.0,
            True,
            False,
            f"Rotate to {target_ticker}",
        )

    def SelectTarget(self, closes):
        qqq = closes["QQQ"]
        above_sma = qqq.iloc[-1] > qqq.iloc[-200:].mean()
        positive_return = qqq.iloc[-1] > qqq.iloc[-127]

        if not (above_sma and positive_return):
            return "GLD"

        returns = {
            ticker: closes[ticker].iloc[-1]
            / closes[ticker].iloc[-(self.COMPARISON_DAYS + 1)]
            - 1
            for ticker in self.RISK_ASSETS
        }
        return min(returns, key=returns.get)

    def OnOrderEvent(self, event):
        if event.Status != OrderStatus.Filled:
            return

        if (
            self.pending_entry is not None
            and event.Symbol == self.pending_entry
            and event.FillQuantity > 0
        ):
            symbol = self.pending_entry
            self.pending_entry = None
            self.PlaceStop(symbol)

        if self.stop_ticket is not None and event.OrderId == self.stop_ticket.OrderId:
            self.stop_ticket = None

    def OnEndOfDay(self, symbol):
        if symbol != self.pending_stop_reset:
            return
        self.pending_stop_reset = None
        if self.Portfolio[symbol].Quantity > 0:
            self.PlaceStop(symbol)

    def PlaceStop(self, symbol):
        quantity = self.Portfolio[symbol].Quantity
        if quantity <= 0:
            return
        stop_price = self.Securities[symbol].Price * (1 - self.STOP_LOSS)
        self.stop_ticket = self.StopMarketOrder(
            symbol,
            -quantity,
            stop_price,
            tag=f"{self.STOP_LOSS:.1%} stop",
        )

    def CancelStop(self):
        if self.stop_ticket is None:
            return
        if self.stop_ticket.Status not in (OrderStatus.Filled, OrderStatus.Canceled):
            self.stop_ticket.Cancel("Monthly reset")
        self.stop_ticket = None
