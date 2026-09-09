# Conditional Sector Rotation five-minute timing test

## Scope

- Decision times: every five minutes from 09:30 through 15:55 Eastern (78 cases).
- Data: Yahoo adjusted five-minute bars, 59 common sessions from June 12 through September 4, 2026.
- Starting capital: $25,000.
- Allocation: 95%.
- Trading cost: 5 basis points per side.
- Current-session signal: previous daily closes plus the most recently completed five-minute bar.
- Execution: open of the bar beginning at the decision time.

For example, the 15:45 case uses the close of the 15:40-15:45 bar for the signal and the 15:45 bar open for execution. This prevents the test from using the future close of the execution bar.

## Results

The decision tree selected TQQQ throughout the available period for every tested time. Each case therefore contains one initial purchase and no rotations. Timing differences in this sample only reflect the price of that initial TQQQ purchase.

| Decision time | Ending equity | Difference from 15:45 | Entries | Maximum drawdown |
| --- | ---: | ---: | ---: | ---: |
| 09:30 | $23,808 | 1.29% | 1 | -30.08% |
| 09:45 | $24,313 | 3.44% | 1 | -30.11% |
| 12:00 | $23,941 | 1.86% | 1 | -30.10% |
| 15:30 | $23,461 | -0.19% | 1 | -30.04% |
| 15:40 | $23,549 | 0.19% | 1 | -30.11% |
| 15:45 | $23,505 | 0.00% | 1 | -30.08% |
| 15:50 | $23,505 | 0.00% | 1 | -30.08% |
| 15:55 | $23,455 | -0.21% | 1 | -30.05% |

The best endpoint was 09:45 at $24,313, 3.44% above the 15:45 case. The worst was 11:25 at $23,255, 1.06% below the 15:45 case. These rankings do not establish an optimal execution time because they are driven by one entry on one date.

## Interpretation

This test validates five-minute mechanics and confirms that 15:45 does not use its own future bar. It does not validate signal-timing performance because the recent Yahoo window contains no target changes. Yahoo only exposes roughly 60 days of five-minute history. A meaningful comparison across 2012-2026, particularly the 2020 volatility episode, requires QuantConnect minute history or another licensed intraday dataset.
