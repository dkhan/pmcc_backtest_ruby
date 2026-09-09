# Conditional Sector Rotation hourly current-metrics test

## Scope

- Period: October 10, 2023 through September 4, 2026.
- Sessions: 729 common sessions.
- Decision times: 09:30 through 15:30 Eastern in one-hour increments.
- Starting capital: $25,000.
- Allocation: 95%.
- Trading cost: 5 basis points per side.
- Signal: prior daily history plus the last completed hourly bar.
- Execution: opening price of the bar beginning at the decision time.

For example, the 15:30 case calculates its indicators using data through the completed 14:30-15:30 bar and executes at the 15:30 bar open. The test does not use the future close of the execution bar. On early-close sessions occurring before a selected decision time, it makes no trade and retains the holding's daily mark-to-market return.

## Results

| Decision time | Ending equity | CAGR | Maximum drawdown | Sharpe | Entries | Difference from 15:30 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 09:30 | $92,261 | 56.8% | -41.5% | 1.07 | 46 | -51.4% |
| 10:30 | $115,976 | 69.7% | -41.6% | 1.23 | 53 | -38.9% |
| 11:30 | $92,384 | 56.9% | -49.4% | 1.08 | 45 | -51.3% |
| 12:30 | $173,414 | 94.9% | -35.4% | 1.48 | 45 | -8.7% |
| 13:30 | $158,611 | 89.0% | -45.0% | 1.47 | 49 | -16.5% |
| 14:30 | $147,042 | 84.1% | -48.7% | 1.38 | 47 | -22.6% |
| 15:30 | $189,855 | 101.1% | -48.8% | 1.52 | 45 | 0.0% |

## Interpretation

15:30 produced the highest ending equity and CAGR in this nearly three-year sample. It was not uniformly superior on risk: 12:30 had the smallest maximum drawdown at -35.4%, while ending only 8.7% below 15:30. The substantial variation across adjacent hours confirms that using current-session metrics materially changes both signal dates and holdings.

This is evidence in favor of late-session evaluation, not proof that 15:30 or 15:45 is universally optimal. The sample excludes 2020, the period responsible for much of the extraordinary full-history result. The tested times are also discrete one-hour observations; 15:45 is not directly available in Yahoo's long hourly history.
