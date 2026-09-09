import unittest
from dataclasses import replace

import pandas as pd

from local_conditional_sector_rotation import SYMBOLS, StrategyConfig, build_indicators, choose_target, stabilized_targets, wilder_rsi


class ConditionalSectorRotationTest(unittest.TestCase):
    def row(self, **overrides):
        values = dict(SPY_close=110, SPY_sma=100, QQQ_close=110, QQQ_sma=100, TQQQ_close=110, TQQQ_sma=100, QQQ_rsi=50, SPY_rsi=50, TQQQ_rsi=50, SQQQ_rsi=50, UVXY_rsi=50, TECS_rsi=50, BSV_rsi=40)
        values.update(overrides)
        return pd.Series(values)

    def test_bull_tree_selects_tqqq_and_uvxy(self):
        cfg = StrategyConfig()
        self.assertEqual(choose_target(self.row(), cfg), ("TQQQ", "bull_trend"))
        self.assertEqual(choose_target(self.row(QQQ_rsi=81.01), cfg), ("UVXY", "bull_overbought"))
        self.assertEqual(choose_target(self.row(QQQ_rsi=81.0), cfg), ("TQQQ", "bull_trend"))

    def test_bear_priority_matches_qc_tree(self):
        cfg = StrategyConfig()
        row = self.row(SPY_close=90, TQQQ_rsi=29, SPY_rsi=20, UVXY_rsi=80)
        self.assertEqual(choose_target(row, cfg), ("TECL", "bear_tqqq_oversold"))

    def test_uvxy_extreme_boundary_and_relative_tie(self):
        cfg = StrategyConfig()
        base = dict(SPY_close=90, TQQQ_close=90, QQQ_close=90, UVXY_rsi=84, TQQQ_rsi=50, SPY_rsi=50)
        self.assertEqual(choose_target(self.row(**base), cfg)[0], "UVXY")
        base["UVXY_rsi"] = 84.01
        base["TECS_rsi"] = base["BSV_rsi"] = 50
        self.assertEqual(choose_target(self.row(**base), cfg)[0], "TECS")

    def test_confirmation_is_temporal_hysteresis(self):
        signals = pd.DataFrame({"raw_target": ["TQQQ", "UVXY", "TQQQ", "UVXY", "UVXY"], "branch": ["x"] * 5}, index=pd.bdate_range("2024-01-01", periods=5))
        stable = stabilized_targets(signals, replace(StrategyConfig(), confirmation_days=2))
        self.assertEqual(list(stable.target), ["TQQQ", "TQQQ", "TQQQ", "TQQQ", "UVXY"])

    def test_wilder_rsi_extremes(self):
        up = wilder_rsi(pd.Series(range(30), dtype=float), 10)
        down = wilder_rsi(pd.Series(range(30, 0, -1), dtype=float), 10)
        self.assertEqual(up.iloc[-1], 100.0)
        self.assertEqual(down.iloc[-1], 0.0)

    def test_long_sma_uses_symbol_history_before_calendar_alignment(self):
        old = pd.bdate_range("2020-01-01", periods=220)
        young = old[-20:]
        prices = {}
        for symbol in SYMBOLS:
            index = young if symbol == "UVXY" else old
            close = pd.Series(range(1, len(index) + 1), index=index, dtype=float)
            prices[symbol] = pd.DataFrame({"open": close, "high": close, "low": close, "close": close})
        indicators = build_indicators(prices, StrategyConfig())
        self.assertEqual(indicators.index[0], young[0])
        self.assertFalse(pd.isna(indicators.iloc[0].SPY_sma))


if __name__ == "__main__":
    unittest.main()
