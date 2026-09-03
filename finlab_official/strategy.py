"""US ETF rotation: one regime signal, two risk levels (FinLab).

Both systems read the same risk-on/risk-off signal from QQQ (price above its
200-day moving average AND a positive 126-day return) and rebalance monthly:

  * "conservative" — rotates between the two strongest 2x leveraged equity
    ETFs (QLD / SSO, held 50/50) in risk-on, with a 6% stop.
  * "aggressive"   — holds the single strongest 3x leveraged growth ETF
    (TQQQ or TECL) in risk-on, with a 10% stop.

In risk-off, both park in the single strongest defensive ETF among
treasuries (IEF), gold (GLD), and cash-like short treasuries (SHY).

Complete the AI-assisted setup flow at https://finlab.finance/en/setup before running this file.

    python strategy.py
"""
import finlab
from finlab import data
from finlab.backtest import sim
from finlab.dataframe import FinlabDataFrame

# Pick which version to run: "conservative" (QLD/SSO) or "aggressive" (TQQQ/TECL).
STRATEGY = "conservative"

PROFILES = {
    "conservative": {
        "risk_assets": ["QLD", "SSO"],   # 2x leveraged NASDAQ-100 / S&P 500
        "risk_holdings": 2,              # hold both, 50% each
        "risk_lookback": 126,            # rank by 6-month return
        "risk_skip": 0,
        "stop_loss": 0.06,               # 6% touched stop
    },
    "aggressive": {
        "risk_assets": ["TQQQ", "TECL"],  # 3x leveraged NASDAQ-100 / tech
        "risk_holdings": 1,               # hold only the strongest one
        "risk_lookback": 63,              # rank by 3-month minus 1-month return
        "risk_skip": 21,
        "stop_loss": 0.10,                # 10% touched stop
    },
}

DEFENSIVE_ASSETS = ["IEF", "GLD", "SHY"]  # treasuries / gold / cash-like (risk-off)


def build_position(profile):
    data.set_market("us_fund")
    tickers = ["QQQ"] + profile["risk_assets"] + DEFENSIVE_ASSETS
    close = data.get("us_fund_price:adj_close")[tickers]

    # 1. Regime signal: QQQ above its 200-day average AND positive 126-day return.
    qqq_trend = close["QQQ"] > close["QQQ"].rolling(200, min_periods=100).mean()
    qqq_momentum = close["QQQ"] / close["QQQ"].shift(126) - 1
    risk_on = qqq_trend & (qqq_momentum > 0)

    # 2. Rank the risk ETFs by recent momentum (optionally skipping the last month).
    risk_score = close[profile["risk_assets"]].pct_change(profile["risk_lookback"])
    if profile["risk_skip"]:
        risk_score = risk_score - close[profile["risk_assets"]].pct_change(profile["risk_skip"])
    risk_score = FinlabDataFrame(risk_score)

    # 3. Rank defensive ETFs by 3-month-minus-1-month momentum.
    defensive_score = FinlabDataFrame(
        close[DEFENSIVE_ASSETS].pct_change(63) - close[DEFENSIVE_ASSETS].pct_change(21)
    )

    # 4. Risk-on: equal-weight the strongest risk ETFs. Risk-off: 1 defensive ETF.
    risky = risk_score.is_largest(profile["risk_holdings"]) & risk_on.to_frame().reindex(risk_score.index).iloc[:, 0]
    risky = risky.astype(float).div(risky.astype(float).sum(axis=1), axis=0).fillna(0)
    defensive = (
        defensive_score.is_largest(1) & (~risk_on).to_frame().reindex(defensive_score.index).iloc[:, 0]
    ).astype(float)

    position = (
        risky.reindex(close.index).fillna(0)
        .join(defensive.reindex(close.index).fillna(0), how="outer")
        .fillna(0)
        .T.groupby(level=0).max().T
    )
    return position.loc["2016-01-01":]


def main():
    finlab.login()  # finlab guides you through login automatically
    profile = PROFILES[STRATEGY]
    position = build_position(profile)
    report = sim(
        position,
        resample="M",                      # rebalance monthly
        position_limit=1,                  # max weight per asset (number of holdings
                                           # is set by is_largest(...) above)
        stop_loss=profile["stop_loss"],    # touched stop on the held ETF
        touched_exit=True,
        trade_at_price="close",
        upload=False,
    )
    print(report.get_stats())
    report.display()


if __name__ == "__main__":
    main()
