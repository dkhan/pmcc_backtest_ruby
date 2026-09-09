# Conditional Sector Rotation morning execution test

Hourly adjusted data covers 729 common ETF sessions and 42 target entries. Signals use the prior completed daily session. Every variant uses the same signal and trades at the opening price of the indicated hourly bar with 95% allocation and 5 bps cost per side.

| Execution | Ending equity | CAGR | Max drawdown | Sharpe | Ending vs. 09:30 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 09:30 | $139,767 | 80.9% | -42.8% | 1.32 | baseline |
| 10:30 | $133,509 | 78.1% | -38.9% | 1.31 | -4.5% |
| 11:30 | $118,310 | 70.8% | -40.3% | 1.24 | -15.4% |

The hourly feed cannot isolate 09:31 or exactly 12:00. The 09:30 bar open is the closest available proxy for the QC 09:31 execution.

Across matched rotations, later execution beat the opening execution on about 51% of individual switches. The median difference was close to zero, but adverse outliers grew with delay. At 10:30, later execution produced an average switch disadvantage of about 0.09%. At 11:30, the average disadvantage was about 0.37%. This asymmetric tail—not a win on every early trade—explains most of the compounding difference.

Conclusion: 09:31 is preferable but not a hard cliff. Execution during the first hour remained close in this sample. Waiting until late morning materially reduced compounded performance. This is a limited recent-history test and does not prove that the same relationship held throughout 2012–2026.
