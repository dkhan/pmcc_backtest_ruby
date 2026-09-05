"""Run FinLab's published mean-reversion strategy through its paper end date."""

from pathlib import Path

import finlab
from finlab import data
from finlab.backtest import sim
from finlab.dataframe import FinlabDataFrame


START = "2016-06-01"
END = "2026-06-12"
RISK_ASSETS = ["TQQQ", "TECL"]
DEFENSIVE_ASSETS = ["IEF", "GLD", "SHY"]
OUTPUT_DIR = Path(__file__).resolve().parent / "reproduction"


finlab.login()
data.set_market("us_fund")
close = data.get("us_fund_price:adj_close")[
    ["QQQ"] + RISK_ASSETS + DEFENSIVE_ASSETS
].loc[:END]

qqq_trend = close["QQQ"] > close["QQQ"].rolling(200, min_periods=100).mean()
qqq_momentum = close["QQQ"] / close["QQQ"].shift(126) - 1
risk_on = qqq_trend & (qqq_momentum > 0)

reversal_score = FinlabDataFrame(
    -(close[RISK_ASSETS] / close[RISK_ASSETS].shift(3) - 1)
)
defensive_score = FinlabDataFrame(
    close[DEFENSIVE_ASSETS].pct_change(63)
    - close[DEFENSIVE_ASSETS].pct_change(21)
)

risky_position = reversal_score.is_largest(1) & risk_on.to_frame().reindex(
    reversal_score.index
).iloc[:, 0]
defensive_position = defensive_score.is_largest(1) & (~risk_on).to_frame().reindex(
    defensive_score.index
).iloc[:, 0]

position = (
    risky_position.reindex(close.index).fillna(False).astype(float)
    .join(
        defensive_position.reindex(close.index).fillna(False).astype(float),
        how="outer",
    )
    .fillna(0)
    .T.groupby(level=0).max().T
)
position = position.loc["2016-01-01":]

report = sim(
    position,
    resample="M",
    name="us_short_term_reversal_etf_paper_period",
    upload=False,
    fee_ratio=0,
    tax_ratio=0,
    trade_at_price="close",
    position_limit=1,
    stop_loss=0.08,
    touched_exit=True,
)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
report.creturn.rename("strategy").to_csv(OUTPUT_DIR / "daily_equity.csv")
report.trades.to_csv(OUTPUT_DIR / "trades.csv", index=False)
close.to_csv(OUTPUT_DIR / "finlab_adjusted_close.csv")
position.to_csv(OUTPUT_DIR / "daily_target_matrix.csv")
print(report.get_stats())
print(f"Saved reproduction files to {OUTPUT_DIR}")
