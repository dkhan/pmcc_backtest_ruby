import unittest
from datetime import time

import pandas as pd

from local_conditional_sector_rotation import SYMBOLS
from research_conditional_sector_rotation_5min_current import (
    TIMES,
    signal_and_fill_prices,
)


class FiveMinuteCurrentTimingTest(unittest.TestCase):
    def test_all_regular_session_decisions_are_five_minutes_apart(self):
        self.assertEqual(TIMES[0], time(9, 30))
        self.assertEqual(TIMES[-1], time(15, 55))
        self.assertEqual(len(TIMES), 78)
        minutes = [clock.hour * 60 + clock.minute for clock in TIMES]
        self.assertTrue(all(right - left == 5 for left, right in zip(minutes, minutes[1:])))

    def test_1545_signal_uses_1540_close_and_1545_fill_open(self):
        index = pd.DatetimeIndex(
            ["2026-01-02 15:40", "2026-01-02 15:45"],
            tz="America/New_York",
        )
        frames = {}
        for offset, symbol in enumerate(SYMBOLS):
            frames[symbol] = pd.DataFrame(
                {"open": [100 + offset, 200 + offset], "close": [150 + offset, 250 + offset]},
                index=index,
            )
        signal, fill = signal_and_fill_prices(
            frames, pd.Timestamp("2026-01-02"), time(15, 45)
        )
        for offset, symbol in enumerate(SYMBOLS):
            self.assertEqual(signal[symbol], 150 + offset)
            self.assertEqual(fill[symbol], 200 + offset)

    def test_open_has_no_future_bar_dependency(self):
        index = pd.DatetimeIndex(["2026-01-02 09:30"], tz="America/New_York")
        frames = {
            symbol: pd.DataFrame({"open": [100.0], "close": [999.0]}, index=index)
            for symbol in SYMBOLS
        }
        signal, fill = signal_and_fill_prices(
            frames, pd.Timestamp("2026-01-02"), time(9, 30)
        )
        self.assertTrue(all(value == 100.0 for value in signal.values()))
        self.assertTrue(all(value == 100.0 for value in fill.values()))

    def test_missing_fill_bar_uses_last_completed_price(self):
        prior = pd.Timestamp("2026-01-02 15:40", tz="America/New_York")
        current = pd.Timestamp("2026-01-02 15:45", tz="America/New_York")
        frames = {}
        for symbol in SYMBOLS:
            index = pd.DatetimeIndex([prior] if symbol == "TECS" else [prior, current])
            frames[symbol] = pd.DataFrame(
                {"open": [100.0] * len(index), "close": [101.0] * len(index)},
                index=index,
            )
        signal, fill = signal_and_fill_prices(
            frames, pd.Timestamp("2026-01-02"), time(15, 45)
        )
        self.assertTrue(all(value == 101.0 for value in signal.values()))
        self.assertEqual(fill["TECS"], 101.0)

    def test_missing_spy_bar_rejects_closed_session(self):
        index = pd.DatetimeIndex(["2026-01-02 12:30"], tz="America/New_York")
        frames = {
            symbol: pd.DataFrame({"open": [100.0], "close": [101.0]}, index=index)
            for symbol in SYMBOLS
        }
        self.assertIsNone(
            signal_and_fill_prices(
                frames, pd.Timestamp("2026-01-02"), time(15, 30)
            )
        )


if __name__ == "__main__":
    unittest.main()
