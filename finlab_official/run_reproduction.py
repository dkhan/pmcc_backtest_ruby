#!/usr/bin/env python3
"""Run the downloaded FinLab strategies over the paper's fixed window."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import finlab
from finlab.backtest import sim

import strategy


PAPER_END = "2026-06-12"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", choices=tuple(strategy.PROFILES))
    parser.add_argument("--end-date", default=PAPER_END)
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()

    finlab.login()
    profile = strategy.PROFILES[args.profile]
    position = strategy.build_position(profile).loc[: args.end_date]
    report = sim(
        position,
        resample="M",
        position_limit=1,
        stop_loss=profile["stop_loss"],
        touched_exit=True,
        trade_at_price="close",
        upload=False,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stats = report.get_stats()
    stats_path = args.output_dir / f"{args.profile}_stats.json"
    report_path = args.output_dir / f"{args.profile}_report.html"
    stats_path.write_text(json.dumps(stats, indent=2, default=str) + "\n")
    report.to_html(str(report_path))

    print(f"Profile          : {args.profile}")
    print(f"Period           : {stats['start']} to {stats['end']}")
    print(f"Total return     : {stats['total_return']:.4%}")
    print(f"CAGR             : {stats['cagr']:.4%}")
    print(f"Max drawdown     : {stats['max_drawdown']:.4%}")
    print(f"Monthly Sharpe   : {stats['monthly_sharpe']:.4f}")
    print(f"Stats            : {stats_path}")
    print(f"HTML report      : {report_path}")


if __name__ == "__main__":
    main()
