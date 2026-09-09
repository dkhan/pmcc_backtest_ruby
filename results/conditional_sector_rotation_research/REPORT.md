# Conditional Sector Rotation robustness report

All signals use adjusted closes through the prior session; trades execute at the next session open. The baseline uses 95% allocation and 5 bps cost per side.

## Baseline

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| baseline | $484,043,935 | 96.0% | -51.0% | 56.5% | 1.47 | 307 | $13,251,617 |

## Benchmark

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SPY_buy_hold | $194,381 | 15.0% | -33.7% | 16.5% | 0.93 | 1 | $0 |
| QQQ_buy_hold | $357,639 | 19.9% | -35.1% | 20.5% | 0.99 | 1 | $0 |
| TQQQ_buy_hold | $5,085,525 | 43.7% | -81.7% | 60.8% | 0.90 | 1 | $0 |

## Chronological Holdout

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2012-2017 | $345,217 | 55.0% | -36.5% | 38.8% | 1.33 | 95 | $9,402 |
| 2018-2021 | $615,055 | 123.0% | -55.7% | 60.5% | 1.62 | 91 | $5,298 |
| 2022-latest | $406,980 | 81.8% | -45.0% | 63.9% | 1.25 | 123 | $10,924 |

## Allocation

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| allocation=50% | $4,943,380 | 43.4% | -30.7% | 29.3% | 1.38 | 307 | $127,161 |
| allocation=75% | $80,995,636 | 73.5% | -42.9% | 44.4% | 1.46 | 307 | $2,159,041 |
| allocation=90% | $303,788,978 | 89.9% | -49.0% | 53.3% | 1.47 | 307 | $8,260,922 |

## Execution Cost

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cost=0bps | $625,896,262 | 99.5% | -50.0% | 56.5% | 1.50 | 307 | $0 |
| cost=10bps | $380,001,186 | 92.8% | -51.9% | 56.4% | 1.45 | 307 | $21,456,517 |
| cost=25bps | $160,295,229 | 81.8% | -54.2% | 56.2% | 1.34 | 307 | $24,982,520 |
| cost=50bps | $48,777,984 | 67.6% | -58.3% | 56.3% | 1.20 | 307 | $18,390,609 |

## Execution Delay

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| delay=1d | $40,839,300 | 65.6% | -67.6% | 59.4% | 1.14 | 268 | $1,283,613 |
| delay=2d | $6,592,852 | 46.2% | -64.5% | 57.4% | 0.95 | 239 | $209,454 |

## Branch Ablation

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| no_uvxy | $58,128,171 | 69.6% | -59.9% | 55.0% | 1.24 | 200 | $884,179 |
| no_oversold_dip | $106,059,167 | 76.7% | -50.3% | 57.1% | 1.29 | 273 | $3,325,104 |
| no_inverse | $163,362,757 | 82.0% | -50.6% | 53.2% | 1.39 | 284 | $4,922,289 |
| simple_regime | $2,023,054 | 34.9% | -56.3% | 44.9% | 0.90 | 67 | $26,242 |

## Hysteresis

| case | ending_equity | cagr | max_drawdown | volatility | sharpe | entries | fees |
| --- | --- | --- | --- | --- | --- | --- | --- |
| confirm=2,hold=0 | $113,464,767 | 77.5% | -60.1% | 58.2% | 1.28 | 164 | $1,303,948 |
| confirm=3,hold=0 | $8,952,233 | 49.3% | -79.8% | 56.8% | 0.99 | 100 | $130,873 |
| confirm=1,hold=2 | $494,015,998 | 96.3% | -51.2% | 58.1% | 1.45 | 268 | $13,290,100 |
| confirm=1,hold=3 | $87,275,424 | 74.4% | -55.3% | 57.9% | 1.25 | 239 | $2,260,613 |
| confirm=2,hold=2 | $113,464,767 | 77.5% | -60.1% | 58.2% | 1.28 | 164 | $1,303,948 |
| confirm=2,hold=3 | $149,875,106 | 80.9% | -59.6% | 58.1% | 1.31 | 157 | $1,702,494 |

## Parameter sensitivity ranges

This summarizes one-parameter-at-a-time perturbations; it is not an optimization search.

| parameter | cases | min_cagr | median_cagr | max_cagr | worst_drawdown | min_sharpe | max_sharpe |
| --- | --- | --- | --- | --- | --- | --- | --- |
| oversold | 4 | 0.8930946706645495 | 0.9407121442752016 | 0.9530547231628459 | -0.6002154770109072 | 1.4101717991121288 | 1.4561053035531732 |
| qqq_overbought | 4 | 0.8564841230914524 | 0.9451121689164586 | 0.9653748533932989 | -0.5107324961562785 | 1.3892072891509695 | 1.4797700813068537 |
| qqq_sma_period | 3 | 0.9598874682375422 | 0.9598874682375422 | 0.9598874682375422 | -0.5101208048211702 | 1.4732406231115942 | 1.4732406231115942 |
| rsi_period | 2 | 0.6606201956793256 | 0.7269674756668413 | 0.7933147556543569 | -0.5999416971999072 | 1.1593136382186926 | 1.3284835704680023 |
| spy_overbought | 4 | 0.8814344262029175 | 0.9486537757313028 | 0.9976234012234222 | -0.5108375709285733 | 1.3828771135069335 | 1.5084101533810588 |
| spy_sma_period | 4 | 0.7056891643250338 | 0.8339150932220954 | 0.8668075073040111 | -0.7018062597564207 | 1.2267723192804525 | 1.389077992916604 |
| sqqq_extreme | 4 | 0.9598874682375422 | 0.9598874682375422 | 0.9598874682375422 | -0.5101208048211702 | 1.4732406231115942 | 1.4732406231115942 |
| sqqq_trend | 4 | 0.7890267715743771 | 0.8935373480469314 | 0.9300994553523001 | -0.5100776463482328 | 1.3132639969796667 | 1.447342191562066 |
| tqqq_sma_period | 3 | 0.8485602943897732 | 0.8603475077530514 | 0.9004133996931409 | -0.5943994397679364 | 1.3736870146864404 | 1.4131068649333702 |
| uvxy_extreme | 4 | 0.9346006712869017 | 0.955670866254385 | 0.9800123520957489 | -0.5101208048211702 | 1.4479350618891456 | 1.4765003984843583 |
| uvxy_trigger | 4 | 0.8991677423380855 | 0.9330638601364121 | 0.9598874682375422 | -0.5513151033623389 | 1.4241271797010073 | 1.4732406231115942 |

## Branch attribution

| branch | holding | days | entries | pnl |
| --- | --- | --- | --- | --- |
| bull_trend | TQQQ | 3050 | 87 | 267663075.7365526 |
| bear_trend_long | TECL | 139 | 33 | 77470124.75774613 |
| bear_tqqq_oversold | TECL | 72 | 33 | 58293857.781939104 |
| bear_relative_inverse | TECS | 133 | 40 | 57061332.709583364 |
| bear_spy_oversold | SPXL | 11 | 7 | 14557805.534791613 |
| bull_overbought | UVXY | 129 | 53 | 11862317.742512602 |
| bear_relative_bond | BSV | 114 | 34 | 4717610.441287251 |
| bear_uvxy_momentum | UVXY | 9 | 6 | -6999.6774764207075 |
| bear_trend_inverse | TECS | 33 | 14 | -7600191.57138106 |

## Interpretation limits

The chronological slices are pseudo-out-of-sample diagnostics, not genuinely unseen data, because the original rules were already known before this test. Yahoo adjusted daily data and next-open fills will not exactly reproduce QuantConnect's minute fills. Results exclude taxes, market impact, borrow constraints, and live-order failures.
