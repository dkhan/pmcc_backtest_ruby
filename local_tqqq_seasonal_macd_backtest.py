#!/usr/bin/env python3
"""Backtest Les Masonson's TQQQ seasonality + MACD timing idea.

The video first describes the Stock Trader's Almanac "best six months" concept,
then its Nasdaq-specific "best eight months" variant. Signals are calculated on
QQQ; the position is held in TQQQ and otherwise remains in cash. Orders execute
at the next session's open so the test never trades on information from the
same close.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass(frozen=True)
class Config:
    initial_cash: float = 100_000.0
    start_date: date = date(2010, 2, 11)
    end_date: Optional[date] = None
    cost_bps: float = 5.0
    fast: int = 12
    slow: int = 26
    signal: int = 9
    exit_month: int = 7


def load_symbol(data_dir: Path, symbol: str) -> pd.DataFrame:
    path = data_dir / f"{symbol.lower()}_daily.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run download_rotation_etfs.py")
    frame = pd.read_parquet(path)
    frame.columns = [str(column).lower() for column in frame.columns]
    required = {"date", "open", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
    frame = frame.set_index("date").sort_index()
    return frame


def build_signals(
    qqq_close: pd.Series, fast: int, slow: int, signal: int, exit_month: int = 7
) -> pd.DataFrame:
    """Return close-known MACD events and the desired position state.

    One entry is allowed per seasonal cycle: the first bullish crossover on or
    after October 1. The next exit is the first bearish crossover on or after
    the configured exit month. This is a seasonal gate, not ordinary MACD trading.
    """
    ema_fast = qqq_close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = qqq_close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False, min_periods=signal).mean()
    bullish = (macd > signal_line) & (macd.shift(1) <= signal_line.shift(1))
    bearish = (macd < signal_line) & (macd.shift(1) >= signal_line.shift(1))

    desired = False
    armed_entry = False
    armed_exit = False
    states: list[bool] = []
    events: list[str] = []
    previous_day: Optional[pd.Timestamp] = None
    for day in qqq_close.index:
        # Arm on the first available session of the relevant month because the
        # first calendar day is sometimes a weekend or market holiday.
        if day.month == 10 and (previous_day is None or previous_day.month != 10):
            armed_entry = True
        elif day.month == exit_month and (
            previous_day is None or previous_day.month != exit_month
        ):
            armed_exit = True

        event = ""
        if not desired and armed_entry and bool(bullish.at[day]):
            desired = True
            armed_entry = False
            armed_exit = False
            event = "BULLISH_MACD_AFTER_OCT_1"
        elif desired and armed_exit and bool(bearish.at[day]):
            desired = False
            armed_exit = False
            armed_entry = False
            gate = pd.Timestamp(2000, exit_month, 1).strftime("%b").upper()
            event = f"BEARISH_MACD_AFTER_{gate}_1"
        states.append(desired)
        events.append(event)
        previous_day = day

    return pd.DataFrame(
        {
            "qqq_close": qqq_close,
            "macd": macd,
            "signal_line": signal_line,
            "bullish_cross": bullish,
            "bearish_cross": bearish,
            "desired_long": states,
            "event": events,
        },
        index=qqq_close.index,
    )


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def statistics(equity: pd.Series, initial_cash: float) -> dict[str, float]:
    years = (equity.index[-1] - equity.index[0]).days / 365.2425
    daily = equity.pct_change().dropna()
    volatility = float(daily.std() * math.sqrt(252)) if len(daily) else 0.0
    sharpe = (
        float(daily.mean() / daily.std() * math.sqrt(252))
        if len(daily) and daily.std() > 0
        else 0.0
    )
    return {
        "ending": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / initial_cash - 1.0),
        "cagr": float((equity.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0),
        "max_drawdown": max_drawdown(equity),
        "volatility": volatility,
        "sharpe": sharpe,
    }


def run_backtest(
    qqq: pd.DataFrame, tqqq: pd.DataFrame, config: Config
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, float]]]:
    common = qqq.index.intersection(tqqq.index)
    if config.end_date:
        common = common[common <= pd.Timestamp(config.end_date)]
    common = common[common >= pd.Timestamp(config.start_date)]
    if len(common) < config.slow + config.signal + 2:
        raise ValueError("Date range is too short for the requested MACD settings")

    # Preserve pre-start history so indicators are warm on the requested date.
    signals = build_signals(
        qqq["close"], config.fast, config.slow, config.signal, config.exit_month
    )
    cost_rate = config.cost_bps / 10_000.0
    cash = config.initial_cash
    shares = 0.0
    trades: list[dict] = []
    rows: list[dict] = []

    for i, day in enumerate(common):
        prior = common[i - 1] if i else None
        if prior is not None:
            want_long = bool(signals.at[prior, "desired_long"])
            is_long = shares > 0
            if want_long != is_long:
                price = float(tqqq.at[day, "open"])
                event = str(signals.at[prior, "event"]) or "INITIAL_STATE_SYNC"
                if want_long:
                    shares = cash / (price * (1.0 + cost_rate))
                    cost = shares * price * cost_rate
                    cash -= shares * price + cost
                    action = "BUY"
                else:
                    cost = shares * price * cost_rate
                    cash += shares * price - cost
                    shares = 0.0
                    action = "SELL"
                trades.append(
                    {
                        "signal_date": prior.date().isoformat(),
                        "date": day.date().isoformat(),
                        "action": action,
                        "price": price,
                        "cost": cost,
                        "reason": event,
                    }
                )
        equity = cash + shares * float(tqqq.at[day, "close"])
        rows.append(
            {
                "date": day,
                "equity": equity,
                "holding": "TQQQ" if shares else "CASH",
                "shares": shares,
                "cash": cash,
                "tqqq_close": float(tqqq.at[day, "close"]),
                "qqq_close": float(qqq.at[day, "close"]),
                "macd": float(signals.at[day, "macd"]),
                "signal_line": float(signals.at[day, "signal_line"]),
            }
        )

    daily = pd.DataFrame(rows).set_index("date")
    trade_frame = pd.DataFrame(trades)
    first_tqqq = float(tqqq.at[common[0], "close"])
    benchmark = config.initial_cash * tqqq.loc[common, "close"] / first_tqqq
    stats = {
        "strategy": statistics(daily["equity"], config.initial_cash),
        "tqqq_buy_hold": statistics(benchmark, config.initial_cash),
    }
    return daily, trade_frame, stats


def write_csv(frame: pd.DataFrame, path: Path, include_index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=include_index, quoting=csv.QUOTE_MINIMAL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/etfs"))
    parser.add_argument("--output-dir", type=Path, default=Path("results/tqqq_seasonal_macd"))
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2010, 2, 11))
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    parser.add_argument("--fast", type=int, default=12)
    parser.add_argument("--slow", type=int, default=26)
    parser.add_argument("--signal", type=int, default=9)
    parser.add_argument(
        "--window",
        choices=("nasdaq-eight", "broad-six"),
        default="nasdaq-eight",
        help="July exit gate for Nasdaq (default), or April for the broad market",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = Config(
        initial_cash=args.initial_cash,
        start_date=args.start_date,
        end_date=args.end_date,
        cost_bps=args.cost_bps,
        fast=args.fast,
        slow=args.slow,
        signal=args.signal,
        exit_month=7 if args.window == "nasdaq-eight" else 4,
    )
    qqq = load_symbol(args.data_dir, "QQQ")
    tqqq = load_symbol(args.data_dir, "TQQQ")
    daily, trades, stats = run_backtest(qqq, tqqq, config)
    write_csv(daily.reset_index(), args.output_dir / "daily_equity.csv")
    write_csv(trades, args.output_dir / "trades.csv")

    print("=== TQQQ SEASONAL MACD (QQQ SIGNAL) ===")
    print(f"Period           : {daily.index[0].date()} to {daily.index[-1].date()}")
    print(f"MACD             : {config.fast}/{config.slow}/{config.signal}")
    print(
        "Seasonal window  : "
        + ("Nasdaq eight months" if config.exit_month == 7 else "Broad six months")
    )
    print(f"Trading costs    : {config.cost_bps:.2f} bps per side")
    for label, key in (("Strategy", "strategy"), ("TQQQ buy/hold", "tqqq_buy_hold")):
        s = stats[key]
        print(
            f"{label:17}: end ${s['ending']:,.2f}, CAGR {s['cagr']:.2%}, "
            f"max DD {s['max_drawdown']:.2%}, Sharpe {s['sharpe']:.2f}"
        )
    print(f"Round trips      : {len(trades) // 2}")
    print(f"Daily equity CSV : {args.output_dir / 'daily_equity.csv'}")
    print(f"Trades CSV       : {args.output_dir / 'trades.csv'}")


if __name__ == "__main__":
    main()
