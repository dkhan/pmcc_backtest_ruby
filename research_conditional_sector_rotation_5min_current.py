#!/usr/bin/env python3
"""Compare current-session CSR signals at every five-minute decision time.

Yahoo labels five-minute bars by their start.  For a 15:45 decision, the signal
therefore uses the close of the 15:40 bar and execution uses the 15:45 bar open.
This approximates the QuantConnect schedule without using the future 15:45 bar.
Yahoo limits five-minute history to roughly 60 days, so this is a recent-period
timing diagnostic rather than a replacement for a full QuantConnect backtest.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import replace
from datetime import time, timedelta
from pathlib import Path

import pandas as pd

from local_conditional_sector_rotation import (
    SYMBOLS,
    StrategyConfig,
    choose_target,
    load_prices,
    performance,
    wilder_rsi,
)


def decision_times() -> tuple[time, ...]:
    stamp = pd.Timestamp("2000-01-01 09:30")
    end = pd.Timestamp("2000-01-01 15:55")
    result = []
    while stamp <= end:
        result.append(stamp.time())
        stamp += timedelta(minutes=5)
    return tuple(result)


TIMES = decision_times()


def load_five_minute(data_dir: Path) -> dict[str, pd.DataFrame]:
    result = {}
    for symbol in SYMBOLS:
        path = data_dir / f"{symbol.lower()}_5min.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path}; run download_conditional_sector_rotation_5min.py"
            )
        frame = pd.read_parquet(path)
        stamp = pd.to_datetime(frame.pop("datetime"), utc=True).dt.tz_convert(
            "America/New_York"
        )
        frame.index = pd.DatetimeIndex(stamp)
        result[symbol] = frame.astype(float).sort_index()
    return result


def bars_for_day(frame: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    dates = frame.index.tz_localize(None).normalize()
    return frame.loc[dates == day]


def signal_and_fill_prices(
    intraday: dict[str, pd.DataFrame], day: pd.Timestamp, clock: time
) -> tuple[dict[str, float], dict[str, float]] | None:
    signal_prices, fill_prices = {}, {}
    decision = clock.hour * 60 + clock.minute
    for symbol in SYMBOLS:
        bars = bars_for_day(intraday[symbol], day)
        minute_of_day = bars.index.hour * 60 + bars.index.minute
        fill = bars[minute_of_day == decision]
        # SPY is the market-session clock. If it has no bar at the decision
        # time, the session ended early or is incomplete; do not trade.
        if symbol == "SPY" and fill.empty:
            return None
        if clock == time(9, 30):
            # There is no completed regular-session bar yet. Use the opening
            # print for both the provisional observation and execution.
            if fill.empty:
                return None
            signal_prices[symbol] = float(fill.iloc[0].open)
        else:
            previous = bars[minute_of_day < decision]
            if previous.empty:
                return None
            signal_prices[symbol] = float(previous.iloc[-1].close)
        # QuantConnect subscriptions fill forward missing secondary-ETF bars. Approximate
        # that behavior with the last completed price when Yahoo has no exact
        # bar for a thinly traded symbol such as TECS.
        fill_prices[symbol] = (
            float(fill.iloc[0].open) if not fill.empty else signal_prices[symbol]
        )
    return signal_prices, fill_prices


def current_signal(
    daily_prices: dict[str, pd.DataFrame],
    day: pd.Timestamp,
    current_prices: dict[str, float],
    cfg: StrategyConfig,
) -> pd.Series | None:
    closes = {}
    for symbol in SYMBOLS:
        prior = daily_prices[symbol].loc[
            daily_prices[symbol].index < day, "close"
        ].copy()
        prior.loc[day] = current_prices[symbol]
        required = max(
            cfg.rsi_period + 1,
            cfg.spy_sma_period if symbol == "SPY" else 0,
            cfg.qqq_sma_period if symbol == "QQQ" else 0,
            cfg.tqqq_sma_period if symbol == "TQQQ" else 0,
        )
        if len(prior) < required:
            return None
        closes[symbol] = prior

    values = {
        f"{symbol}_rsi": float(wilder_rsi(close, cfg.rsi_period).iloc[-1])
        for symbol, close in closes.items()
    }
    values.update(
        SPY_close=float(closes["SPY"].iloc[-1]),
        QQQ_close=float(closes["QQQ"].iloc[-1]),
        TQQQ_close=float(closes["TQQQ"].iloc[-1]),
        SPY_sma=float(closes["SPY"].iloc[-cfg.spy_sma_period :].mean()),
        QQQ_sma=float(closes["QQQ"].iloc[-cfg.qqq_sma_period :].mean()),
        TQQQ_sma=float(closes["TQQQ"].iloc[-cfg.tqqq_sma_period :].mean()),
    )
    return pd.Series(values)


def common_sessions(intraday: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    sessions = None
    for frame in intraday.values():
        dates = set(frame.index.tz_localize(None).normalize())
        sessions = dates if sessions is None else sessions & dates
    return pd.DatetimeIndex(sorted(sessions or set()))


def run_time(
    daily_prices: dict[str, pd.DataFrame],
    intraday: dict[str, pd.DataFrame],
    clock: time,
    cfg: StrategyConfig,
) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    days = common_sessions(intraday)
    if cfg.end_date:
        days = days[days <= pd.Timestamp(cfg.end_date)]
    days = days[days >= pd.Timestamp(cfg.start_date)]
    cash, shares, holding = cfg.initial_cash, 0, None
    rate = cfg.cost_bps / 10_000.0
    equity_rows, orders = [], []

    for day in days:
        prices = signal_and_fill_prices(intraday, day, clock)
        if prices is None:
            # On an early close after the chosen decision time, make no trade
            # but retain the session's mark-to-market return.
            if holding is not None and day in daily_prices[holding].index:
                close = float(daily_prices[holding].at[day, "close"])
                equity_rows.append(
                    dict(
                        date=day,
                        equity=cash + shares * close,
                        holding=holding,
                    )
                )
            continue
        signal_prices, fill_prices = prices
        signal = current_signal(daily_prices, day, signal_prices, cfg)
        if signal is None:
            continue
        target, branch = choose_target(signal, cfg)

        if target != holding:
            if holding is not None:
                fill = fill_prices[holding]
                gross = shares * fill
                fee = gross * rate
                cash += gross - fee
                orders.append(
                    dict(date=day, time=clock.strftime("%H:%M"), action="SELL", symbol=holding, price=fill, shares=shares, fee=fee, branch=branch)
                )
            fill = fill_prices[target]
            shares = math.floor(cash * cfg.allocation / (fill * (1 + rate)))
            gross = shares * fill
            fee = gross * rate
            cash -= gross + fee
            holding = target
            orders.append(
                dict(date=day, time=clock.strftime("%H:%M"), action="BUY", symbol=holding, price=fill, shares=shares, fee=fee, branch=branch)
            )

        close = float(daily_prices[holding].at[day, "close"])
        equity_rows.append(dict(date=day, equity=cash + shares * close, holding=holding))

    daily = pd.DataFrame(equity_rows)
    order_frame = pd.DataFrame(orders)
    if daily.empty:
        raise RuntimeError(f"No complete sessions for {clock:%H:%M}")
    stats = performance(daily, cfg.initial_cash)
    stats.update(
        time=clock.strftime("%H:%M"),
        sessions=len(daily),
        entries=int((order_frame.action == "BUY").sum()),
        fees=float(order_frame.fee.sum()),
    )
    return stats, daily, order_frame


def parse_args() -> argparse.Namespace:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-data", type=Path, default=root / "data/conditional_sector_rotation")
    parser.add_argument("--intraday-data", type=Path, default=root / "data/conditional_sector_rotation_5min")
    parser.add_argument("--output-dir", type=Path, default=root / "results/conditional_sector_rotation_5min_current")
    parser.add_argument("--cost-bps", type=float, default=StrategyConfig.cost_bps)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    daily_prices = load_prices(args.daily_data)
    intraday = load_five_minute(args.intraday_data)
    first = common_sessions(intraday)[0].date()
    last_daily = min(frame.index.max() for frame in daily_prices.values()).date()
    cfg = replace(
        StrategyConfig(),
        start_date=first,
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
    reference = summary.loc[summary.time == "15:45"].iloc[0]
    summary["ending_vs_1545"] = summary.ending_equity / reference.ending_equity - 1
    summary["cagr_vs_1545"] = summary.cagr - reference.cagr
    summary.to_csv(args.output_dir / "timing_summary.csv", index=False)
    ranked = summary.sort_values("ending_equity", ascending=False)
    print(ranked.to_string(index=False))
    print(f"\nWrote {len(summary)} five-minute cases to {args.output_dir}")


if __name__ == "__main__":
    main()
