import unittest

import pandas as pd

from local_rotation_backtest import (
    Config,
    RotationBacktest,
    build_signals,
    month_end_dates,
    stop_fill,
)


class RotationHelpersTest(unittest.TestCase):
    @staticmethod
    def rising_prices():
        index = pd.bdate_range("2020-01-01", periods=220)
        bases = {
            "QQQ": 1.001,
            "TQQQ": 1.003,
            "TECL": 1.002,
            "GLD": 1.0005,
            "IEF": 1.0002,
            "SHY": 1.0001,
        }
        prices = {}
        for symbol, growth in bases.items():
            close = pd.Series([100.0 * growth**i for i in range(len(index))], index=index)
            prices[symbol] = pd.DataFrame(
                {"open": close, "high": close, "low": close, "close": close}
            )
        return prices

    def test_month_end_uses_last_available_session(self):
        index = pd.to_datetime(["2024-03-28", "2024-04-01", "2024-04-30"])
        self.assertEqual(
            month_end_dates(index),
            {pd.Timestamp("2024-03-28"), pd.Timestamp("2024-04-30")},
        )

    def test_stop_fill_models_gap_and_intraday_touch(self):
        self.assertEqual(stop_fill(95.0, 89.0, 90.0), 90.0)
        self.assertEqual(stop_fill(87.0, 85.0, 90.0), 87.0)
        self.assertIsNone(stop_fill(95.0, 91.0, 90.0))

    def test_signal_selects_strongest_risk_and_defensive_assets(self):
        prices = self.rising_prices()
        signals = build_signals(prices)
        last = signals.iloc[-1]
        self.assertTrue(last["risk_on"])
        self.assertEqual(last["risk_pick"], "TQQQ")
        self.assertEqual(last["defensive_pick"], "GLD")
        self.assertEqual(last["target"], "TQQQ")

    def test_round_trip_costs_are_charged_on_both_sides(self):
        prices = self.rising_prices()
        engine = RotationBacktest(prices, Config(initial_cash=1_000.0, cost_bps=10.0))
        day = prices["QQQ"].index[-1]
        engine.buy(day, "TQQQ", 100.0, "TEST")
        engine.sell(day, 100.0, "TEST")
        self.assertAlmostEqual(engine.cash, 1_000.0 / 1.001 * 0.999, places=8)

    def test_qc_close_trades_first_session_close_and_skips_old_stop(self):
        prices = self.rising_prices()
        days = prices["QQQ"].index
        start = days[-20]
        prior = days[-21]
        # Make the selected asset cross its old stop intraday on the rebalance
        # session. QC has already cancelled that stop, so it must remain held.
        prices["TQQQ"].loc[start, "low"] = 1.0
        engine = RotationBacktest(
            prices,
            Config(start_date=start.date(), end_date=start.date(), execution="qc_close"),
        )
        engine.signals.loc[prior, "target"] = "TQQQ"
        engine.symbol = "TQQQ"
        engine.shares = 1.0
        engine.cash = 0.0
        engine.stop_reference = 100.0

        daily, trades = engine.run()

        self.assertFalse((trades.get("reason", pd.Series(dtype=str)) == "STOP").any())
        self.assertTrue(trades.empty)
        self.assertEqual(daily.iloc[0]["holding"], "TQQQ")
        self.assertAlmostEqual(
            float(daily.iloc[0]["stop_reference"]),
            float(prices["TQQQ"].at[prior, "close"]),
        )


if __name__ == "__main__":
    unittest.main()
