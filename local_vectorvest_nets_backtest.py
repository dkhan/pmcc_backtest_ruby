#!/usr/bin/env python3
"""Local public-data approximation of VectorVest's NETS Turbo/Nitro/QQQ systems.

The proprietary VectorVest Confirmed Calls and Relative Timing values are not
available in market data. This model uses an EMA regime and RSI/50 respectively,
but preserves the published portfolio state machine and next-open execution.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Config:
    strategy: str = "turbo"
    start_date: str = "2016-02-08"
    end_date: str = "2024-02-07"
    initial_cash: float = 100_000.0
    trend_period: int = 50
    rsi_period: int = 14
    cost_bps: float = 0.0
    regime_mode: str = "two-week"


def load_prices(path: Path, prefix: str) -> pd.DataFrame:
    frame = pd.read_parquet(path)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    return frame[["open", "close"]].rename(
        columns={"open": f"{prefix}_open", "close": f"{prefix}_close"}
    )


def wilder_rsi(close: pd.Series, period: int) -> pd.Series:
    change = close.diff()
    gain = change.clip(lower=0.0)
    loss = -change.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    relative_strength = avg_gain / avg_loss.replace(0.0, float("nan"))
    result = 100.0 - 100.0 / (1.0 + relative_strength)
    return result.where(avg_loss != 0.0, 100.0)


def synthetic_qld(qqq: pd.DataFrame) -> pd.DataFrame:
    """Build a clearly labeled 2x daily QQQ proxy when QLD data is absent."""
    qqq_close = qqq["qqq_close"]
    returns = qqq_close.pct_change().fillna(0.0)
    close = 25.0 * (1.0 + 2.0 * returns).cumprod()
    overnight = qqq["qqq_open"] / qqq_close.shift(1) - 1.0
    open_price = close.shift(1) * (1.0 + 2.0 * overnight.fillna(0.0))
    open_price.iloc[0] = close.iloc[0]
    return pd.DataFrame({"qld_open": open_price, "qld_close": close})


class NetsBacktest:
    def __init__(self, prices: pd.DataFrame, config: Config):
        self.data = prices.copy()
        self.cfg = config
        close = self.data["qqq_close"]
        self.data["trend"] = close.ewm(
            span=config.trend_period,
            adjust=False,
            min_periods=config.trend_period,
        ).mean()
        self.data["qqq_rt"] = wilder_rsi(close, config.rsi_period) / 50.0
        self.data["qqq_rt10"] = self.data["qqq_rt"].rolling(10).mean()
        self.data["qqq_rt15"] = self.data["qqq_rt"].rolling(15).mean()
        for asset in ("qld", "tqqq"):
            self.data[f"{asset}_rt"] = (
                wilder_rsi(self.data[f"{asset}_close"], config.rsi_period) / 50.0
            )

        self.cash = config.initial_cash
        self.shares = 0.0
        self.asset: str | None = None
        self.entry_price: float | None = None
        self.pending: tuple[str | None, str] | None = None
        self.last_regime: str | None = None
        self.trades: list[dict] = []
        self.equity: list[dict] = []

    def price(self, row, asset: str, field: str) -> float:
        return float(row[f"{asset}_{field}"])

    def value(self, row, field="close") -> float:
        if self.asset is None:
            return self.cash
        return self.cash + self.shares * self.price(row, self.asset, field)

    def execute_pending(self, timestamp, row):
        if self.pending is None:
            return
        target, reason = self.pending
        self.pending = None
        cost_rate = self.cfg.cost_bps / 10_000.0

        if self.asset is not None:
            fill = self.price(row, self.asset, "open") * (1.0 - cost_rate)
            proceeds = self.shares * fill
            pnl = proceeds - self.shares * float(self.entry_price)
            self.cash += proceeds
            self.trades.append({
                "date": timestamp.date().isoformat(), "action": "SELL",
                "asset": self.asset.upper(), "price": fill,
                "shares": self.shares, "pnl": pnl, "reason": reason,
            })
            self.asset = None
            self.shares = 0.0
            self.entry_price = None

        if target is not None:
            fill = self.price(row, target, "open") * (1.0 + cost_rate)
            self.shares = self.cash / fill
            self.cash -= self.shares * fill
            self.asset = target
            self.entry_price = fill
            self.trades.append({
                "date": timestamp.date().isoformat(), "action": "BUY",
                "asset": target.upper(), "price": fill,
                "shares": self.shares, "pnl": "", "reason": reason,
            })

    def queue(self, target: str | None, reason: str):
        if target != self.asset:
            self.pending = (target, reason)

    def market_regime(self, index: int, row) -> str | None:
        if self.cfg.regime_mode == "ema":
            return "up" if row["qqq_close"] > row["trend"] else "down"

        # Public portion of VectorVest Confirmed Calls: a new direction needs
        # two consecutive five-session moves and agreement from today's move.
        # The proprietary Buy/Sell Ratio confirmation cannot be reconstructed.
        if index < 10:
            return self.last_regime
        closes = self.data["qqq_close"]
        current = float(closes.iloc[index])
        prior_day = float(closes.iloc[index - 1])
        prior_week = float(closes.iloc[index - 5])
        two_weeks = float(closes.iloc[index - 10])
        if current > prior_week > two_weeks and current > prior_day:
            return "up"
        if current < prior_week < two_weeks and current < prior_day:
            return "down"
        return self.last_regime

    def run(self):
        warmup = max(self.cfg.trend_period, self.cfg.rsi_period + 15) + 10
        start = pd.Timestamp(self.cfg.start_date)
        end = pd.Timestamp(self.cfg.end_date)

        for index, (timestamp, row) in enumerate(self.data.iterrows()):
            if timestamp < start:
                continue
            if timestamp > end:
                break
            self.execute_pending(timestamp, row)

            equity = self.value(row)
            self.equity.append({"date": timestamp.date().isoformat(), "equity": equity})
            if index < warmup or pd.isna(row["qqq_rt15"]) or pd.isna(row["trend"]):
                continue

            regime = self.market_regime(index, row)
            if regime is None:
                continue
            entered_up = regime == "up" and self.last_regime != "up"
            qqq_rt = float(row["qqq_rt"])

            if self.cfg.strategy == "qqq":
                prior_rt = float(self.data.iloc[index - 1]["qqq_rt"])
                if self.asset == "qqq":
                    position_return = row["qqq_close"] / self.entry_price - 1.0
                    if position_return >= 0.99:
                        self.queue(None, "99% profit exit")
                    elif position_return <= -0.15:
                        self.queue(None, "15% loss exit")
                    elif regime == "down":
                        self.queue(None, "Confirmed-Down proxy exit")
                elif regime == "up" and qqq_rt > 1.0 >= prior_rt:
                    self.queue("qqq", "GLB/RT-kicker proxy entry")
            else:
                leveraged_asset = "tqqq" if self.cfg.strategy == "nitro" else "qld"
                profit_target = 0.50 if self.cfg.strategy == "nitro" else 0.30
                leveraged_name = leveraged_asset.upper()
                leveraged_rt = float(row[f"{leveraged_asset}_rt"])

                if self.asset == leveraged_asset:
                    position_return = row[f"{leveraged_asset}_close"] / self.entry_price - 1.0
                    if position_return >= profit_target:
                        self.queue(None, f"{leveraged_name} {profit_target:.0%} profit exit")
                    elif position_return <= -0.05:
                        self.queue(None, f"{leveraged_name} 5% loss exit")
                    elif regime == "down":
                        self.queue(None, f"{leveraged_name} Confirmed-Down proxy exit")
                elif self.asset == "qqq":
                    if regime == "up":
                        if leveraged_rt <= 1.40:
                            self.queue(
                                leveraged_asset,
                                f"{leveraged_name} entry: Confirmed Up, RT <= 1.40",
                            )
                        else:
                            self.queue(None, "QQQ exit on Confirmed Up")
                elif entered_up and leveraged_rt <= 1.40:
                    self.queue(
                        leveraged_asset,
                        f"{leveraged_name} entry: Confirmed Up, RT <= 1.40",
                    )
                elif regime == "down" and index > 0:
                    prior = self.data.iloc[index - 1]
                    crossed = (
                        prior["qqq_rt10"] <= prior["qqq_rt15"]
                        and row["qqq_rt10"] > row["qqq_rt15"]
                    )
                    if crossed and qqq_rt <= 1.12:
                        self.queue("qqq", "QQQ RT10/RT15 crossover, RT <= 1.12")

            self.last_regime = regime

        if self.pending is not None:
            self.pending = None
        return self.metrics()

    def metrics(self):
        curve = pd.DataFrame(self.equity)
        values = curve["equity"]
        years = (
            pd.Timestamp(curve.iloc[-1]["date"]) - pd.Timestamp(curve.iloc[0]["date"])
        ).days / 365.2425
        cagr = (values.iloc[-1] / values.iloc[0]) ** (1.0 / years) - 1.0
        drawdown = values / values.cummax() - 1.0
        dates = pd.to_datetime(curve["date"])
        trailing_start = dates.iloc[-1] - pd.DateOffset(years=1)
        trailing_candidates = curve.loc[dates >= trailing_start, "equity"]
        trailing_one_year = (
            values.iloc[-1] / trailing_candidates.iloc[0] - 1.0
            if not trailing_candidates.empty else float("nan")
        )
        trailing_drawdown = (
            trailing_candidates / trailing_candidates.cummax() - 1.0
            if not trailing_candidates.empty else pd.Series(dtype=float)
        )
        return {
            "final_value": float(values.iloc[-1]),
            "total_return": float(values.iloc[-1] / values.iloc[0] - 1.0),
            "cagr": float(cagr),
            "max_drawdown": float(drawdown.min()),
            "trailing_one_year": float(trailing_one_year),
            "trailing_one_year_drawdown": (
                float(trailing_drawdown.min()) if not trailing_drawdown.empty
                else float("nan")
            ),
            "entries": sum(trade["action"] == "BUY" for trade in self.trades),
        }

    def write_results(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(self.equity).to_csv(output_dir / "daily_equity.csv", index=False)
        with (output_dir / "trades.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=("date", "action", "asset", "price", "shares", "pnl", "reason"),
            )
            writer.writeheader()
            writer.writerows(self.trades)


def prepare_data(data_dir: Path) -> tuple[pd.DataFrame, bool]:
    qqq = load_prices(data_dir / "qqq_daily.parquet", "qqq")
    qld_path = data_dir / "qld_daily.parquet"
    synthetic = not qld_path.exists()
    qld = synthetic_qld(qqq) if synthetic else load_prices(qld_path, "qld")
    tqqq = load_prices(data_dir / "tqqq_daily.parquet", "tqqq")
    return qqq.join(qld, how="inner").join(tqqq, how="inner").dropna(), synthetic


def print_metrics(label: str, metrics: dict):
    print(
        f"{label:<20} entries={metrics['entries']:>3}  "
        f"CAGR={metrics['cagr']:>7.2%}  DD={metrics['max_drawdown']:>7.2%}  "
        f"1Y={metrics['trailing_one_year']:>7.2%}  "
        f"1Y-DD={metrics['trailing_one_year_drawdown']:>7.2%}  "
        f"return={metrics['total_return']:>8.2%}  "
        f"final=${metrics['final_value']:,.0f}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strategy", choices=("qqq", "turbo", "nitro"), default="turbo"
    )
    parser.add_argument("--start-date")
    parser.add_argument("--end-date", default="2024-02-07")
    parser.add_argument("--trend-period", type=int, default=50)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--cost-bps", type=float, default=0.0)
    parser.add_argument(
        "--regime-mode", choices=("two-week", "ema"), default="two-week"
    )
    parser.add_argument("--sweep", action="store_true")
    parser.add_argument(
        "--sweep-target", choices=("cagr", "one-year"), default="cagr"
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/etfs"))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    start_date = args.start_date or (
        "2020-02-14" if args.strategy == "qqq" else "2015-12-29"
    )

    prices, used_synthetic = prepare_data(args.data_dir)
    if used_synthetic:
        print("WARNING: qld_daily.parquet missing; using a synthetic 2x QQQ series.")

    if args.sweep:
        rows = []
        target_value = 0.616 if args.sweep_target == "one-year" else (
            0.645 if args.strategy == "nitro" else 0.423
        )
        metric_name = "trailing_one_year" if args.sweep_target == "one-year" else "cagr"
        trend_values = (
            (args.trend_period,)
            if args.regime_mode == "two-week"
            else (10, 15, 20, 30, 40, 50, 75, 100)
        )
        for trend in trend_values:
            for rsi in (3, 4, 5, 7, 10, 14, 21, 28, 35, 42):
                cfg = Config(args.strategy, start_date, args.end_date,
                             100_000.0, trend, rsi, args.cost_bps,
                             args.regime_mode)
                test = NetsBacktest(prices, cfg)
                metrics = test.run()
                rows.append((abs(metrics[metric_name] - target_value), trend, rsi, metrics))
        for _, trend, rsi, metrics in sorted(rows)[:10]:
            regime_label = "2-week" if args.regime_mode == "two-week" else f"EMA {trend}"
            print_metrics(f"{regime_label}/RSI {rsi}", metrics)
        return

    config = Config(args.strategy, start_date, args.end_date, 100_000.0,
                    args.trend_period, args.rsi_period, args.cost_bps,
                    args.regime_mode)
    backtest = NetsBacktest(prices, config)
    metrics = backtest.run()
    output_dir = args.output_dir or Path(f"results/vectorvest_nets_{args.strategy}")
    backtest.write_results(output_dir)
    print_metrics(f"{args.strategy} local", metrics)
    print(f"Wrote {output_dir / 'trades.csv'}")
    print(f"Wrote {output_dir / 'daily_equity.csv'}")


if __name__ == "__main__":
    main()
