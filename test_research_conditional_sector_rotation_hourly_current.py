import unittest
from datetime import time

from research_conditional_sector_rotation_hourly_current import TIMES


class HourlyCurrentTimingTest(unittest.TestCase):
    def test_regular_session_times_are_hourly(self):
        self.assertEqual(
            TIMES,
            (
                time(9, 30),
                time(10, 30),
                time(11, 30),
                time(12, 30),
                time(13, 30),
                time(14, 30),
                time(15, 30),
            ),
        )


if __name__ == "__main__":
    unittest.main()
