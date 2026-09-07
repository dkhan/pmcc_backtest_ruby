import unittest

import pandas as pd

from local_rotation_backtest import (
    Config,
    RotationBacktest,
    build_signals,
    month_end_dates,
    qc_rebalance_dates,
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

    def test_defaults_match_quantconnect_finlab_strategy(self):
        config = Config()
        self.assertEqual(config.execution, "qc_close")
        self.assertEqual(config.ema_rule, "off")
        self.assertEqual(config.risk_selection, "laggard")
        self.assertEqual(config.laggard_days, 7)
        self.assertEqual(config.stop_loss, 0.105)

    def test_ema_rule_can_confirm_or_replace_finlab_regime(self):
        prices = self.rising_prices()
        off = build_signals(prices, ema_rule="off").iloc[-1]
        confirm = build_signals(prices, ema_rule="confirm").iloc[-1]
        replace = build_signals(prices, ema_rule="replace").iloc[-1]
        self.assertTrue(off["finlab_risk_on"])
        self.assertTrue(off["ema_risk_on"])
        self.assertTrue(confirm["risk_on"])
        self.assertTrue(replace["risk_on"])

    def test_unknown_ema_rule_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ema_rule must be one of"):
            build_signals(self.rising_prices(), ema_rule="unknown")

    def test_stop_fill_models_gap_and_intraday_touch(self):
        self.assertEqual(stop_fill(95.0, 89.0, 90.0), 90.0)
        self.assertEqual(stop_fill(87.0, 85.0, 90.0), 87.0)
        self.assertIsNone(stop_fill(95.0, 91.0, 90.0))

    def test_first_monday_includes_month_start_when_it_is_monday(self):
        index = pd.bdate_range("2024-07-01", "2024-08-09")
        dates = qc_rebalance_dates(index, "first_monday", index[0])
        self.assertIn(pd.Timestamp("2024-07-01"), dates)
        self.assertIn(pd.Timestamp("2024-08-05"), dates)

    def test_signal_selects_strongest_risk_and_defensive_assets(self):
        prices = self.rising_prices()
        signals = build_signals(prices)
        last = signals.iloc[-1]
        self.assertTrue(last["risk_on"])
        self.assertEqual(last["risk_pick"], "TQQQ")
        self.assertEqual(last["defensive_pick"], "GLD")
        self.assertEqual(last["target"], "TQQQ")

    def test_signal_can_select_weakest_risk_asset(self):
        prices = self.rising_prices()
        last = build_signals(prices, risk_selection="weakest").iloc[-1]
        self.assertTrue(last["risk_on"])
        self.assertEqual(last["risk_pick"], "TECL")
        self.assertEqual(last["defensive_pick"], "GLD")
        self.assertEqual(last["target"], "TECL")

    def test_signal_can_select_three_day_laggard(self):
        prices = self.rising_prices()
        last = build_signals(
            prices, risk_selection="laggard", laggard_days=3
        ).iloc[-1]
        self.assertTrue(last["risk_on"])
        self.assertEqual(last["risk_pick"], "TECL")
        self.assertEqual(last["target"], "TECL")

    def test_signal_can_select_one_and_two_day_laggards(self):
        prices = self.rising_prices()
        for days in (1, 2):
            with self.subTest(days=days):
                last = build_signals(
                    prices, risk_selection="laggard", laggard_days=days
                ).iloc[-1]
                self.assertEqual(last["risk_pick"], "TECL")

    def test_unknown_risk_selection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unknown risk selection"):
            build_signals(self.rising_prices(), risk_selection="unknown")

    def test_invalid_laggard_days_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "laggard_days must be at least 1"):
            build_signals(
                self.rising_prices(), risk_selection="laggard", laggard_days=0
            )

    def test_random_selection_is_reproducible_and_seeded(self):
        prices = self.rising_prices()
        first = build_signals(prices, risk_selection="random", random_seed=7)
        repeat = build_signals(prices, risk_selection="random", random_seed=7)
        other = build_signals(prices, risk_selection="random", random_seed=8)
        self.assertTrue(first["risk_pick"].equals(repeat["risk_pick"]))
        self.assertFalse(first["risk_pick"].equals(other["risk_pick"]))

    def test_alternate_selection_uses_tqqq_in_odd_and_tecl_in_even_months(self):
        signals = build_signals(self.rising_prices(), risk_selection="alternate")
        january = signals.loc[signals.index.month == 1, "risk_pick"]
        february = signals.loc[signals.index.month == 2, "risk_pick"]
        self.assertTrue(january.eq("TQQQ").all())
        self.assertTrue(february.eq("TECL").all())

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
        month_starts = [
            days[i] for i in range(1, len(days)) if days[i].month != days[i - 1].month
        ]
        start = month_starts[-1]
        prior = days[days.get_loc(start) - 1]
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

    def test_daily_qqq_risk_off_rotates_risk_holding_next_close(self):
        prices = self.rising_prices()
        days = prices["QQQ"].index
        month_starts = [
            days[i] for i in range(1, len(days)) if days[i].month != days[i - 1].month
        ]
        start = month_starts[-1]
        start_index = days.get_loc(start)
        prior = days[start_index - 1]
        exit_day = days[start_index + 1]
        engine = RotationBacktest(
            prices,
            Config(
                start_date=start.date(),
                end_date=exit_day.date(),
                execution="qc_close",
                daily_qqq_risk_off=True,
            ),
        )
        engine.signals.loc[prior, "target"] = "TQQQ"
        engine.signals.loc[start, "risk_on"] = False
        engine.signals.loc[start, "defensive_pick"] = "GLD"

        daily, trades = engine.run()

        self.assertEqual(daily.iloc[-1]["holding"], "GLD")
        self.assertEqual(trades.iloc[-2]["reason"], "DAILY_QQQ_RISK_OFF")
        self.assertEqual(trades.iloc[-2]["action"], "SELL")
        self.assertEqual(trades.iloc[-1]["reason"], "DAILY_QQQ_RISK_OFF")
        self.assertEqual(trades.iloc[-1]["action"], "BUY")


if __name__ == "__main__":
    unittest.main()
