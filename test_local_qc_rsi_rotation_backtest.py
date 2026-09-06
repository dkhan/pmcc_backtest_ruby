import math
import unittest

import pandas as pd

from local_smart_qqq import choose_target, qc_wilder_rsi


class RSIRebalanceTests(unittest.TestCase):
    def test_rsi_extremes(self):
        self.assertEqual(qc_wilder_rsi(pd.Series(range(50))), 100.0)
        self.assertEqual(qc_wilder_rsi(pd.Series(range(50, 0, -1))), 0.0)

    def test_rsi_needs_full_extension(self):
        self.assertTrue(math.isnan(qc_wilder_rsi(pd.Series(range(49)))))

    def test_extreme_rsi_overrides_spy_regime(self):
        self.assertEqual(choose_target(80, 90, 100, 100), "UVXY")
        self.assertEqual(choose_target(30, 110, 100, 100), "TQQQ")

    def test_spy_regime_tree(self):
        self.assertEqual(choose_target(50, 120, 110, 100), "QQQ")
        self.assertEqual(choose_target(50, 105, 110, 100), "XLU")
        self.assertEqual(choose_target(50, 105, 100, 110), "QQQ")
        self.assertEqual(choose_target(50, 90, 100, 110), "GLD")

    def test_missing_indicator_has_no_target(self):
        self.assertIsNone(choose_target(math.nan, 100, 100, 100))


if __name__ == "__main__":
    unittest.main()
