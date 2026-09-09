#!/usr/bin/env python3
"""Compare current-session CSR signals at one-hour decision intervals.

Yahoo hourly bars are labeled by their start. For a 15:30 decision, the signal
uses the completed 14:30-15:30 bar and execution uses the 15:30 bar open. The
09:30 case uses the opening print because no regular-session bar is complete.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import time
from pathlib import Path

import pandas as pd

from local_conditional_sector_rotation import SYMBOLS, StrategyConfig, load_prices
from research_conditional_sector_rotation_5min_current import (
    common_sessions,
    run_time,
)


TIMES = tuple(time(hour, 30) for hour in range(9, 16))


def load_hourly(data_dir: Path) -> dict[str, pd.DataFrame]:
    result = {}
    for symbol in SYMBOLS:
        path = data_dir / f"{symbol.lower()}_hourly.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}; run download_conditional_sector_rotation_intraday.py"
            )
        frame = pd.read_parquet(path)
        stamp = pd.to_datetime(frame.pop("datetime"), utc=True).dt.tz_convert(
            "America/New_York"
        )
        frame.index = pd.DatetimeIndex(stamp)
        result[symbol] = frame.astype(float).sort_index()
    return result


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--daily-data",
        type=Path,
        default=root / "data/conditional_sector_rotation",
    )
    parser.add_argument(
        "--intraday-data",
        type=Path,
        default=root / "data/conditional_sector_rotation_hourly",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "results/conditional_sector_rotation_hourly_current",
    )
    parser.add_argument("--cost-bps", type=float, default=StrategyConfig.cost_bps)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    daily_prices = load_prices(args.daily_data)
    intraday = load_hourly(args.intraday_data)
    sessions = common_sessions(intraday)
    last_daily = min(frame.index.max() for frame in daily_prices.values()).date()
    cfg = replace(
        StrategyConfig(),
        start_date=sessions[0].date(),
        end_date=last_daily,
        cost_bps=args.cost_bps,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for clock in TIMES:
        stats, daily, orders = run_time(daily_prices, intraday, clock, cfg)
        rows.append(stats)
        suffix = clock.strftime("%H%M")
        daily.to_csv(args.output_dir / f"equity_{suffix}.csv", index=False)
        orders.to_csv(args.output_dir / f"orders_{suffix}.csv", index=False)

    summary = pd.DataFrame(rows).sort_values("time").reset_index(drop=True)
    reference = summary.loc[summary.time == "15:30"].iloc[0]
    summary["ending_vs_1530"] = summary.ending_equity / reference.ending_equity - 1
    summary["cagr_vs_1530"] = summary.cagr - reference.cagr
    summary.to_csv(args.output_dir / "timing_summary.csv", index=False)
    print(summary.sort_values("ending_equity", ascending=False).to_string(index=False))
    print(f"\nWrote {len(summary)} hourly cases to {args.output_dir}")


if __name__ == "__main__":
    main()
