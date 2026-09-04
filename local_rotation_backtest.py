#!/usr/bin/env python3
"""Backtest the monthly QQQ-regime leveraged ETF rotation strategy."""

# .venv/bin/python local_rotation_backtest.py \
#   --execution qc_close \
#   --start-date 2016-06-01 \
#   --end-date 2026-06-12 \
#   --initial-cash 100000 \
#   --output-dir results/rotation_qc_close

# === MONTHLY LEVERAGED ETF ROTATION ===
# Period           : 2016-06-01 to 2026-06-12
# Execution        : qc_close
# Trading costs    : 0.00 bps per side
# Ending equity    : $7,460,461.09
# CAGR             : 53.72%
# Max drawdown     : -35.46%
# Volatility       : 39.88%
# Daily Sharpe     : 1.28
# Trade actions    : 126
# Stop exits       : 30
# QQQ buy/hold     : CAGR 21.44%, max DD -35.12%
# TQQQ buy/hold    : CAGR 43.47%, max DD -81.66%
# Daily equity CSV : results/rotation_qc_close/daily_equity.csv
# Trades CSV       : results/rotation_qc_close/trades.csv

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd


SYMBOLS = ("QQQ", "TQQQ", "TECL", "GLD", "IEF", "SHY")
RISK_ASSETS = ("TQQQ", "TECL")
DEFENSIVE_ASSETS = ("GLD", "IEF", "SHY")


@dataclass(frozen=True)
class Config:
    initial_cash: float = 100_000.0
    start_date: date = date(2016, 6, 1)
    end_date: Optional[date] = None
    execution: str = "next_open"
    rebalance_schedule: str = "month_start"
    daily_qqq_risk_off: bool = False
    risk_selection: str = "strongest"
    stop_loss: float = 0.10
    cost_bps: float = 0.0


def load_prices(data_dir: Path) -> dict[str, pd.DataFrame]:
    prices: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        path = data_dir / f"{symbol.lower()}_daily.parquet"
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; run download_rotation_etfs.py")
        frame = pd.read_parquet(path)
        frame.columns = [str(column).lower() for column in frame.columns]
        required = {"date", "open", "high", "low", "close"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        frame["date"] = pd.to_datetime(frame["date"]).dt.normalize()
        frame = frame.set_index("date").sort_index()
        if frame.index.has_duplicates:
            raise ValueError(f"{path} contains duplicate dates")
        if frame[list(required - {"date"})].isna().any().any():
            raise ValueError(f"{path} contains missing OHLC values")
        prices[symbol] = frame
    return prices


def build_signals(
    prices: dict[str, pd.DataFrame], risk_selection: str = "strongest"
) -> pd.DataFrame:
    """Build signals using only information available at each session close."""
    valid_risk_selections = {"strongest", "weakest", "laggard_3d"}
    if risk_selection not in valid_risk_selections:
        raise ValueError(
            f"Unknown risk selection {risk_selection!r}; expected one of "
            f"{sorted(valid_risk_selections)}"
        )
    close = pd.concat(
        {symbol: prices[symbol]["close"] for symbol in SYMBOLS}, axis=1
    ).sort_index()
    qqq = close["QQQ"]
    result = pd.DataFrame(index=close.index)
    result["qqq_close"] = qqq
    result["qqq_sma200"] = qqq.rolling(200, min_periods=100).mean()
    result["qqq_return126"] = qqq / qqq.shift(126) - 1.0
    result["risk_on"] = (
        (result["qqq_close"] > result["qqq_sma200"])
        & (result["qqq_return126"] > 0.0)
    )

    # Match the publisher's downloadable code exactly: 63-session percentage
    # change minus 21-session percentage change. This is not mathematically
    # identical to the return from t-63 through t-21.
    risk_returns = (
        close[list(RISK_ASSETS)].pct_change(63, fill_method=None)
        - close[list(RISK_ASSETS)].pct_change(21, fill_method=None)
    )
    risk_returns_3d = close[list(RISK_ASSETS)].pct_change(3, fill_method=None)
    defensive_momentum = (
        close[list(DEFENSIVE_ASSETS)].pct_change(63, fill_method=None)
        - close[list(DEFENSIVE_ASSETS)].pct_change(21, fill_method=None)
    )
    def available_winner(row: pd.Series) -> Optional[str]:
        available = row.dropna()
        return str(available.idxmax()) if not available.empty else None

    def available_risk_pick(row: pd.Series) -> Optional[str]:
        available = row.dropna()
        if available.empty:
            return None
        if risk_selection == "weakest":
            return str(available.idxmin())
        return str(available.idxmax())

    risk_scores = risk_returns_3d if risk_selection == "laggard_3d" else risk_returns
    if risk_selection == "laggard_3d":
        result["risk_pick"] = risk_scores.apply(
            lambda row: str(row.dropna().idxmin()) if not row.dropna().empty else None,
            axis=1,
        )
    else:
        result["risk_pick"] = risk_scores.apply(available_risk_pick, axis=1)
    result["defensive_pick"] = defensive_momentum.apply(available_winner, axis=1)
    result["target"] = result["defensive_pick"]
    result.loc[result["risk_on"], "target"] = result.loc[
        result["risk_on"], "risk_pick"
    ]
    result.loc[result["qqq_return126"].isna(), "target"] = None
    return result


def month_end_dates(index: pd.DatetimeIndex) -> set[pd.Timestamp]:
    periods = index.to_period("M")
    return {
        index[i]
        for i in range(len(index))
        if i == len(index) - 1 or periods[i] != periods[i + 1]
    }


def qc_rebalance_dates(
    index: pd.DatetimeIndex, schedule: str, anchor: pd.Timestamp
) -> set[pd.Timestamp]:
    """Return executable sessions for QC-style close rebalances."""
    if schedule == "month_start":
        periods = index.to_period("M")
        candidates = {index[i] for i in range(len(index)) if i == 0 or periods[i] != periods[i - 1]}
    elif schedule == "first_monday":
        candidates = set()
        for period in index.to_period("M").unique():
            month_days = index[index.to_period("M") == period]
            month_start = period.start_time
            first_calendar_monday = month_start + pd.Timedelta(
                days=(7 - month_start.weekday()) % 7
            )
            eligible = month_days[month_days >= first_calendar_monday]
            if len(eligible):
                candidates.add(eligible[0])
    elif schedule == "weekly_monday":
        periods = index.to_period("W-SUN")
        candidates = {index[i] for i in range(len(index)) if i == 0 or periods[i] != periods[i - 1]}
    elif schedule in ("every_2_months", "every_3_months"):
        step = 2 if schedule == "every_2_months" else 3
        periods = index.to_period("M")
        month_starts = [index[i] for i in range(len(index)) if i == 0 or periods[i] != periods[i - 1]]
        anchor_number = anchor.year * 12 + anchor.month
        candidates = {
            day
            for day in month_starts
            if ((day.year * 12 + day.month) - anchor_number) % step == 0
        }
    else:
        raise ValueError(f"Unknown rebalance schedule: {schedule}")
    return {day for day in candidates if day >= anchor}


def stop_fill(open_price: float, low_price: float, stop_price: float) -> Optional[float]:
    """Model a sell stop: gap through it fills at open, otherwise at the stop."""
    if low_price > stop_price:
        return None
    return open_price if open_price < stop_price else stop_price


class RotationBacktest:
    def __init__(self, prices: dict[str, pd.DataFrame], config: Config):
        self.prices = prices
        self.cfg = config
        self.signals = build_signals(prices, config.risk_selection)
        self.cash = config.initial_cash
        self.symbol: Optional[str] = None
        self.shares = 0.0
        self.stop_reference: Optional[float] = None
        self.trades: list[dict] = []
        self.daily: list[dict] = []

    @property
    def cost_rate(self) -> float:
        return self.cfg.cost_bps / 10_000.0

    def sell(self, day: pd.Timestamp, price: float, reason: str) -> None:
        if self.symbol is None:
            return
        symbol = self.symbol
        proceeds_before_cost = self.shares * price
        cost = proceeds_before_cost * self.cost_rate
        self.cash += proceeds_before_cost - cost
        self.trades.append(
            {
                "date": day.date().isoformat(),
                "action": "SELL",
                "symbol": symbol,
                "price": price,
                "shares": self.shares,
                "cost": cost,
                "reason": reason,
            }
        )
        self.symbol = None
        self.shares = 0.0
        self.stop_reference = None

    def buy(self, day: pd.Timestamp, symbol: str, price: float, reason: str) -> None:
        cost_rate = self.cost_rate
        shares = self.cash / (price * (1.0 + cost_rate))
        notional = shares * price
        cost = notional * cost_rate
        self.cash -= notional + cost
        if abs(self.cash) < 1e-8:
            self.cash = 0.0
        self.symbol = symbol
        self.shares = shares
        self.stop_reference = price
        self.trades.append(
            {
                "date": day.date().isoformat(),
                "action": "BUY",
                "symbol": symbol,
                "price": price,
                "shares": shares,
                "cost": cost,
                "reason": reason,
            }
        )

    def rebalance(
        self,
        day: pd.Timestamp,
        target: str,
        field: str,
        reason: str = "REBALANCE",
    ) -> None:
        price = float(self.prices[target].at[day, field])
        if self.symbol == target:
            # FinLab evaluates the resampled position as a fresh monthly lot,
            # so its percentage stop is reset even when the ticker is unchanged.
            self.stop_reference = price
            return
        if self.symbol is not None:
            old_price = float(self.prices[self.symbol].at[day, field])
            self.sell(day, old_price, reason)
        self.buy(day, target, price, reason)

    def check_stop(self, day: pd.Timestamp) -> None:
        if self.symbol is None or self.stop_reference is None:
            return
        row = self.prices[self.symbol].loc[day]
        threshold = self.stop_reference * (1.0 - self.cfg.stop_loss)
        fill = stop_fill(float(row["open"]), float(row["low"]), threshold)
        if fill is not None:
            self.sell(day, fill, "STOP")

    def equity(self, day: pd.Timestamp) -> float:
        if self.symbol is None:
            return self.cash
        return self.cash + self.shares * float(self.prices[self.symbol].at[day, "close"])

    def run(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        common = self.prices["QQQ"].index
        for symbol in SYMBOLS[1:]:
            common = common.intersection(self.prices[symbol].index)
        start = pd.Timestamp(self.cfg.start_date)
        end = pd.Timestamp(self.cfg.end_date) if self.cfg.end_date else common[-1]
        days = common[(common >= start) & (common <= end)]
        if days.empty:
            raise RuntimeError("No common ETF sessions in the requested period")
        ends = month_end_dates(days)
        qc_dates = qc_rebalance_dates(days, self.cfg.rebalance_schedule, days[0])
        pending_target: Optional[str] = None
        prior_days = common[common < days[0]]
        if self.cfg.execution in ("next_open", "qc_close") and len(prior_days):
            initial_target = self.signals.at[prior_days[-1], "target"]
            pending_target = initial_target if isinstance(initial_target, str) else None

        for day_number, day in enumerate(days):
            pending_reason = "REBALANCE"
            if self.cfg.execution == "qc_close" and day in qc_dates:
                history_days = common[common < day]
                target = self.signals.at[history_days[-1], "target"] if len(history_days) else None
                pending_target = target if isinstance(target, str) else None
            elif (
                self.cfg.execution == "qc_close"
                and self.cfg.daily_qqq_risk_off
                and self.symbol in RISK_ASSETS
            ):
                history_days = common[common < day]
                if len(history_days):
                    prior_signal = self.signals.loc[history_days[-1]]
                    defensive_pick = prior_signal["defensive_pick"]
                    if not bool(prior_signal["risk_on"]) and isinstance(
                        defensive_pick, str
                    ):
                        pending_target = defensive_pick
                        pending_reason = "DAILY_QQQ_RISK_OFF"
            entered_at_open = False
            if self.cfg.execution == "next_open" and pending_target is not None:
                self.rebalance(day, pending_target, "open")
                pending_target = None
                entered_at_open = True

            if self.cfg.execution == "qc_close" and pending_target is not None:
                # LEAN cancels the working stop at 09:31 and daily-resolution
                # market orders become market-on-close orders. Consequently,
                # the old stop cannot trigger during this session and the
                # rebalance is valued at this session's close.
                target = pending_target
                pending_target = None
                if self.symbol == target:
                    # With a daily subscription, Security.Price observed by
                    # OnEndOfDay is the prior completed bar in the reference QC
                    # run. Preserve that empirically observed stop-reset rule.
                    reference_day = days[day_number - 1] if day_number else prior_days[-1]
                    self.stop_reference = float(
                        self.prices[target].at[reference_day, "close"]
                    )
                else:
                    self.rebalance(day, target, "close", pending_reason)
            else:
                # A next-open entry can be stopped during that same session.
                # Close entries cannot encounter the day's earlier high/low.
                self.check_stop(day)

            is_initial_same_close = (
                self.cfg.execution == "same_close" and day == days[0]
            )
            if day in ends or is_initial_same_close:
                target = self.signals.at[day, "target"]
                if isinstance(target, str):
                    if self.cfg.execution == "same_close":
                        # Reproduce FinLab's same-bar month-end close convention.
                        self.rebalance(day, target, "close")
                    elif self.cfg.execution == "next_open":
                        pending_target = target

            self.daily.append(
                {
                    "date": day.date().isoformat(),
                    "equity": self.equity(day),
                    "holding": self.symbol or "CASH",
                    "shares": self.shares,
                    "cash": self.cash,
                    "stop_reference": self.stop_reference,
                    "entered_at_open": entered_at_open,
                }
            )

        final_day = days[-1]
        # QuantConnect marks an existing holding at the backtest end; it does
        # not synthesize a liquidation order. Retain the legacy forced exit in
        # the two original local modes for backward-compatible CSV output.
        if self.symbol is not None and self.cfg.execution != "qc_close":
            self.sell(
                final_day,
                float(self.prices[self.symbol].at[final_day, "close"]),
                "END",
            )
            self.daily[-1]["equity"] = self.cash
            self.daily[-1]["holding"] = "CASH"
            self.daily[-1]["shares"] = 0.0
            self.daily[-1]["cash"] = self.cash
        return pd.DataFrame(self.daily), pd.DataFrame(self.trades)


def performance(equity: pd.Series, dates: pd.Series) -> dict[str, float]:
    values = equity.astype(float)
    elapsed = (pd.Timestamp(dates.iloc[-1]) - pd.Timestamp(dates.iloc[0])).days / 365.2425
    ending = float(values.iloc[-1])
    initial = float(values.iloc[0])
    cagr = (ending / initial) ** (1.0 / elapsed) - 1.0 if elapsed > 0 else 0.0
    drawdown = values / values.cummax() - 1.0
    daily_returns = values.pct_change().dropna()
    volatility = float(daily_returns.std(ddof=1) * math.sqrt(252))
    sharpe = (
        float(daily_returns.mean() / daily_returns.std(ddof=1) * math.sqrt(252))
        if daily_returns.std(ddof=1) > 0
        else 0.0
    )
    return {
        "ending": ending,
        "cagr": cagr,
        "max_drawdown": float(drawdown.min()),
        "volatility": volatility,
        "sharpe": sharpe,
    }


def benchmark(prices: pd.DataFrame, days: pd.Series, initial: float) -> pd.Series:
    index = pd.to_datetime(days)
    close = prices.reindex(index)["close"]
    return close / close.iloc[0] * initial


def write_csv(path: Path, rows: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=root / "data/etfs")
    parser.add_argument("--output-dir", type=Path, default=root / "results/rotation")
    parser.add_argument("--initial-cash", type=float, default=100_000.0)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2016, 6, 1))
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--execution",
        choices=("same_close", "next_open", "qc_close"),
        default="next_open",
        help=(
            "same_close uses month-end closes; next_open trades the following "
            "open; qc_close uses prior-session signals and first-session closes"
        ),
    )
    parser.add_argument("--stop-loss", type=float, default=0.10)
    parser.add_argument(
        "--rebalance-schedule",
        choices=(
            "month_start",
            "first_monday",
            "every_2_months",
            "every_3_months",
            "weekly_monday",
        ),
        default="month_start",
        help="QC-close management schedule (default: month_start)",
    )
    parser.add_argument(
        "--daily-qqq-risk-off",
        action="store_true",
        help=(
            "check QQQ's regime after every close and rotate a risk holding "
            "to the prior session's defensive pick at the next close"
        ),
    )
    parser.add_argument(
        "--risk-selection",
        choices=("strongest", "weakest", "laggard_3d"),
        default="strongest",
        help=(
            "strongest/weakest use 63-day minus 21-day momentum; laggard_3d "
            "implements FinLab's mean-reversion rule (default: strongest)"
        ),
    )
    parser.add_argument("--cost-bps", type=float, default=0.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 < args.stop_loss < 1:
        raise SystemExit("--stop-loss must be between 0 and 1")
    if args.initial_cash <= 0 or args.cost_bps < 0:
        raise SystemExit("--initial-cash must be positive and --cost-bps non-negative")
    if args.daily_qqq_risk_off and args.execution != "qc_close":
        raise SystemExit("--daily-qqq-risk-off requires --execution qc_close")
    config = Config(
        initial_cash=args.initial_cash,
        start_date=args.start_date,
        end_date=args.end_date,
        execution=args.execution,
        rebalance_schedule=args.rebalance_schedule,
        daily_qqq_risk_off=args.daily_qqq_risk_off,
        risk_selection=args.risk_selection,
        stop_loss=args.stop_loss,
        cost_bps=args.cost_bps,
    )
    prices = load_prices(args.data_dir)
    engine = RotationBacktest(prices, config)
    daily, trades = engine.run()
    write_csv(args.output_dir / "daily_equity.csv", daily)
    write_csv(args.output_dir / "trades.csv", trades)

    strategy = performance(daily["equity"], daily["date"])
    print("\n=== MONTHLY LEVERAGED ETF ROTATION ===")
    print(f"Period           : {daily.iloc[0]['date']} to {daily.iloc[-1]['date']}")
    print(f"Execution        : {args.execution}")
    print(f"Rebalance        : {args.rebalance_schedule}")
    print(
        "Daily QQQ exit   : "
        + ("enabled" if args.daily_qqq_risk_off else "disabled")
    )
    print(f"Risk selection   : {args.risk_selection}")
    print(f"Trading costs    : {args.cost_bps:.2f} bps per side")
    print(f"Ending equity    : ${strategy['ending']:,.2f}")
    print(f"CAGR             : {strategy['cagr']:.2%}")
    print(f"Max drawdown     : {strategy['max_drawdown']:.2%}")
    print(f"Volatility       : {strategy['volatility']:.2%}")
    print(f"Daily Sharpe     : {strategy['sharpe']:.2f}")
    print(f"Trade actions    : {len(trades):,}")
    print(f"Stop exits       : {(trades['reason'] == 'STOP').sum():,}")

    for symbol in ("QQQ", "TQQQ"):
        values = benchmark(prices[symbol], daily["date"], args.initial_cash)
        stats = performance(values, daily["date"])
        print(
            f"{symbol + ' buy/hold':<17}: CAGR {stats['cagr']:.2%}, "
            f"max DD {stats['max_drawdown']:.2%}"
        )
    print(f"Daily equity CSV : {args.output_dir / 'daily_equity.csv'}")
    print(f"Trades CSV       : {args.output_dir / 'trades.csv'}")


if __name__ == "__main__":
    main()
