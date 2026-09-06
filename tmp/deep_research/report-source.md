# Can a public trading strategy beat 67.8% CAGR with no more than 35% drawdown?

Audience: Individual investor evaluating a weekly/manual leveraged-ETF strategy  
Research date: 2026-09-05  
Scope: Publicly documented, rules-based strategies; emphasis on liquid U.S. securities, comparable multi-year samples, executable rules, and evidence available without trusting marketing claims.

## Executive answer

No publicly reproducible strategy was found that credibly exceeds both 67.8% CAGR and a 35% maximum-drawdown ceiling. Several pages claim numbers above both thresholds, but none supplies the combination of complete rules/code, realistic execution, a comparable sample, and independent or out-of-sample verification. The current laggard-7 / TQQQ-TECL / GLD-only result therefore remains the strongest result in the reviewed set, but it is itself an optimized in-sample backtest rather than a validated expectation.

## Screening results

| Candidate | Reported CAGR | Max DD | Verdict |
|---|---:|---:|---|
| Current FinLab-derived laggard-7, 10.5%, GLD-only QC test | 67.805% | 35.1% | Benchmark; reproducible locally/QC, but selected after extensive tuning |
| Anonymous SMA Trading backtest | 173.22% | 21.64% | Clears numbers, fails verification: no strategy identity/rules/code or independent record; 2017-2025 only |
| Anonymous Reddit TQQQ/QID/UGL/SGOV rotation | 72% | 32% | Clears claim, fails verification: vague rules, synthetic pre-2010 TQQQ, no code, no meaningful live history |
| QuantConnect modified In & Out strategy | 73.358% | about 54% | Fails drawdown; exact modified code withheld |
| QuantConnect RSI Rebalance strategy | 66.214% | 32.3% | Strongest open-code near-miss; CAGR is 1.59 points below benchmark |
| QuantConnect Intraday Volatility Regime Harvester | 53.415% | 20.7% | Open code and fees included, but fails return threshold and requires intraday automation |
| Triple X Semiconductor timing service | 59.54% | not clearly presented as a comparable daily MDD | Fails return threshold; retrospective commercial methodology |
| TQQQ/SOXL macro-regime rotation (Reddit) | 49.9% | 41.4% | Fails both; 54,000 parameter combinations and weak long-history behavior |
| 50/50 TQQQ/TMF bimonthly rebalance | 44.9% | under 25% EOM | Fails return threshold; drawdown is end-of-month rather than daily |

## What the apparent winners hide

### SMA Trading: 173.22% CAGR / 21.64% drawdown

The PDF reports an extraordinary 2017-08-30 to 2025-03-11 result, 55% time in market, a 635% best year, no losing calendar year, and a 2.24 Sharpe ratio. But the discoverable artifact does not identify the traded instruments, full rules, costs, code, or an out-of-sample period. The arithmetic claim exists; the strategy does not exist in a reproducible public form. It is a lead, not investable evidence.

### Anonymous TQQQ rotation: 72% CAGR / 32% drawdown

The author describes moving among TQQQ, QID, UGL, and SGOV using moving averages, price-to-average disparity, VIX thresholds, and RSI. Exact thresholds and position sizing are not disclosed. The pre-2010 segment uses synthetic TQQQ, the strategy was finalized using the same backtest, and the author says the real-money record is too short to calculate meaningful CAGR. Community reviewers also flag implausible annual patterns. It cannot be independently reproduced.

### QuantConnect In & Out: 73.358% CAGR

This is the closest high-return platform result. However, the author says the modified code will not be released. When directly asked, the author discloses maximum drawdown of about 0.54, which violates the 35% ceiling. The discussion also notes that different data sources changed results from roughly 1,700% to 400%, demonstrating signal sensitivity.

## Best reproducible alternatives

The open QuantConnect RSI Rebalance example reports 66.214% CAGR, 32.3% drawdown, 1.773 Sharpe, 1,845 orders, $133,085 fees on $10,000 starting capital, and 21.38% turnover. It rotates among TQQQ, QQQ, UVXY, XLU, and GLD using daily RSI-driven weights. It narrowly misses the return threshold and is operationally far more demanding than a monthly strategy. Its embedded code makes it the most relevant candidate for independent reproduction, not an established superior replacement.

The open Intraday Volatility Regime Harvester reports 53.415% CAGR and 20.7% drawdown after $1.46 million in fees on $1 million initial capital. It trades UVXY and TQQQ at seven scheduled intraday times with highly specific optimized thresholds. It offers a lower-drawdown research direction but does not meet the return requirement and cannot be managed in 15-30 minutes weekly.

## Why the hurdle is unusually high

A 67.8% CAGR with a 35% drawdown implies a Calmar ratio near 1.94 over roughly ten years. Public liquid-asset strategies sustaining that combination are exceptionally rare. The current benchmark was selected after testing multiple risk-selection modes, laggard windows, stop percentages, and defensive universes. Research on backtest overfitting defines the key failure mode: the configuration that ranks best in-sample may rank below the median out-of-sample. The Deflated Sharpe Ratio literature specifically requires the number and distribution of trials to judge an observed Sharpe; ordinary Sharpe and CAGR omit that selection penalty.

Leveraged ETFs add path dependence. The SEC warns that their objectives are daily and that performance over longer periods can diverge significantly, especially in volatile markets, potentially creating sudden losses. Historical ETF prices incorporate this effect, but an optimized timing rule can still exploit one unusually favorable realized path.

## Recommendation

Do not replace the current strategy with any discovered claim. The rational next step is not another internet winner; it is a locked-rule validation program:

1. Freeze the current rules before seeing new data: TQQQ/TECL, seven-session laggard, GLD-only defense, 10.5% monthly-reset stop.
2. Treat 2026-09 onward as a true untouched forward test and record every scheduled signal and executable fill.
3. Re-run the complete parameter family using combinatorially symmetric or purged cross-validation, including every stop and lookback already tried, then calculate a Deflated Sharpe Ratio.
4. Independently reproduce the QuantConnect RSI Rebalance strategy on the same 2016-2026 window and with the same fee/slippage assumptions. It is the only open-code candidate close enough to warrant testing.
5. Reject any purported improvement that lacks exact rules, next-bar execution, delisted/survivorship-safe data, transaction costs, and an untouched out-of-sample result.

## Sources

1. QuantConnect, “RSIRebalanceStrategy” embedded backtest, accessed 2026-09-05. https://www.quantconnect.com/terminal/cache/embedded_backtest_83fec99a44c2f2d3bea9fa3dd7027854.html
2. QuantConnect, “IntradayVolatilityRegimeHarvester” embedded backtest, accessed 2026-09-05. https://www.quantconnect.com/terminal/cache/embedded_backtest_562c7d4edcab5662cbd37ffc8ac9440b.html
3. QuantConnect community, “The In & Out Strategy - Continued from Quantopian,” pages 7-8, accessed 2026-09-05. https://www.quantconnect.com/forum/discussion/9597/the-in-amp-out-strategy-continued-from-quantopian/p8
4. SMA Trading, anonymous backtest PDF, 2017-08-30 to 2025-03-11, accessed 2026-09-05. https://smatrading.com/assets/pdf/backtest.pdf
5. Reddit r/TQQQ, “I built a strategy to survive the Dot-com crash with TQQQ,” accessed 2026-09-05. https://www.reddit.com/r/TQQQ/comments/1qdx3tk/i_built_a_strategy_to_survive_the_dotcom_crash/
6. Triple X Market Timing Strategies, “Semiconductor Strategy” fact card, accessed 2026-09-05. https://fact.3xtiming.com/pdf/fact_xsc.pdf
7. Reddit r/LETFs, “I ran 54,000 backtests on a TQQQ/SOXL rotation strategy,” accessed 2026-09-05. https://www.reddit.com/r/LETFs/comments/1tnwufk/i_ran_54000_backtests_on_a_tqqqsoxl_rotation/
8. QuantConnect community, discussion of Lewis A. Glenn, “Long-Term Investing in Triple Leveraged Exchange Traded Funds,” accessed 2026-09-05. https://www.quantconnect.com/forum/discussion/9632/amazing-returns-superior-stock-selection-strategy-superior-in-amp-out-strategy/p12
9. D. H. Bailey, J. M. Borwein, M. Lopez de Prado, Q. J. Zhu, “The Probability of Backtest Overfitting,” 2014. https://carmamaths.org/jon/backtest2.pdf
10. D. H. Bailey and M. Lopez de Prado, “The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality,” 2014. https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf
11. U.S. SEC Office of Investor Education and Advocacy, “Updated Investor Bulletin: Leveraged and Inverse ETFs,” 2023-08-29. https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-alerts/sec

## Claim-to-source ledger

- Current benchmark metrics: user-provided QuantConnect report `Crawling Fluorescent Yellow Horse.json`; verified in this workspace.
- SMA Trading metrics and dates: source 4; access succeeded through indexed PDF metadata, direct PDF fetch timed out.
- Reddit 72/32 claim, rules vagueness, synthetic data, short live record: source 5.
- In & Out CAGR, withheld code, and approximately 54% drawdown: source 3.
- RSI Rebalance metrics, code, fees, turnover, universe: source 1.
- Intraday harvester metrics, schedule, fees, and rules: source 2.
- Triple X metrics and retrospective disclosure: source 6.
- 54,000-test rotation metrics and long-history deterioration: source 7.
- TQQQ/TMF performance and EOM drawdown: source 8.
- PBO definition and in-sample/out-of-sample rank risk: source 9.
- DSR correction for selection bias, non-normality, trial count, and sample length: source 10.
- Leveraged ETF daily-objective and path-dependence warning: source 11.
