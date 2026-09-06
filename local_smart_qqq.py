#!/usr/bin/env python3
"""Local daily-bar reproduction of QuantConnect's public RSI Rebalance strategy."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


SYMBOLS = ("QQQ", "TQQQ", "SPY", "GLD", "XLU", "UVXY")


@dataclass(frozen=True)
class Config:
    start_date: str = "2016-01-01"
    end_date: str | None = None
    initial_cash: float = 10_000.0
    cost_bps: float = 0.0


def load_closes(data_dir: Path) -> pd.DataFrame:
    series = {}
    for symbol in SYMBOLS:
        path = data_dir / f"{symbol.lower()}_daily.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; run download_rotation_etfs.py")
        frame = pd.read_parquet(path)
        frame.columns = [str(c).lower() for c in frame.columns]
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame = frame.set_index("date").sort_index()
        series[symbol] = frame["close"].astype(float)
    return pd.concat(series, axis=1).sort_index().ffill()


def qc_wilder_rsi(values: pd.Series, period: int = 10, extension: int = 50) -> float:
    """Match the custom Wilder RSI calculation in the published QC script."""
    values = values.dropna().iloc[-extension:]
    if len(values) < extension:
        return math.nan
    changes = values.diff().dropna()
    seed = changes.iloc[:period]
    avg_gain = seed.clip(lower=0).mean()
    avg_loss = (-seed.clip(upper=0)).mean()
    for change in changes.iloc[period:]:
        gain = max(float(change), 0.0)
        loss = max(float(-change), 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def choose_target(rsi: float, spy: float, sma30: float, sma200: float) -> str | None:
    if any(math.isnan(x) for x in (rsi, spy, sma30, sma200)):
        return None
    if rsi > 79:
        return "UVXY"
    if rsi < 31:
        return "TQQQ"
    if spy > sma200:
        return "QQQ" if spy > sma30 else "XLU"
    return "QQQ" if spy > sma30 else "GLD"


def build_signals(close: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=close.index)
    out["rsi10"] = close["QQQ"].rolling(50).apply(qc_wilder_rsi, raw=False)
    out["spy_sma30"] = close["SPY"].rolling(30).mean()
    out["spy_sma200"] = close["SPY"].rolling(200).mean()
    out["target"] = [
        choose_target(rsi, spy, sma30, sma200)
        for rsi, spy, sma30, sma200 in zip(
            out["rsi10"], close["SPY"], out["spy_sma30"], out["spy_sma200"]
        )
    ]
    return out


def metrics(equity: pd.Series) -> dict[str, float]:
    returns = equity.pct_change().dropna()
    years = (equity.index[-1] - equity.index[0]).days / 365.2425
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1
    drawdown = equity / equity.cummax() - 1
    volatility = returns.std(ddof=1) * math.sqrt(252)
    sharpe = returns.mean() / returns.std(ddof=1) * math.sqrt(252)
    return {"cagr": cagr, "max_drawdown": drawdown.min(), "volatility": volatility, "sharpe": sharpe}


def run_backtest(close: pd.DataFrame, config: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    signals = build_signals(close)
    start = pd.Timestamp(config.start_date)
    end = pd.Timestamp(config.end_date) if config.end_date else close.dropna().index.max()
    dates = close.index[(close.index >= start) & (close.index <= end)]
    if len(dates) < 2:
        raise ValueError("Requested period has fewer than two sessions")

    equity = config.initial_cash
    held: str | None = None
    rows, trades = [], []
    for i, day in enumerate(dates):
        if i and held is not None:
            previous = dates[i - 1]
            equity *= close.at[day, held] / close.at[previous, held]

        target = signals.at[day, "target"]
        if target is not None and target != held:
            sides = 1 if held is None else 2
            fee = equity * (config.cost_bps / 10_000.0) * sides
            equity -= fee
            trades.append({"date": day, "from_symbol": held or "CASH", "to_symbol": target, "fee": fee})
            held = target
        rows.append({
            "date": day,
            "equity": equity,
            "holding": held or "CASH",
            "rsi10": signals.at[day, "rsi10"],
            "spy_close": close.at[day, "SPY"],
            "spy_sma30": signals.at[day, "spy_sma30"],
            "spy_sma200": signals.at[day, "spy_sma200"],
        })
    return pd.DataFrame(rows).set_index("date"), pd.DataFrame(trades)


def benchmark(close: pd.Series, dates: pd.DatetimeIndex, initial_cash: float) -> pd.Series:
    values = close.reindex(dates)
    return initial_cash * values / values.iloc[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/etfs"))
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date")
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    parser.add_argument("--output-dir", type=Path, default=Path("results/qc_rsi_rotation_2016"))
    args = parser.parse_args()
    config = Config(args.start_date, args.end_date, args.initial_cash, args.cost_bps)
    close = load_closes(args.data_dir)
    daily, trades = run_backtest(close, config)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.output_dir / "daily_equity.csv")
    trades.to_csv(args.output_dir / "trades.csv", index=False)

    result = metrics(daily["equity"])
    print("\n=== QUANTCONNECT RSI ROTATION (LOCAL DAILY) ===")
    print(f"Period           : {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"Ending equity    : ${daily['equity'].iloc[-1]:,.2f}")
    print(f"CAGR             : {result['cagr']:.2%}")
    print(f"Max drawdown     : {result['max_drawdown']:.2%}")
    print(f"Volatility       : {result['volatility']:.2%}")
    print(f"Daily Sharpe     : {result['sharpe']:.2f}")
    print(f"Target changes   : {len(trades):,}")
    print(f"Trading costs    : {args.cost_bps:.2f} bps per side")
    for symbol in ("QQQ", "TQQQ"):
        b = benchmark(close[symbol], daily.index, config.initial_cash)
        m = metrics(b)
        print(f"{symbol} buy/hold     : CAGR {m['cagr']:.2%}, max DD {m['max_drawdown']:.2%}")
    print(f"Daily equity CSV : {args.output_dir / 'daily_equity.csv'}")
    print(f"Trades CSV       : {args.output_dir / 'trades.csv'}")


if __name__ == "__main__":
    main()
