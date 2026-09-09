#!/usr/bin/env python3
"""Local, auditable implementation of the QC Conditional Sector Rotation tree."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
import numpy as np


SYMBOLS = ("SPY", "QQQ", "TQQQ", "UVXY", "TECL", "SPXL", "SQQQ", "TECS", "BSV")
TARGETS = ("TQQQ", "UVXY", "TECL", "SPXL", "TECS", "BSV")


@dataclass(frozen=True)
class StrategyConfig:
    initial_cash: float = 25_000.0
    allocation: float = 0.95
    start_date: date = date(2021, 1, 1)
    end_date: Optional[date] = None
    rsi_period: int = 10
    spy_sma_period: int = 200
    qqq_sma_period: int = 20
    tqqq_sma_period: int = 20
    qqq_overbought: float = 81.0
    spy_overbought: float = 80.0
    oversold: float = 30.0
    uvxy_trigger: float = 74.0
    uvxy_extreme: float = 84.0
    sqqq_extreme: float = 31.0
    sqqq_trend: float = 34.0
    cost_bps: float = 5.0
    execution_delay_days: int = 0
    confirmation_days: int = 1
    minimum_hold_days: int = 0
    variant: str = "baseline"


def load_prices(data_dir: Path) -> dict[str, pd.DataFrame]:
    prices: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        path = data_dir / f"{symbol.lower()}_daily.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; run download_conditional_sector_rotation_data.py")
        frame = pd.read_parquet(path)
        frame.columns = [str(column).lower() for column in frame.columns]
        required = {"date", "open", "high", "low", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        frame["date"] = pd.to_datetime(frame["date"]).dt.tz_localize(None).dt.normalize()
        frame = frame.set_index("date").sort_index()
        if frame.index.has_duplicates:
            raise ValueError(f"{path} contains duplicate dates")
        prices[symbol] = frame[["open", "high", "low", "close"]].astype(float)
    return prices


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder RSI seeded with the arithmetic mean of the first period."""
    values = close.to_numpy(dtype=float)
    result = np.full(len(values), np.nan)
    if len(values) <= period:
        return pd.Series(result, index=close.index)
    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())
    result[period] = _rsi_value(average_gain, average_loss)
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result[index + 1] = _rsi_value(average_gain, average_loss)
    return pd.Series(result, index=close.index)


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def build_indicators(prices: dict[str, pd.DataFrame], cfg: StrategyConfig) -> pd.DataFrame:
    common = prices["SPY"].index
    for symbol in SYMBOLS[1:]:
        common = common.intersection(prices[symbol].index)
    out = pd.DataFrame(index=common)
    for symbol in SYMBOLS:
        # Calculate on each symbol's complete history before aligning calendars.
        # This matches QC warm-up behavior: SPY's 200-day SMA can be ready even
        # when a younger symbol such as UVXY has only enough bars for RSI(10).
        close = prices[symbol]["close"]
        out[f"{symbol}_close"] = close.reindex(common)
        out[f"{symbol}_rsi"] = wilder_rsi(close, cfg.rsi_period).reindex(common)
    out["SPY_sma"] = prices["SPY"]["close"].rolling(cfg.spy_sma_period).mean().reindex(common)
    out["QQQ_sma"] = prices["QQQ"]["close"].rolling(cfg.qqq_sma_period).mean().reindex(common)
    out["TQQQ_sma"] = prices["TQQQ"]["close"].rolling(cfg.tqqq_sma_period).mean().reindex(common)
    return out


def choose_target(row: pd.Series, cfg: StrategyConfig) -> tuple[str, str]:
    """Return target and the first QC branch that selected it."""
    if cfg.variant == "simple_regime":
        return ("TQQQ", "simple_bull") if row.SPY_close > row.SPY_sma else ("BSV", "simple_defensive")

    bull = row.SPY_close > row.SPY_sma
    if bull:
        target, branch = (("UVXY", "bull_overbought") if row.QQQ_rsi > cfg.qqq_overbought or row.SPY_rsi > cfg.spy_overbought else ("TQQQ", "bull_trend"))
    elif cfg.variant != "no_oversold_dip" and row.TQQQ_rsi < cfg.oversold:
        target, branch = "TECL", "bear_tqqq_oversold"
    elif cfg.variant != "no_oversold_dip" and row.SPY_rsi < cfg.oversold:
        target, branch = "SPXL", "bear_spy_oversold"
    elif row.UVXY_rsi > cfg.uvxy_trigger:
        if row.UVXY_rsi <= cfg.uvxy_extreme:
            target, branch = "UVXY", "bear_uvxy_momentum"
        elif row.QQQ_close > row.QQQ_sma:
            target, branch = (("TECS", "bear_extreme_inverse") if row.SQQQ_rsi < cfg.sqqq_extreme else ("TECL", "bear_extreme_long"))
        else:
            target, branch = (("TECS", "bear_relative_inverse") if row.TECS_rsi >= row.BSV_rsi else ("BSV", "bear_relative_bond"))
    elif row.TQQQ_close > row.TQQQ_sma:
        target, branch = (("TECS", "bear_trend_inverse") if row.SQQQ_rsi < cfg.sqqq_trend else ("TECL", "bear_trend_long"))
    else:
        target, branch = (("TECS", "bear_relative_inverse") if row.TECS_rsi >= row.BSV_rsi else ("BSV", "bear_relative_bond"))

    if cfg.variant == "no_uvxy" and target == "UVXY":
        return ("TQQQ", "uvxy_replaced_bull") if bull else ("BSV", "uvxy_replaced_defensive")
    if cfg.variant == "no_inverse" and target == "TECS":
        return "BSV", "inverse_replaced_defensive"
    return target, branch


def build_signals(indicators: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    required = ["SPY_sma", "QQQ_sma", "TQQQ_sma"] + [f"{s}_rsi" for s in ("SPY", "QQQ", "TQQQ", "UVXY", "SQQQ", "TECS", "BSV")]
    ready = indicators[required].notna().all(axis=1)
    records = []
    for day, row in indicators.loc[ready].iterrows():
        target, branch = choose_target(row, cfg)
        records.append((day, target, branch))
    return pd.DataFrame(records, columns=["date", "raw_target", "branch"]).set_index("date")


def stabilized_targets(signals: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Temporal confirmation plus minimum holding time acts as hysteresis."""
    result = signals.copy()
    held: Optional[str] = None
    candidate: Optional[str] = None
    streak = 0
    held_days = 0
    active = []
    for raw in result["raw_target"]:
        held_days += 1
        if raw == held:
            candidate, streak = None, 0
        elif raw == candidate:
            streak += 1
        else:
            candidate, streak = str(raw), 1
        if held is None or (streak >= cfg.confirmation_days and held_days >= cfg.minimum_hold_days):
            held = candidate
            candidate, streak, held_days = None, 0, 0
        active.append(held)
    result["target"] = active
    return result


def run_backtest(prices: dict[str, pd.DataFrame], cfg: StrategyConfig) -> tuple[pd.DataFrame, pd.DataFrame]:
    if not 0 < cfg.allocation <= 1 or cfg.confirmation_days < 1 or cfg.execution_delay_days < 0:
        raise ValueError("Invalid allocation, confirmation, or delay setting")
    indicators = build_indicators(prices, cfg)
    signals = stabilized_targets(build_signals(indicators, cfg), cfg)
    common = indicators.index
    start, end = pd.Timestamp(cfg.start_date), pd.Timestamp(cfg.end_date) if cfg.end_date else common[-1]
    days = common[(common >= start) & (common <= end)]
    cash, shares, holding, holding_branch = cfg.initial_cash, 0.0, None, "cash"
    cost_rate = cfg.cost_bps / 10_000.0
    pending: list[tuple[int, str, str, pd.Timestamp]] = []
    daily, trades = [], []

    for i, day in enumerate(days):
        prior = common[common < day]
        if len(prior) and prior[-1] in signals.index:
            signal = signals.loc[prior[-1]]
            if signal.target != holding and not any(item[1] == signal.target for item in pending):
                pending.append((i + cfg.execution_delay_days, str(signal.target), str(signal.branch), prior[-1]))
        due = [item for item in pending if item[0] <= i]
        if due:
            _, target, branch, signal_day = due[-1]
            pending.clear()
            if holding != target:
                if holding is not None:
                    price = float(prices[holding].at[day, "open"])
                    gross = shares * price
                    fee = gross * cost_rate
                    cash += gross - fee
                    trades.append({"date": day, "signal_date": signal_day, "action": "SELL", "symbol": holding, "price": price, "shares": shares, "fee": fee, "branch": branch})
                equity_at_open = cash
                price = float(prices[target].at[day, "open"])
                budget = equity_at_open * cfg.allocation
                shares = math.floor(budget / (price * (1.0 + cost_rate)))
                gross = shares * price
                fee = gross * cost_rate
                cash -= gross + fee
                holding = target
                holding_branch = branch
                trades.append({"date": day, "signal_date": signal_day, "action": "BUY", "symbol": holding, "price": price, "shares": shares, "fee": fee, "branch": branch})
        equity = cash if holding is None else cash + shares * float(prices[holding].at[day, "close"])
        daily.append({"date": day, "equity": equity, "holding": holding or "CASH", "branch": holding_branch, "cash": cash, "shares": shares})
    return pd.DataFrame(daily), pd.DataFrame(trades)


def performance(daily: pd.DataFrame, initial_cash: float) -> dict[str, float]:
    equity = daily.equity.astype(float)
    returns = equity.pct_change().dropna()
    elapsed = (daily.date.iloc[-1] - daily.date.iloc[0]).days / 365.2425
    dd = equity / equity.cummax() - 1.0
    std = returns.std(ddof=1)
    return {
        "ending_equity": float(equity.iloc[-1]),
        "cagr": float((equity.iloc[-1] / initial_cash) ** (1 / elapsed) - 1),
        "max_drawdown": float(dd.min()),
        "volatility": float(std * math.sqrt(252)),
        "sharpe": float(returns.mean() / std * math.sqrt(252)) if std > 0 else 0.0,
    }


def run_case(prices: dict[str, pd.DataFrame], name: str, cfg: StrategyConfig) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    daily, trades = run_backtest(prices, cfg)
    stats = {"case": name, **performance(daily, cfg.initial_cash), "orders": len(trades), "entries": int((trades.action == "BUY").sum()), "fees": float(trades.fee.sum())}
    return stats, daily, trades


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data/conditional_sector_rotation")
    parser.add_argument("--output-dir", type=Path, default=root / "results/conditional_sector_rotation")
    parser.add_argument("--start-date", type=date.fromisoformat, default=StrategyConfig.start_date)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--cost-bps", type=float, default=StrategyConfig.cost_bps)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cfg = replace(StrategyConfig(), start_date=args.start_date, end_date=args.end_date, cost_bps=args.cost_bps)
    prices = load_prices(args.data_dir)
    stats, daily, trades = run_case(prices, "baseline", cfg)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.output_dir / "daily_equity.csv", index=False)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    print(pd.Series(stats).to_string())


if __name__ == "__main__":
    main()
