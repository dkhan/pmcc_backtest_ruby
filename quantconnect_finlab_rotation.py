"""QuantConnect/LEAN port of FinLab's aggressive US ETF rotation strategy.

Source rules:
https://finlab.finance/en/blog/us-etf-rotation-strategy

This is a local port, not an original QuantConnect file published by FinLab.
It reproduces the paper's zero-fee, monthly close-execution convention as
closely as LEAN permits while using only completed prior-session signals.
"""

import hashlib

from AlgorithmImports import *


class FinLabMonthlyLeveragedEtfRotation(QCAlgorithm):
    RISK_TICKERS = ("TQQQ", "TECL")
    DEFENSIVE_TICKERS = ("GLD",)
    RISK_SELECTION = "laggard"
    VALID_RISK_SELECTIONS = (
        "strongest",
        "weakest",
        "alternate",
        "random",
        "laggard",
    )
    LAGGARD_DAYS = 7
    STOP_LOSS = 0.105
    HISTORY_BARS = 220

    def Initialize(self):
        self.SetStartDate(2011, 9, 5)
        self.SetEndDate(2026, 9, 5)
        self.SetCash(100_000)
        self.SetBenchmark("QQQ")

        # Set `risk-selection` in the QuantConnect Parameters panel. The
        # default preserves the original aggressive rotation strategy.
        self.risk_selection = (
            self.GetParameter("risk-selection") or self.RISK_SELECTION
        ).strip().lower()
        if self.risk_selection not in self.VALID_RISK_SELECTIONS:
            raise ValueError(
                "risk-selection must be one of: "
                + ", ".join(self.VALID_RISK_SELECTIONS)
            )
        random_seed_text = self.GetParameter("random-seed") or "0"
        try:
            self.random_seed = int(random_seed_text)
        except ValueError as exc:
            raise ValueError("random-seed must be an integer") from exc
        laggard_days_text = (
            self.GetParameter("laggard-days") or str(self.LAGGARD_DAYS)
        )
        try:
            self.laggard_days = int(laggard_days_text)
        except ValueError as exc:
            raise ValueError("laggard-days must be an integer") from exc
        if not 1 <= self.laggard_days < self.HISTORY_BARS:
            raise ValueError(
                f"laggard-days must be between 1 and {self.HISTORY_BARS - 1}"
            )

        tickers = ("QQQ",) + self.RISK_TICKERS + self.DEFENSIVE_TICKERS
        self.symbols = {}
        for ticker in tickers:
            security = self.AddEquity(
                ticker,
                Resolution.Daily,
                dataNormalizationMode=DataNormalizationMode.Adjusted,
            )
            security.SetFeeModel(ConstantFeeModel(0))
            # Holdings remain capped at 100%. The higher brokerage leverage is
            # used only so LEAN can accept simultaneous MOC sell/buy tickets;
            # both fill at the same close and never create 3x account exposure.
            security.SetLeverage(3)
            self.symbols[ticker] = security.Symbol

        self.qqq = self.symbols["QQQ"]
        self.stop_ticket = None
        self.pending_entry_symbol = None
        self.pending_stop_reset_symbol = None
        self.invalid_order_count = 0

        # Populate Security.Price and the full signal lookback before the first
        # scheduled rebalance. This fixes the missing June 2016 entry.
        self.SetWarmUp(self.HISTORY_BARS, Resolution.Daily)

        # The first tradable session of each month uses data through the prior
        # completed session. This is the executable counterpart to FinLab's
        # monthly close-price backtest convention.
        self.Schedule.On(
            self.DateRules.MonthStart(self.qqq),
            self.TimeRules.AfterMarketOpen(self.qqq, 1),
            self.Rebalance,
        )

    def Rebalance(self):
        if self.IsWarmingUp:
            return
        if any(not self.Securities[symbol].HasData for symbol in self.symbols.values()):
            self.Error(f"Skipped rebalance on {self.Time:%Y-%m-%d}: price not ready")
            return

        history = self.History(
            list(self.symbols.values()), self.HISTORY_BARS, Resolution.Daily
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

        if risk_on:
            target_ticker = self.SelectRiskAsset(closes)
        else:
            scores = {
                ticker: self.MomentumScore(closes[ticker])
                for ticker in self.DEFENSIVE_TICKERS
            }
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
            # Wait for this session's close instead of using the stale prior
            # close visible at 09:31 with daily-resolution subscriptions.
            self.pending_stop_reset_symbol = target
            return

        self.pending_entry_symbol = target
        # liquidateExistingHoldings=True submits the old reduction and new
        # target together. Daily-resolution intraday market orders become MOC,
        # matching the paper's trade_at_price="close" convention.
        self.SetHoldings(
            target,
            1.0,
            True,
            False,
            f"Monthly rotation to {target_ticker}",
        )

    def SelectRiskAsset(self, closes):
        """Select TQQQ/TECL using the requested QuantConnect parameter."""
        if self.risk_selection == "alternate":
            signal_date = closes[self.RISK_TICKERS[0]].index[-1]
            return self.RISK_TICKERS[(signal_date.month - 1) % len(self.RISK_TICKERS)]

        if self.risk_selection == "random":
            signal_date = closes[self.RISK_TICKERS[0]].index[-1]
            key = f"{self.random_seed}:{signal_date.date().isoformat()}".encode()
            choice = int.from_bytes(hashlib.sha256(key).digest()[:8], "big")
            return self.RISK_TICKERS[choice % len(self.RISK_TICKERS)]

        if self.risk_selection == "laggard":
            returns = {
                ticker: closes[ticker].iloc[-1]
                / closes[ticker].iloc[-(self.laggard_days + 1)]
                - 1.0
                for ticker in self.RISK_TICKERS
            }
            return min(returns, key=returns.get)

        scores = {
            ticker: self.MomentumScore(closes[ticker])
            for ticker in self.RISK_TICKERS
        }
        if self.risk_selection == "weakest":
            return min(scores, key=scores.get)
        return max(scores, key=scores.get)

    @staticmethod
    def MomentumScore(close):
        """Match posted code: pct_change(63) minus pct_change(21)."""
        return_value_63 = close.iloc[-1] / close.iloc[-64] - 1.0
        return_value_21 = close.iloc[-1] / close.iloc[-22] - 1.0
        return return_value_63 - return_value_21

    def OnOrderEvent(self, order_event):
        if order_event.Status == OrderStatus.Invalid:
            self.invalid_order_count += 1
            self.Error(
                f"INVALID ORDER {order_event.OrderId} on {self.Time:%Y-%m-%d}: "
                f"{order_event.Message}"
            )
            if order_event.Symbol == self.pending_entry_symbol:
                self.pending_entry_symbol = None
            return

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

    def OnEndOfDay(self, symbol):
        if symbol != self.pending_stop_reset_symbol:
            return
        self.pending_stop_reset_symbol = None
        quantity = self.Portfolio[symbol].Quantity
        if quantity > 0:
            self.PlaceStop(symbol, quantity)

    def OnEndOfAlgorithm(self):
        self.Log(f"Risk selection: {self.risk_selection}")
        if self.risk_selection == "random":
            self.Log(f"Random seed: {self.random_seed}")
        if self.risk_selection == "laggard":
            self.Log(f"Laggard days: {self.laggard_days}")
        self.Log(f"Invalid order count: {self.invalid_order_count}")
        if self.invalid_order_count:
            self.Error("Backtest is invalid for comparison: one or more orders failed")

    def PlaceStop(self, symbol, quantity):
        if quantity <= 0:
            return
        stop_price = self.Securities[symbol].Price * (1.0 - self.STOP_LOSS)
        self.stop_ticket = self.StopMarketOrder(
            symbol,
            -quantity,
            stop_price,
            tag=f"{self.STOP_LOSS:.1%} monthly-reset stop",
        )

    def CancelStop(self):
        if self.stop_ticket is None:
            return
        if self.stop_ticket.Status not in (OrderStatus.Filled, OrderStatus.Canceled):
            self.stop_ticket.Cancel("Monthly stop reset")
        self.stop_ticket = None
