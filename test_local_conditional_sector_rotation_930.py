import unittest
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from local_conditional_sector_rotation_930 import (
    SYMBOLS,
    StrategyConfig,
    build_indicators,
    run_backtest,
)


def synthetic_prices() -> dict[str, pd.DataFrame]:
    dates = pd.bdate_range("2023-01-02", "2025-01-03")
    prices = {}
    for offset, symbol in enumerate(SYMBOLS):
        close = 100.0 + offset + np.arange(len(dates)) * 0.10
        prices[symbol] = pd.DataFrame(
            {
                "open": close - 0.03,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
            },
            index=dates,
        )
    return prices


class CurrentOpenStrategyTest(unittest.TestCase):
    def test_current_close_does_not_enter_same_day_signal(self):
        prices = synthetic_prices()
        day = pd.Timestamp("2024-06-03")
        baseline = build_indicators(prices, StrategyConfig()).loc[day]
        altered = {symbol: frame.copy() for symbol, frame in prices.items()}
        altered["SPY"].loc[day, "close"] = 1_000_000.0
        changed = build_indicators(altered, StrategyConfig()).loc[day]
        self.assertEqual(baseline.SPY_close, prices["SPY"].at[day, "open"])
        self.assertEqual(baseline.SPY_rsi, changed.SPY_rsi)
        self.assertEqual(baseline.SPY_sma, changed.SPY_sma)

    def test_positive_year_withdraws_configured_profit_percentage(self):
        cfg = replace(
            StrategyConfig(),
            start_date=date(2024, 1, 2),
            end_date=date(2025, 1, 3),
            cost_bps=0.0,
            annual_tax_withdrawal_rate=0.443,
        )
        daily, _ = run_backtest(synthetic_prices(), cfg)
        withdrawals = daily[daily.tax_withdrawal > 0]
        self.assertEqual(len(withdrawals), 1)
        row = withdrawals.iloc[0]
        pre_withdrawal_balance = row.ending_balance + row.tax_withdrawal
        expected = (pre_withdrawal_balance - cfg.initial_cash) * 0.443
        self.assertAlmostEqual(row.tax_withdrawal, expected, places=6)
        self.assertAlmostEqual(
            daily.cumulative_tax_withdrawal.iloc[-1], row.tax_withdrawal, places=6
        )


if __name__ == "__main__":
    unittest.main()
