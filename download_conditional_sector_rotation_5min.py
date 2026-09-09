#!/usr/bin/env python3
"""Download Yahoo's available adjusted 5-minute data for CSR timing tests."""

from pathlib import Path

import yfinance as yf

from local_conditional_sector_rotation import SYMBOLS


def main() -> None:
    output = Path(__file__).resolve().parent / "data/conditional_sector_rotation_5min"
    output.mkdir(parents=True, exist_ok=True)
    for symbol in SYMBOLS:
        frame = yf.download(
            symbol,
            period="60d",
            interval="5m",
            auto_adjust=True,
            actions=False,
            repair=True,
            prepost=False,
            progress=False,
            multi_level_index=False,
        )
        if frame.empty:
            raise RuntimeError(f"No 5-minute data downloaded for {symbol}")
        frame.index.name = "datetime"
        frame.columns = [str(column).lower().replace(" ", "_") for column in frame.columns]
        path = output / f"{symbol.lower()}_5min.parquet"
        frame.reset_index().to_parquet(path, index=False)
        print(f"{symbol}: {len(frame):,} five-minute bars -> {path}")


if __name__ == "__main__":
    main()
