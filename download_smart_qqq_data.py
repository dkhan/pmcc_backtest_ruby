#!/usr/bin/env python3
"""Download raw signal prices and adjusted return prices for Smart QQQ."""

from pathlib import Path

import yfinance as yf


SYMBOLS = ("QQQ", "TQQQ", "SPY", "GLD", "XLU", "UVXY")
OUTPUT_DIR = Path("data/smart_qqq")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

for symbol in SYMBOLS:
    data = yf.download(
        symbol,
        start="2008-01-01",
        end="2026-09-06",
        interval="1d",
        auto_adjust=False,
        actions=True,
        repair=True,
        progress=False,
        multi_level_index=False,
    )
    if data.empty:
        raise RuntimeError(f"No data downloaded for {symbol}")
    data.index.name = "date"
    data.columns = [str(c).lower().replace(" ", "_") for c in data.columns]
    path = OUTPUT_DIR / f"{symbol.lower()}_daily.parquet"
    data.reset_index().to_parquet(path, index=False)
    print(f"{symbol}: {len(data):,} rows -> {path}")
