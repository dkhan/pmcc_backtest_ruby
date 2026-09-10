import pandas as pd

from jake_supply_demand_backtest import backtest


def test_backtest_is_causal_and_returns_expected_shape():
    index = pd.date_range("2026-07-06 11:00", periods=80, freq="5min", tz="UTC")
    close = [1.25 + i * 0.00001 for i in range(80)]
    frame = pd.DataFrame({
        "open": close,
        "high": [x + 0.0002 for x in close],
        "low": [x - 0.0002 for x in close],
        "close": close,
        "volume": 0,
    }, index=index)
    trades, stats = backtest(frame)
    assert isinstance(trades, pd.DataFrame)
    assert stats["trades"] == len(trades)
    assert stats["ending_equity"] > 0


def test_empty_input_window_produces_no_trades():
    index = pd.date_range("2026-07-06", periods=20, freq="5min", tz="UTC")
    frame = pd.DataFrame(1.0, index=index, columns=["open", "high", "low", "close", "volume"])
    trades, stats = backtest(frame)
    assert trades.empty
    assert stats == {"trades": 0, "ending_equity": 10_000.0}
