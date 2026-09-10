#!/usr/bin/env python3
"""9:30 current-session Conditional Sector Rotation backtest.

Signals use prior completed daily closes plus the current session's opening
price. Trades execute at that same opening price. At each completed calendar
year-end, the model can withdraw a percentage of positive annual account growth
as a tax reserve and carry the reduced strategy balance into the next year.
"""

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
    annual_tax_withdrawal_rate: float = 0.443


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


def wilder_rsi_at_open(
    close: pd.Series, open_price: pd.Series, period: int
) -> pd.Series:
    """RSI using closes through yesterday and today's known opening price."""
    closes = close.to_numpy(dtype=float)
    opens = open_price.to_numpy(dtype=float)
    result = np.full(len(closes), np.nan)
    if len(closes) <= period:
        return pd.Series(result, index=close.index)

    close_changes = np.diff(closes)
    close_gains = np.maximum(close_changes, 0.0)
    close_losses = np.maximum(-close_changes, 0.0)
    average_gain = average_loss = None

    for index in range(period, len(closes)):
        opening_change = opens[index] - closes[index - 1]
        opening_gain = max(opening_change, 0.0)
        opening_loss = max(-opening_change, 0.0)

        if index == period:
            gains = np.append(close_gains[: period - 1], opening_gain)
            losses = np.append(close_losses[: period - 1], opening_loss)
            result[index] = _rsi_value(float(gains.mean()), float(losses.mean()))
            average_gain = float(close_gains[:period].mean())
            average_loss = float(close_losses[:period].mean())
        else:
            provisional_gain = (
                average_gain * (period - 1) + opening_gain
            ) / period
            provisional_loss = (
                average_loss * (period - 1) + opening_loss
            ) / period
            result[index] = _rsi_value(provisional_gain, provisional_loss)

            close_gain = close_gains[index - 1]
            close_loss = close_losses[index - 1]
            average_gain = (
                average_gain * (period - 1) + close_gain
            ) / period
            average_loss = (
                average_loss * (period - 1) + close_loss
            ) / period

    return pd.Series(result, index=close.index)


def _rsi_value(average_gain: float, average_loss: float) -> float:
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + average_gain / average_loss)


def build_indicators(prices: dict[str, pd.DataFrame], cfg: StrategyConfig) -> pd.DataFrame:
    """Build indicators known at 9:30, including today's opening print."""
    common = prices["SPY"].index
    for symbol in SYMBOLS[1:]:
        common = common.intersection(prices[symbol].index)
    out = pd.DataFrame(index=common)
    for symbol in SYMBOLS:
        # Calculate on each symbol's complete history before aligning calendars.
        # This matches QC warm-up behavior: SPY's 200-day SMA can be ready even
        # when a younger symbol such as UVXY has only enough bars for RSI(10).
        close = prices[symbol]["close"]
        opening = prices[symbol]["open"]
        out[f"{symbol}_close"] = opening.reindex(common)
        out[f"{symbol}_rsi"] = wilder_rsi_at_open(
            close, opening, cfg.rsi_period
        ).reindex(common)
    for symbol, period in (
        ("SPY", cfg.spy_sma_period),
        ("QQQ", cfg.qqq_sma_period),
        ("TQQQ", cfg.tqqq_sma_period),
    ):
        frame = prices[symbol]
        prior_sum = frame["close"].shift(1).rolling(period - 1).sum()
        out[f"{symbol}_sma"] = ((prior_sum + frame["open"]) / period).reindex(common)
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
    if (
        not 0 < cfg.allocation <= 1
        or cfg.confirmation_days < 1
        or cfg.execution_delay_days < 0
        or not 0 <= cfg.annual_tax_withdrawal_rate <= 1
    ):
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
    total_tax_withdrawal = 0.0
    year_start_balance = cfg.initial_cash

    for i, day in enumerate(days):
        if day in signals.index:
            signal = signals.loc[day]
            if signal.target != holding and not any(item[1] == signal.target for item in pending):
                pending.append((i + cfg.execution_delay_days, str(signal.target), str(signal.branch), day))
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
        closing_price = None if holding is None else float(prices[holding].at[day, "close"])
        equity = cash if holding is None else cash + shares * closing_price
        later_market_days = common[common > day]
        is_calendar_year_end = (
            len(later_market_days) > 0
            and later_market_days[0].year != day.year
        )
        tax_withdrawal = 0.0

        if is_calendar_year_end:
            annual_profit = max(0.0, equity - year_start_balance)
            tax_withdrawal = annual_profit * cfg.annual_tax_withdrawal_rate
            if tax_withdrawal > 0:
                if holding is not None:
                    post_withdrawal_balance = equity - tax_withdrawal
                    desired_shares = math.floor(
                        post_withdrawal_balance * cfg.allocation / closing_price
                    )
                    shares_to_sell = max(0, min(shares, shares - desired_shares))
                else:
                    shares_to_sell = 0
                if shares_to_sell > 0:
                    gross = shares_to_sell * closing_price
                    fee = gross * cost_rate
                    shares -= shares_to_sell
                    cash += gross - fee
                    trades.append(
                        {
                            "date": day,
                            "signal_date": day,
                            "action": "SELL_FOR_TAX",
                            "symbol": holding,
                            "price": closing_price,
                            "shares": shares_to_sell,
                            "fee": fee,
                            "branch": "annual_tax_withdrawal",
                        }
                    )
                tax_withdrawal = min(tax_withdrawal, cash)
                cash -= tax_withdrawal
                total_tax_withdrawal += tax_withdrawal
                equity = cash if holding is None else cash + shares * closing_price
            year_start_balance = equity

        daily.append(
            {
                "date": day,
                "equity": equity,
                "ending_balance": equity,
                "tax_withdrawal": tax_withdrawal,
                "cumulative_tax_withdrawal": total_tax_withdrawal,
                "holding": holding or "CASH",
                "branch": holding_branch,
                "cash": cash,
                "shares": shares,
            }
        )
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
    total_tax = float(daily.tax_withdrawal.sum())
    ending_balance = float(daily.ending_balance.iloc[-1])
    stats = {
        "case": name,
        **performance(daily, cfg.initial_cash),
        "ending_balance": ending_balance,
        "total_tax_withdrawal": total_tax,
        "ending_balance_plus_withdrawals": ending_balance + total_tax,
        "orders": len(trades),
        "entries": int((trades.action == "BUY").sum()),
        "fees": float(trades.fee.sum()),
    }
    return stats, daily, trades


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data/conditional_sector_rotation")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "results/conditional_sector_rotation_930",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=StrategyConfig.start_date)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--cost-bps", type=float, default=StrategyConfig.cost_bps)
    parser.add_argument(
        "--annual-tax-withdrawal-pct",
        type=float,
        default=StrategyConfig.annual_tax_withdrawal_rate * 100.0,
        help="Percent of positive calendar-year account growth withdrawn (default: 44.3)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 <= args.annual_tax_withdrawal_pct <= 100:
        raise ValueError("annual-tax-withdrawal-pct must be between 0 and 100")
    cfg = replace(
        StrategyConfig(),
        start_date=args.start_date,
        end_date=args.end_date,
        cost_bps=args.cost_bps,
        annual_tax_withdrawal_rate=args.annual_tax_withdrawal_pct / 100.0,
    )
    prices = load_prices(args.data_dir)
    stats, daily, trades = run_case(prices, "baseline", cfg)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    daily.to_csv(args.output_dir / "daily_equity.csv", index=False)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    pd.DataFrame([stats]).to_csv(args.output_dir / "summary.csv", index=False)
    daily.loc[daily.tax_withdrawal > 0, [
        "date",
        "ending_balance",
        "tax_withdrawal",
        "cumulative_tax_withdrawal",
    ]].to_csv(args.output_dir / "tax_withdrawals.csv", index=False)
    print(pd.Series(stats).to_string())


if __name__ == "__main__":
    main()
