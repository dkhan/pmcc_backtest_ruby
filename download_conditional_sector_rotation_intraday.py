#!/usr/bin/env python3
"""Download the longest Yahoo hourly window available for timing tests."""

from pathlib import Path

import yfinance as yf

from local_conditional_sector_rotation import SYMBOLS


def main() -> None:
    output_dir = Path(__file__).resolve().parent / "data" / "conditional_sector_rotation_hourly"
    output_dir.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        data = yf.download(
            symbol,
            period="729d",
            interval="60m",
            auto_adjust=True,
            actions=False,
            repair=True,
            prepost=False,
            progress=False,
            multi_level_index=False,
        )
        if data.empty:
            raise RuntimeError(f"No hourly data downloaded for {symbol}")
        data.index.name = "datetime"
        data.columns = [str(column).lower().replace(" ", "_") for column in data.columns]
        path = output_dir / f"{symbol.lower()}_hourly.parquet"
        data.reset_index().to_parquet(path, index=False)
        print(f"{symbol}: {len(data):,} hourly bars -> {path}")


if __name__ == "__main__":
    main()
