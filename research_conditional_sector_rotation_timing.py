#!/usr/bin/env python3
"""Test how Conditional Sector Rotation changes with morning execution time."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import time
from pathlib import Path

import pandas as pd

from local_conditional_sector_rotation import (
    SYMBOLS,
    StrategyConfig,
    build_indicators,
    build_signals,
    load_prices,
    performance,
    stabilized_targets,
)


TIMES = (time(9, 30), time(10, 30), time(11, 30))


def load_hourly(data_dir: Path) -> dict[str, pd.DataFrame]:
    result = {}
    for symbol in SYMBOLS:
        frame = pd.read_parquet(data_dir / f"{symbol.lower()}_hourly.parquet")
        stamp = pd.to_datetime(frame.pop("datetime"), utc=True).dt.tz_convert("America/New_York")
        frame.index = pd.DatetimeIndex(stamp)
        result[symbol] = frame.astype(float)
    return result


def price_at(frame: pd.DataFrame, day: pd.Timestamp, clock: time) -> float:
    local_day = frame.index.tz_localize(None).normalize() == day
    bars = frame.loc[local_day]
    matches = bars[(bars.index.hour == clock.hour) & (bars.index.minute == clock.minute)]
    if matches.empty:
        raise KeyError(f"No {clock} bar on {day.date()}")
    return float(matches.iloc[0].open)


def run_timing(daily_prices, hourly, clock, cfg):
    indicators = build_indicators(daily_prices, cfg)
    signals = stabilized_targets(build_signals(indicators, cfg), cfg)
    hourly_dates = set(hourly["SPY"].index.tz_localize(None).normalize())
    common_daily = indicators.index
    for symbol in SYMBOLS:
        symbol_dates = set(hourly[symbol].index.tz_localize(None).normalize())
        hourly_dates &= symbol_dates
    days = pd.DatetimeIndex(sorted(hourly_dates))
    days = days[(days >= pd.Timestamp(cfg.start_date)) & (days <= common_daily[-1])]
    cash, shares, holding = cfg.initial_cash, 0, None
    cost_rate = cfg.cost_bps / 10_000
    rows, orders = [], []
    for day in days:
        prior = common_daily[common_daily < day]
        if not len(prior) or prior[-1] not in signals.index:
            continue
        target = str(signals.at[prior[-1], "target"])
        branch = str(signals.at[prior[-1], "branch"])
        if target != holding:
            if holding is not None:
                px = price_at(hourly[holding], day, clock)
                gross = shares * px
                fee = gross * cost_rate
                cash += gross - fee
                orders.append({"date": day, "time": clock.strftime("%H:%M"), "action": "SELL", "symbol": holding, "price": px, "shares": shares, "fee": fee, "branch": branch})
            px = price_at(hourly[target], day, clock)
            shares = math.floor(cash * cfg.allocation / (px * (1 + cost_rate)))
            gross = shares * px
            fee = gross * cost_rate
            cash -= gross + fee
            holding = target
            orders.append({"date": day, "time": clock.strftime("%H:%M"), "action": "BUY", "symbol": holding, "price": px, "shares": shares, "fee": fee, "branch": branch})
        equity = cash + shares * float(daily_prices[holding].at[day, "close"])
        rows.append({"date": day, "equity": equity, "holding": holding})
    daily = pd.DataFrame(rows)
    stats = performance(daily, cfg.initial_cash)
    stats.update(time=clock.strftime("%H:%M"), sessions=len(daily), entries=sum(row["action"] == "BUY" for row in orders), fees=sum(row["fee"] for row in orders))
    return stats, daily, pd.DataFrame(orders)


def main() -> None:
    root = Path(__file__).resolve().parent
    daily_prices = load_prices(root / "data" / "conditional_sector_rotation")
    hourly = load_hourly(root / "data" / "conditional_sector_rotation_hourly")
    first = max(frame.index.tz_localize(None).normalize().min() for frame in hourly.values())
    cfg = replace(StrategyConfig(), start_date=first.date())
    output = root / "results" / "conditional_sector_rotation_timing"
    output.mkdir(parents=True, exist_ok=True)
    stats_rows = []
    for clock in TIMES:
        stats, daily, orders = run_timing(daily_prices, hourly, clock, cfg)
        stats_rows.append(stats)
        daily.to_csv(output / f"equity_{clock.strftime('%H%M')}.csv", index=False)
        orders.to_csv(output / f"orders_{clock.strftime('%H%M')}.csv", index=False)
    results = pd.DataFrame(stats_rows)
    baseline = results.iloc[0]
    results["ending_vs_0930"] = results.ending_equity / baseline.ending_equity - 1
    results["cagr_difference"] = results.cagr - baseline.cagr
    results.to_csv(output / "timing_summary.csv", index=False)
    print(results.to_string(index=False))


if __name__ == "__main__":
    main()
