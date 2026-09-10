#!/usr/bin/env python3
"""Reproducible approximation of Jake M Trades' supply/demand method.

The video rules contain discretionary terms ("significant" structure, "nice"
zone, strong reaction).  This program makes each of those terms explicit and
uses only information available at the time of a signal.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import io
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
import yfinance as yf


@dataclass
class Zone:
    side: str
    created_at: pd.Timestamp
    low: float
    high: float
    touched: bool = False


def download(symbol: str, cache: Path, period: str = "60d") -> pd.DataFrame:
    cache.parent.mkdir(parents=True, exist_ok=True)
    frame = yf.download(
        symbol, period=period, interval="5m", auto_adjust=True, actions=False,
        repair=True, prepost=False, progress=False, multi_level_index=False,
    )
    if frame.empty:
        raise RuntimeError(f"No 5-minute bars returned for {symbol}")
    frame.columns = [str(c).lower().replace(" ", "_") for c in frame.columns]
    frame.index = pd.to_datetime(frame.index, utc=True)
    frame.index.name = "datetime"
    frame.to_parquet(cache)
    return frame


def load_or_download(symbol: str, cache: Path, refresh: bool) -> pd.DataFrame:
    if refresh or not cache.exists():
        return download(symbol, cache)
    frame = pd.read_parquet(cache)
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


def load_histdata_archives(archive_dir: Path, cache: Path) -> pd.DataFrame:
    """Load HistData M1 bid archives (fixed EST) and aggregate to M5."""
    archives = sorted(archive_dir.glob("DAT_ASCII_GBPUSD_M1_2026*.zip"))
    if not archives:
        raise RuntimeError(f"No 2026 HistData archives found in {archive_dir}")
    parts = []
    names = ["datetime", "open", "high", "low", "close", "volume"]
    for archive in archives:
        with zipfile.ZipFile(archive) as zf:
            csv_name = next(name for name in zf.namelist() if name.lower().endswith(".csv"))
            raw = zf.read(csv_name).decode("ascii")
        part = pd.read_csv(io.StringIO(raw), sep=";", names=names)
        # HistData documents these timestamps as fixed EST without DST.
        part["datetime"] = (pd.to_datetime(part.datetime, format="%Y%m%d %H%M%S")
                            .dt.tz_localize("Etc/GMT+5").dt.tz_convert("UTC"))
        parts.append(part.set_index("datetime"))
    minute = pd.concat(parts).sort_index()
    minute = minute[~minute.index.duplicated(keep="last")]
    five = minute.resample("5min").agg({
        "open": "first", "high": "max", "low": "min", "close": "last",
        "volume": "sum",
    }).dropna(subset=["open", "high", "low", "close"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    five.to_parquet(cache)
    return five


def backtest(
    bars: pd.DataFrame,
    structure_bars: int = 12,
    origin_lookback: int = 6,
    max_zone_atr: float = 1.25,
    spread_pips: float = 0.4,
    initial_equity: float = 10_000.0,
    risk_fraction: float = 0.01,
) -> tuple[pd.DataFrame, dict]:
    """Backtest fresh-zone retests with engulfing confirmation and 1R target.

    Fill convention is conservative when stop and target occur in one bar:
    the stop is assumed to occur first. Entry is at the confirmation close,
    because that is observable and matches the market-order examples.
    """
    df = bars.copy().sort_index()
    local = df.index.tz_convert("America/New_York")
    df["local_time"] = local
    df["prior_high"] = df.high.shift(1).rolling(structure_bars).max()
    df["prior_low"] = df.low.shift(1).rolling(structure_bars).min()
    true_range = pd.concat([
        df.high - df.low,
        (df.high - df.close.shift()).abs(),
        (df.low - df.close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = true_range.rolling(14).mean()

    zone: Zone | None = None
    position: dict | None = None
    trades: list[dict] = []
    traded_days: set = set()
    equity = initial_equity
    cost = spread_pips * 0.0001

    for i in range(max(structure_bars, 14), len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        day = row.local_time.date()

        if position is not None:
            stop_hit = row.low <= position["stop"] if position["side"] == "long" else row.high >= position["stop"]
            target_hit = row.high >= position["target"] if position["side"] == "long" else row.low <= position["target"]
            if stop_hit or target_hit:
                result_r = -1.0 if stop_hit else 1.0
                # Spread is charged as a fraction of initial price risk.
                net_r = result_r - cost / position["risk_price"]
                pnl = equity * risk_fraction * net_r
                equity += pnl
                trades.append({**position, "exit_time": ts, "result_r": result_r,
                               "net_r": net_r, "pnl": pnl, "equity": equity})
                position = None

        # A close beyond the distal edge invalidates Jake's zone.
        if zone and ((zone.side == "demand" and row.close < zone.low) or
                     (zone.side == "supply" and row.close > zone.high)):
            zone = None

        # A structure-breaking displacement creates a zone from the most
        # recent opposite-colour origin candle.
        bullish_break = row.close > row.prior_high
        bearish_break = row.close < row.prior_low
        if bullish_break or bearish_break:
            side = "demand" if bullish_break else "supply"
            candidates = df.iloc[max(0, i - origin_lookback):i]
            if side == "demand":
                candidates = candidates[candidates.close < candidates.open]
            else:
                candidates = candidates[candidates.close > candidates.open]
            if not candidates.empty:
                origin = candidates.iloc[-1]
                height = origin.high - origin.low
                if pd.notna(row.atr) and 0 < height <= max_zone_atr * row.atr:
                    zone = Zone(side, ts, float(origin.low), float(origin.high))

        if position is not None or zone is None or day in traded_days:
            continue
        # Video examples are in the New York morning and Jake skips Fridays.
        if row.local_time.weekday() >= 4 or not (7 <= row.local_time.hour < 11):
            continue
        prev = df.iloc[i - 1]
        overlaps = row.low <= zone.high and row.high >= zone.low
        if overlaps:
            zone.touched = True
        bullish_engulf = row.close > row.open and row.close >= prev.open and row.open <= prev.close
        bearish_engulf = row.close < row.open and row.close <= prev.open and row.open >= prev.close
        confirmed = ((zone.side == "demand" and bullish_engulf) or
                     (zone.side == "supply" and bearish_engulf))
        if zone.touched and confirmed:
            entry = float(row.close)
            stop = zone.low if zone.side == "demand" else zone.high
            risk_price = abs(entry - stop)
            if risk_price <= 0:
                continue
            direction = 1 if zone.side == "demand" else -1
            position = {
                "side": "long" if direction == 1 else "short",
                "zone_created": zone.created_at, "entry_time": ts,
                "entry": entry, "stop": stop,
                "target": entry + direction * risk_price,
                "risk_price": risk_price,
            }
            traded_days.add(day)
            zone = None

    out = pd.DataFrame(trades)
    if out.empty:
        stats = {"trades": 0, "ending_equity": equity}
    else:
        gross_profit = out.loc[out.pnl > 0, "pnl"].sum()
        gross_loss = -out.loc[out.pnl < 0, "pnl"].sum()
        peak = out.equity.cummax().clip(lower=initial_equity)
        drawdown = out.equity / peak - 1
        stats = {
            "trades": len(out),
            "wins": int((out.result_r > 0).sum()),
            "win_rate": float((out.result_r > 0).mean()),
            "net_r": float(out.net_r.sum()),
            "net_profit": float(equity - initial_equity),
            "ending_equity": float(equity),
            "return_pct": float((equity / initial_equity - 1) * 100),
            "profit_factor": float(gross_profit / gross_loss) if gross_loss else np.inf,
            "max_drawdown_pct": float(drawdown.min() * 100),
        }
    return out, stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="GBPUSD=X")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--spread-pips", type=float, default=0.4)
    parser.add_argument("--months", type=int, default=2,
                        help="Calendar months in the reported backtest window")
    parser.add_argument("--source", choices=("yahoo", "histdata"), default="yahoo")
    parser.add_argument("--start", help="Inclusive UTC start date; overrides --months")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    data_dir = root / "data" / "jake_supply_demand"
    if args.source == "histdata":
        cache = data_dir / "gbpusd_5m_histdata_2026.parquet"
        if args.refresh or not cache.exists():
            bars = load_histdata_archives(data_dir / "histdata_m1", cache)
        else:
            bars = pd.read_parquet(cache)
            bars.index = pd.to_datetime(bars.index, utc=True)
    else:
        cache = data_dir / "gbpusd_5m.parquet"
        bars = load_or_download(args.symbol, cache, args.refresh)
    window_start = (pd.Timestamp(args.start, tz="UTC") if args.start else
                    bars.index.max() - pd.DateOffset(months=args.months))
    bars = bars.loc[bars.index >= window_start]
    trades, stats = backtest(bars, spread_pips=args.spread_pips)
    output = root / "outputs" / "jake_supply_demand"
    output.mkdir(parents=True, exist_ok=True)
    suffix = "_2026_histdata" if args.source == "histdata" else ""
    trades_path = output / f"trades{suffix}.csv"
    summary_path = output / f"summary{suffix}.json"
    trades.to_csv(trades_path, index=False)
    pd.Series(stats).to_json(summary_path, indent=2)
    print(f"Bars: {len(bars):,} | {bars.index.min()} through {bars.index.max()}")
    for key, value in stats.items():
        print(f"{key}: {value:.4f}" if isinstance(value, float) else f"{key}: {value}")
    print(f"Trades: {trades_path}")


if __name__ == "__main__":
    main()
