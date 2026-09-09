#!/usr/bin/env python3
"""Download adjusted daily OHLC data for Conditional Sector Rotation research."""

from pathlib import Path

import yfinance as yf


SYMBOLS = ("SPY", "QQQ", "TQQQ", "UVXY", "TECL", "SPXL", "SQQQ", "TECS", "BSV")


def main() -> None:
    output_dir = Path(__file__).resolve().parent / "data" / "conditional_sector_rotation"
    output_dir.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        data = yf.download(
            symbol,
            start="2008-01-01",
            interval="1d",
            auto_adjust=True,
            actions=False,
            repair=True,
            progress=False,
            multi_level_index=False,
        )
        if data.empty:
            raise RuntimeError(f"No data downloaded for {symbol}")
        data.index.name = "date"
        data.columns = [str(column).lower().replace(" ", "_") for column in data.columns]
        path = output_dir / f"{symbol.lower()}_daily.parquet"
        data.reset_index().to_parquet(path, index=False)
        print(f"{symbol}: {len(data):,} rows -> {path}")


if __name__ == "__main__":
    main()
