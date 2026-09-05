"""US short-term reversal ETF-timing strategy (FinLab).

Inside a positive QQQ trend regime, hold whichever of two leveraged growth
ETFs (TQQQ / TECL) lagged over the last 3 trading days. When the regime turns
risk-off, rotate into the strongest defensive asset (IEF / GLD / SHY).
Monthly rebalance, one holding at a time, 8% intramonth stop-loss.

Complete the AI-assisted setup flow at https://finlab.finance/en/setup before running this file.
"""

import finlab
from finlab import data
from finlab.backtest import sim
from finlab.dataframe import FinlabDataFrame

finlab.login()  # opens a browser prompt; no token needed in the script

data.set_market("us_fund")

RISK_ASSETS = ["TQQQ", "TECL"]
DEFENSIVE_ASSETS = ["IEF", "GLD", "SHY"]

close = data.get("us_fund_price:adj_close")[["QQQ"] + RISK_ASSETS + DEFENSIVE_ASSETS]

# Regime filter: QQQ above its 200-day average AND positive 6-month momentum.
qqq_trend = close["QQQ"] > close["QQQ"].rolling(200, min_periods=100).mean()
qqq_momentum = close["QQQ"] / close["QQQ"].shift(126) - 1
risk_on = qqq_trend & (qqq_momentum > 0)

# Reversal signal: rank the leveraged ETFs by NEGATIVE 3-day return,
# so the recent laggard scores highest.
reversal_score = FinlabDataFrame(-(close[RISK_ASSETS] / close[RISK_ASSETS].shift(3) - 1))

# Defensive sleeve: strongest 3-month-minus-1-month momentum among IEF/GLD/SHY.
defensive_score = FinlabDataFrame(
    close[DEFENSIVE_ASSETS].pct_change(63) - close[DEFENSIVE_ASSETS].pct_change(21)
)

risky_position = reversal_score.is_largest(1) & risk_on.to_frame().reindex(reversal_score.index).iloc[:, 0]
defensive_position = (
    defensive_score.is_largest(1) & (~risk_on).to_frame().reindex(defensive_score.index).iloc[:, 0]
)

position = (
    risky_position.reindex(close.index).fillna(False).astype(float)
    .join(defensive_position.reindex(close.index).fillna(False).astype(float), how="outer")
    .fillna(0)
    .T.groupby(level=0).max().T
)
position = position.loc["2016-01-01":]

report = sim(
    position,
    resample="M",
    name="us_short_term_reversal_etf",
    upload=False,
    fee_ratio=0,
    tax_ratio=0,
    trade_at_price="close",
    position_limit=1,
    stop_loss=0.08,
    touched_exit=True,
)
print(report.get_stats())
report.display()
