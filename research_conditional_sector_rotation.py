#!/usr/bin/env python3
"""Run robustness diagnostics for the local Conditional Sector Rotation model."""

from __future__ import annotations

import argparse
from dataclasses import fields, replace
from pathlib import Path

import pandas as pd

from local_conditional_sector_rotation import StrategyConfig, load_prices, performance, run_case


def add_case(rows, prices, name, cfg, category):
    stats, daily, trades = run_case(prices, name, cfg)
    rows.append({"category": category, **stats})
    return daily, trades


def markdown_table(frame: pd.DataFrame, columns: list[str]) -> str:
    shown = frame[columns].copy()
    for column in ("cagr", "max_drawdown", "volatility"):
        if column in shown:
            shown[column] = shown[column].map(lambda value: f"{value:.1%}")
    for column in ("ending_equity", "fees"):
        if column in shown:
            shown[column] = shown[column].map(lambda value: f"${value:,.0f}")
    if "sharpe" in shown:
        shown["sharpe"] = shown["sharpe"].map(lambda value: f"{value:.2f}")
    headers = list(shown.columns)
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in shown.itertuples(index=False, name=None))
    return "\n".join(lines)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data/conditional_sector_rotation")
    parser.add_argument("--output-dir", type=Path, default=root / "results/conditional_sector_rotation_research")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    prices = load_prices(args.data_dir)
    base = StrategyConfig()
    rows = []
    baseline_daily, baseline_trades = add_case(rows, prices, "baseline", base, "baseline")
    baseline_dates = pd.DatetimeIndex(baseline_daily.date)
    for symbol in ("SPY", "QQQ", "TQQQ"):
        close = prices[symbol].reindex(baseline_dates)["close"]
        equity = close / close.iloc[0] * base.initial_cash
        benchmark_daily = pd.DataFrame({"date": baseline_dates, "equity": equity.to_numpy()})
        rows.append({"category": "benchmark", "case": f"{symbol}_buy_hold", **performance(benchmark_daily, base.initial_cash), "orders": 1, "entries": 1, "fees": 0.0})

    threshold_grid = {
        "rsi_period": [7, 10, 14],
        "spy_sma_period": [150, 175, 200, 225, 250],
        "qqq_sma_period": [15, 20, 25, 30],
        "tqqq_sma_period": [15, 20, 25, 30],
        "qqq_overbought": [78.0, 80.0, 81.0, 82.0, 84.0],
        "spy_overbought": [76.0, 78.0, 80.0, 82.0, 84.0],
        "oversold": [25.0, 28.0, 30.0, 32.0, 35.0],
        "uvxy_trigger": [70.0, 72.0, 74.0, 76.0, 78.0],
        "uvxy_extreme": [80.0, 82.0, 84.0, 86.0, 88.0],
        "sqqq_extreme": [27.0, 29.0, 31.0, 33.0, 35.0],
        "sqqq_trend": [30.0, 32.0, 34.0, 36.0, 38.0],
    }
    valid = {field.name for field in fields(base)}
    for parameter, values in threshold_grid.items():
        assert parameter in valid
        for value in values:
            if value == getattr(base, parameter):
                continue
            add_case(rows, prices, f"{parameter}={value:g}", replace(base, **{parameter: value}), "parameter_sensitivity")

    for allocation in (0.50, 0.75, 0.90, 0.95):
        if allocation != base.allocation:
            add_case(rows, prices, f"allocation={allocation:.0%}", replace(base, allocation=allocation), "allocation")
    for cost in (0.0, 5.0, 10.0, 25.0, 50.0):
        if cost != base.cost_bps:
            add_case(rows, prices, f"cost={cost:g}bps", replace(base, cost_bps=cost), "execution_cost")
    for delay in (1, 2):
        add_case(rows, prices, f"delay={delay}d", replace(base, execution_delay_days=delay), "execution_delay")
    for variant in ("no_uvxy", "no_oversold_dip", "no_inverse", "simple_regime"):
        add_case(rows, prices, variant, replace(base, variant=variant), "branch_ablation")
    for confirmation, minimum_hold in ((2, 0), (3, 0), (1, 2), (1, 3), (2, 2), (2, 3)):
        add_case(rows, prices, f"confirm={confirmation},hold={minimum_hold}", replace(base, confirmation_days=confirmation, minimum_hold_days=minimum_hold), "hysteresis")

    last_date = min(frame.index.max() for frame in prices.values()).date()
    periods = (("2012-2017", "2012-01-01", "2017-12-31"), ("2018-2021", "2018-01-01", "2021-12-31"), ("2022-latest", "2022-01-01", str(last_date)))
    for name, start, end in periods:
        add_case(rows, prices, name, replace(base, start_date=pd.Timestamp(start).date(), end_date=pd.Timestamp(end).date()), "chronological_holdout")

    results = pd.DataFrame(rows)
    results.to_csv(args.output_dir / "robustness_cases.csv", index=False)
    baseline_daily.to_csv(args.output_dir / "baseline_daily_equity.csv", index=False)
    baseline_trades.to_csv(args.output_dir / "baseline_orders.csv", index=False)

    attributed = baseline_daily.copy()
    attributed["pnl"] = attributed.equity.diff().fillna(0.0)
    branch = attributed.groupby(["branch", "holding"], as_index=False).agg(days=("date", "size"), pnl=("pnl", "sum"))
    entries = baseline_trades[baseline_trades.action == "BUY"].groupby(["branch", "symbol"]).size().rename("entries").reset_index()
    branch = branch.merge(entries, left_on=["branch", "holding"], right_on=["branch", "symbol"], how="left").drop(columns="symbol")
    branch["entries"] = branch.entries.fillna(0).astype(int)
    branch.sort_values("pnl", ascending=False).to_csv(args.output_dir / "branch_attribution.csv", index=False)

    report = ["# Conditional Sector Rotation robustness report", "", "All signals use adjusted closes through the prior session; trades execute at the next session open. The baseline uses 95% allocation and 5 bps cost per side.", ""]
    columns = ["case", "ending_equity", "cagr", "max_drawdown", "volatility", "sharpe", "entries", "fees"]
    for category in ("baseline", "benchmark", "chronological_holdout", "allocation", "execution_cost", "execution_delay", "branch_ablation", "hysteresis"):
        report.extend([f"## {category.replace('_', ' ').title()}", "", markdown_table(results[results.category == category], columns), ""])
    sensitivity = results[results.category == "parameter_sensitivity"]
    report.extend(["## Parameter sensitivity ranges", "", "This summarizes one-parameter-at-a-time perturbations; it is not an optimization search.", ""])
    sensitivity_summary = sensitivity.assign(parameter=sensitivity.case.str.split("=").str[0]).groupby("parameter", as_index=False).agg(cases=("case", "size"), min_cagr=("cagr", "min"), median_cagr=("cagr", "median"), max_cagr=("cagr", "max"), worst_drawdown=("max_drawdown", "min"), min_sharpe=("sharpe", "min"), max_sharpe=("sharpe", "max"))
    report.append(markdown_table(sensitivity_summary, list(sensitivity_summary.columns)))
    report.extend(["", "## Branch attribution", "", markdown_table(branch.sort_values("pnl", ascending=False), ["branch", "holding", "days", "entries", "pnl"]), "", "## Interpretation limits", "", "The chronological slices are pseudo-out-of-sample diagnostics, not genuinely unseen data, because the original rules were already known before this test. Yahoo adjusted daily data and next-open fills will not exactly reproduce QuantConnect's minute fills. Results exclude taxes, market impact, borrow constraints, and live-order failures."])
    (args.output_dir / "REPORT.md").write_text("\n".join(report) + "\n")
    print(f"Wrote {len(results)} cases to {args.output_dir}")
    print(results[results.category != "parameter_sensitivity"][columns].to_string(index=False))


if __name__ == "__main__":
    main()
