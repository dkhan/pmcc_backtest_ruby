import unittest
from datetime import date

import pandas as pd

from local_tqqq_seasonal_macd_backtest import Config, build_signals, run_backtest


class SeasonalMacdTest(unittest.TestCase):
    def test_seasonal_gate_ignores_crosses_before_october(self):
        index = pd.bdate_range("2023-01-02", "2024-07-01")
        close = pd.Series(100.0, index=index)
        # Oscillation creates many crosses, but only one entry in the Oct-Apr cycle.
        close += pd.Series(range(len(index)), index=index).map(lambda x: 5 if x % 20 < 10 else -5)
        signals = build_signals(close, 3, 6, 2, exit_month=4)
        entries = signals.index[signals["event"] == "BULLISH_MACD_AFTER_OCT_1"]
        exits = signals.index[signals["event"] == "BEARISH_MACD_AFTER_APR_1"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(exits), 1)
        self.assertGreaterEqual(entries[0], pd.Timestamp("2023-10-01"))
        self.assertGreaterEqual(exits[0], pd.Timestamp("2024-04-01"))

    def test_trade_executes_at_next_open_and_charges_both_sides(self):
        index = pd.bdate_range("2022-01-03", "2024-07-01")
        qclose = pd.Series(
            [100 + (5 if (i // 10) % 2 else -5) for i in range(len(index))], index=index
        )
        qqq = pd.DataFrame({"open": qclose, "close": qclose})
        tqqq = pd.DataFrame({"open": 100.0, "close": 100.0}, index=index)
        _, trades, _ = run_backtest(
            qqq,
            tqqq,
            Config(
                start_date=date(2022, 1, 3),
                end_date=date(2024, 7, 1),
                cost_bps=10,
                exit_month=4,
            ),
        )
        self.assertGreaterEqual(len(trades), 2)
        for _, trade in trades.iterrows():
            signal_day = pd.Timestamp(trade["signal_date"])
            fill_day = pd.Timestamp(trade["date"])
            self.assertGreater(fill_day, signal_day)


if __name__ == "__main__":
    unittest.main()
