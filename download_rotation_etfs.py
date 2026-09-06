from pathlib import Path

import yfinance as yf

symbols = ["QQQ", "QLD", "TQQQ", "TECL", "GLD", "IEF", "SHY", "SPY", "XLU", "UVXY"]
output_dir = Path("data/etfs")
output_dir.mkdir(parents=True, exist_ok=True)

for symbol in symbols:
    data = yf.download(
        symbol,
        start="2008-01-01",
        end="2026-09-03",  # end is exclusive
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
    data.columns = [column.lower().replace(" ", "_") for column in data.columns]

    path = output_dir / f"{symbol.lower()}_daily.parquet"
    data.reset_index().to_parquet(path, index=False)
    print(f"{symbol}: {len(data):,} rows -> {path}")
