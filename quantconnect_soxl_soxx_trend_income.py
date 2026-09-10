"""QuantConnect/LEAN port of QuantStrategyLab's SOXL/SOXX trend-income core.

2012: CAGR: 50%, DD: 50%

Source profile:
https://github.com/QuantStrategyLab/UsEquityStrategies/blob/main/src/us_equity_strategies/strategies/soxl_soxx_trend_income.py

This standalone QCAlgorithm reproduces the repository's core-only lifecycle
backtest: the income sleeve and external market-regime plugin are disabled.
Those features depend on late-inception ETFs or external artifacts and cannot
be tested consistently from 2012 using only QuantConnect price history.

BOXX did not exist in 2012, so BIL is used as the defensive/cash substitute.
Signals use completed daily bars and orders are submitted after the following
market open, avoiding same-close look-ahead.
"""

from AlgorithmImports import *

import numpy as np


class SoxlSoxxTrendIncomeCore(QCAlgorithm):
    TREND_WINDOW = 140
    ENTRY_BUFFER = 0.08
    MID_BUFFER = 0.06
    EXIT_BUFFER = 0.02

    FULL_SOXL = 0.70
    MID_SOXL = 0.65
    ACTIVE_SOXX = 0.20
    DEFENSIVE_SOXX = 0.15
    CASH_RESERVE = 0.03

    RSI_WINDOW = 14
    RSI_CAP = 70.0
    BOLLINGER_WINDOW = 20
    BOLLINGER_STD = 2.0

    VOL_WINDOW = 10
    VOL_LOOKBACK = 252
    VOL_PERCENTILE = 0.95
    VOL_MIN_PERIODS = 126
    VOL_FLOOR = 0.50
    VOL_CAP = 0.75
    VOL_FALLBACK = 0.55

    HISTORY_BARS = TREND_WINDOW + VOL_LOOKBACK + VOL_WINDOW + 10
    REBALANCE_THRESHOLD = 0.01

    def Initialize(self):
        self.SetStartDate(2012, 1, 1)
        self.SetEndDate(2026, 9, 9)
        self.SetCash(100_000)
        self.SetBenchmark("SOXX")

        self.symbols = {}
        for ticker in ("SOXL", "SOXX", "BIL"):
            security = self.AddEquity(
                ticker,
                Resolution.Daily,
                dataNormalizationMode=DataNormalizationMode.Adjusted,
            )
            # The upstream replay charges 5 bps per traded notional.
            security.SetFeeModel(ConstantFeeModel(0))
            security.SetSlippageModel(ConstantSlippageModel(0.0005))
            self.symbols[ticker] = security.Symbol

        self.soxl = self.symbols["SOXL"]
        self.soxx = self.symbols["SOXX"]
        self.bil = self.symbols["BIL"]

        self.SetWarmUp(self.HISTORY_BARS, Resolution.Daily)
        self.Schedule.On(
            self.DateRules.EveryDay(self.soxx),
            self.TimeRules.AfterMarketOpen(self.soxx, 5),
            self.Rebalance,
        )

        self.last_tier = None
        self.last_vol_threshold = None

    def Rebalance(self):
        if self.IsWarmingUp:
            return

        history = self.History(self.soxx, self.HISTORY_BARS, Resolution.Daily)
        if history.empty:
            return

        try:
            close = history.loc[self.soxx]["close"].dropna().astype(float)
        except (KeyError, TypeError):
            close = history["close"].dropna().astype(float)

        if len(close) < self.TREND_WINDOW:
            return

        price = float(close.iloc[-1])
        trend_ma = float(close.iloc[-self.TREND_WINDOW:].mean())
        entry_line = trend_ma * (1.0 + self.ENTRY_BUFFER)
        mid_line = trend_ma * (1.0 + self.MID_BUFFER)
        exit_line = trend_ma * (1.0 - self.EXIT_BUFFER)

        # The upstream hysteresis treats the blend as active while SOXL is held.
        blend_active = self.Portfolio[self.soxl].Invested
        if price > entry_line:
            tier = "full"
        elif price > mid_line or (blend_active and price > exit_line):
            tier = "mid"
        else:
            tier = "defensive"

        # Wilder RSI, matching pandas ewm(alpha=1/14, adjust=False).
        rsi = self.WilderRsi(close.values, self.RSI_WINDOW)
        bb_sample = close.iloc[-self.BOLLINGER_WINDOW:].values
        bb_upper = float(np.mean(bb_sample) + self.BOLLINGER_STD * np.std(bb_sample, ddof=0))

        # RSI and Bollinger triggers stack: each trigger lowers one tier.
        overlay_count = 0
        if tier in ("full", "mid"):
            if rsi > self.RSI_CAP:
                overlay_count += 1
            if price > bb_upper:
                overlay_count += 1
        tier = self.DowngradeTier(tier, overlay_count)

        if tier == "full":
            soxl_weight, soxx_weight = self.FULL_SOXL, self.ACTIVE_SOXX
        elif tier == "mid":
            soxl_weight, soxx_weight = self.MID_SOXL, self.ACTIVE_SOXX
        else:
            soxl_weight, soxx_weight = 0.0, self.DEFENSIVE_SOXX

        realized_vol, vol_threshold = self.RealizedVolatilitySignal(close.values)
        vol_triggered = soxl_weight > 0 and realized_vol >= vol_threshold
        if vol_triggered:
            # Core-only upstream replay uses retention_mode='none': redirect the
            # entire levered sleeve to SOXX when the volatility gate fires.
            soxx_weight += soxl_weight
            soxl_weight = 0.0

        deployable = 1.0 - self.CASH_RESERVE
        targets = {
            self.soxl: deployable * soxl_weight,
            self.soxx: deployable * soxx_weight,
            self.bil: deployable * max(0.0, 1.0 - soxl_weight - soxx_weight),
        }

        if not self.NeedsRebalance(targets):
            return

        self.SetHoldings([PortfolioTarget(symbol, weight) for symbol, weight in targets.items()])

        if tier != self.last_tier or vol_triggered:
            self.Debug(
                f"{self.Time:%Y-%m-%d} tier={tier} SOXX={price:.2f} "
                f"MA140={trend_ma:.2f} RSI14={rsi:.1f} "
                f"vol10={realized_vol:.1%} threshold={vol_threshold:.1%} "
                f"vol_delever={vol_triggered} targets="
                f"SOXL {targets[self.soxl]:.1%}, SOXX {targets[self.soxx]:.1%}, "
                f"BIL {targets[self.bil]:.1%}"
            )
        self.last_tier = tier
        self.last_vol_threshold = vol_threshold

    @staticmethod
    def DowngradeTier(tier, steps):
        tiers = ("full", "mid", "defensive")
        return tiers[min(tiers.index(tier) + steps, len(tiers) - 1)]

    @staticmethod
    def WilderRsi(prices, period):
        deltas = np.diff(np.asarray(prices, dtype=float))
        if len(deltas) < period:
            return 50.0
        gains = np.maximum(deltas, 0.0)
        losses = np.maximum(-deltas, 0.0)
        avg_gain = float(np.mean(gains[:period]))
        avg_loss = float(np.mean(losses[:period]))
        for index in range(period, len(deltas)):
            avg_gain = ((period - 1) * avg_gain + gains[index]) / period
            avg_loss = ((period - 1) * avg_loss + losses[index]) / period
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 0.0
        relative_strength = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    def RealizedVolatilitySignal(self, prices):
        prices = np.asarray(prices, dtype=float)
        returns = prices[1:] / prices[:-1] - 1.0
        if len(returns) < self.VOL_WINDOW:
            return 0.0, self.VOL_FALLBACK

        rolling_vol = []
        for end in range(self.VOL_WINDOW, len(returns) + 1):
            sample = returns[end - self.VOL_WINDOW:end]
            rolling_vol.append(float(np.std(sample, ddof=1) * np.sqrt(252.0)))

        current = rolling_vol[-1]
        historical = rolling_vol[-self.VOL_LOOKBACK:]
        if len(historical) < self.VOL_MIN_PERIODS:
            return current, self.VOL_FALLBACK

        threshold = float(np.percentile(historical, self.VOL_PERCENTILE * 100.0))
        threshold = max(self.VOL_FLOOR, min(self.VOL_CAP, threshold))
        return current, threshold

    def NeedsRebalance(self, targets):
        total = float(self.Portfolio.TotalPortfolioValue)
        if total <= 0:
            return False
        for symbol, target in targets.items():
            current = float(self.Portfolio[symbol].HoldingsValue) / total
            if abs(current - target) >= self.REBALANCE_THRESHOLD:
                return True
        return False

