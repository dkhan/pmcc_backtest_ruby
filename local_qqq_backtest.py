#!/usr/bin/env python3
"""Fast local backtest for the Tastytrade QQQ 21/7-delta call spread."""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import duckdb


@dataclass
class Config:
    start_date: date = date(2012, 12, 28)
    end_date: date = date(2025, 12, 28)
    initial_cash: float = 7_000.0
    fixed_contracts: Optional[int] = 7
    entry_equity_fraction: float = 0.007
    target_dte: int = 35
    max_dte: int = 42
    long_delta: float = 0.21
    short_delta: float = 0.07
    # stop_return: float = -0.84
    # profit_return: float = 4.20
    stop_return: float = float("-inf")
    profit_return: float = float("inf")
    fee_per_contract_per_leg: float = 0.56
    hold_idle_cash_in_qqq: bool = False
    qqq_equity_fraction: float = 0.77
    use_vix_rules: bool = False
    vix_trend_entry_threshold: Optional[float] = None
    entry_vix_min: float = 8.0
    entry_vix_max: float = 27.0
    vix_exit: float = 28.0
    debit_ratio_min: Optional[float] = None
    debit_ratio_max: Optional[float] = None
    skip_return_min: Optional[float] = None
    skip_return_max: Optional[float] = None
    return_lookback: int = 20
    max_open_debit_fraction: Optional[float] = None
    check_tp_sl_daily: bool = False


@dataclass
class Position:
    position_id: int
    opened: date
    expiration: date
    long_id: str
    short_id: str
    long_strike: float
    short_strike: float
    long_delta: float
    short_delta: float
    contracts: int
    debit: float
    entry_underlying: float
    entry_equity: float
    entry_dte: int
    entry_fees: float
    last_value: float = 0.0


class QqqCallSpreadBacktest:
    LOG_HEADER = (
        f"{'OPENED':<10} | {'CLOSED':<10} | {'PX OPEN':>7} | {'PX CLOSE':>8} | "
        f"{'L/S STRIKES':<11} | {'L/S DELTAS':<11} | {'EXP':<10} | {'DTE':>3} | "
        f"{'NUM':>3} | {'DEBIT':>5} | {'COST':>9} | {'EQUITY':>10} | {'EQ%':>6} | "
        f"{'VALUE':>6} | {'P/L':>9} | {'RSN':<3} | {'MULT':>5}"
    )

    def __init__(self, data_dir: Path, output_dir: Path, config: Config, verbose=True):
        self.data_dir = data_dir
        self.output_dir = output_dir
        self.cfg = config
        self.verbose = verbose
        self.db = duckdb.connect(database=":memory:")
        parquet_glob = str(data_dir / "options_*.parquet").replace("'", "''")
        underlying = str(data_dir / "underlying_prices.parquet").replace("'", "''")
        self.db.execute(
            f"""
            CREATE VIEW options AS
            SELECT *, CAST(date AS DATE) AS quote_date,
                      CAST(expiration AS DATE) AS expiry_date
            FROM read_parquet('{parquet_glob}', union_by_name=true)
            """
        )
        self.db.execute(
            f"""
            CREATE VIEW underlying AS
            SELECT *, CAST(date AS DATE) AS trading_date
            FROM read_parquet('{underlying}')
            """
        )

        self.cash = config.initial_cash
        self.qqq_shares = 0
        self.contract_floor = 0
        self.next_position_id = 1
        self.positions: list[Position] = []
        self.trades: list[dict] = []
        self.daily: list[dict] = []
        self.skipped_entries = 0
        self.vix_filtered_entries = 0
        self.rule_filtered_entries = 0
        self.skipped_entry_dates: list[date] = []
        self.vix = self._load_vix() if (
            config.use_vix_rules or config.vix_trend_entry_threshold is not None
        ) else {}

    @staticmethod
    def _as_date(value) -> date:
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value)[:10])

    def _load_vix(self) -> dict[date, float]:
        path = self.data_dir.parents[1] / "vix_daily.csv"
        values: dict[date, float] = {}
        if not path.exists():
            raise FileNotFoundError(f"VIX rules enabled but {path} does not exist")
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                values[self._as_date(row["date"])] = float(row["vix"])
        return values

    def trading_days(self) -> list[tuple[date, float]]:
        rows = self.db.execute(
            """
            SELECT trading_date, close
            FROM underlying
            WHERE trading_date BETWEEN ? AND ?
            ORDER BY trading_date
            """,
            [self.cfg.start_date, self.cfg.end_date],
        ).fetchall()
        return [(self._as_date(day), float(close)) for day, close in rows]

    @staticmethod
    def is_week_end(days: list[tuple[date, float]], index: int) -> bool:
        current = days[index][0]
        if index + 1 == len(days):
            return True
        following = days[index + 1][0]
        return current.isocalendar()[:2] != following.isocalendar()[:2]

    def entry_vix_allowed(self, days: list[tuple[date, float]], index: int) -> bool:
        """Use only VIX closes known before the current session's entry."""
        if self.cfg.vix_trend_entry_threshold is not None:
            if index < 5:
                return False
            trailing_dates = [day for day, _ in days[index - 5:index]]
            if any(day not in self.vix for day in trailing_dates):
                return False
            values = [self.vix[day] for day in trailing_dates]
            prior_vix = values[-1]
            prior_five_day_average = sum(values) / len(values)
            return not (
                prior_vix > self.cfg.vix_trend_entry_threshold
                and prior_vix > prior_five_day_average
            )
        if not self.cfg.use_vix_rules:
            return True
        if index == 0:
            return False
        prior_day = days[index - 1][0]
        return (
            prior_day in self.vix
            and self.cfg.entry_vix_min
            <= self.vix[prior_day]
            <= self.cfg.entry_vix_max
        )

    def quotes(self, day: date) -> dict[str, tuple[float, float]]:
        ids = [x for p in self.positions for x in (p.long_id, p.short_id)]
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.db.execute(
            f"SELECT contract_id, bid, ask FROM options "
            f"WHERE quote_date = ? AND contract_id IN ({placeholders})",
            [day, *ids],
        ).fetchall()
        return {
            contract_id: (float(bid or 0), float(ask or 0))
            for contract_id, bid, ask in rows
        }

    @staticmethod
    def liquidation_value(position: Position, quotes: dict) -> Optional[float]:
        long_quote = quotes.get(position.long_id)
        short_quote = quotes.get(position.short_id)
        if long_quote is None or short_quote is None:
            return None
        return max(0.0, long_quote[0] - short_quote[1])

    def portfolio_value(self, qqq_price: float, quotes: dict) -> float:
        option_value = 0.0
        for position in self.positions:
            value = self.liquidation_value(position, quotes)
            if value is not None:
                position.last_value = value
            option_value += position.last_value * 100 * position.contracts
        return self.cash + self.qqq_shares * qqq_price + option_value

    def choose_spread(self, day: date):
        rows = self.db.execute(
            """
            SELECT contract_id, expiry_date, strike, bid, ask, delta
            FROM options
            WHERE quote_date = ? AND type = 'call'
              AND expiry_date > ? AND expiry_date <= ?
              AND delta > 0 AND ask > 0 AND bid >= 0
            """,
            [day, day, day + timedelta(days=self.cfg.max_dte)],
        ).fetchall()
        if not rows:
            return None
        target_expiry = min(
            {self._as_date(row[1]) for row in rows},
            key=lambda expiry: abs((expiry - day).days - self.cfg.target_dte),
        )
        eligible = [row for row in rows if self._as_date(row[1]) == target_expiry]
        long_leg = min(eligible, key=lambda row: abs(float(row[5]) - self.cfg.long_delta))
        short_eligible = [row for row in eligible if float(row[2]) > float(long_leg[2])]
        if not short_eligible:
            return None
        short_leg = min(
            short_eligible, key=lambda row: abs(float(row[5]) - self.cfg.short_delta)
        )
        debit = float(long_leg[4]) - float(short_leg[3])
        if debit <= 0:
            return None
        return long_leg, short_leg, target_expiry, debit

    def raise_cash(self, required: float, qqq_price: float):
        if not self.cfg.hold_idle_cash_in_qqq or self.cash >= required:
            return
        shares = min(self.qqq_shares, math.ceil((required - self.cash) / qqq_price))
        self.qqq_shares -= shares
        self.cash += shares * qqq_price

    def sweep_cash(self, qqq_price: float):
        if not self.cfg.hold_idle_cash_in_qqq or qqq_price <= 0:
            return
        option_value = sum(
            position.last_value * 100 * position.contracts
            for position in self.positions
        )
        equity = self.cash + self.qqq_shares * qqq_price + option_value
        target_shares = math.floor(
            equity * self.cfg.qqq_equity_fraction / qqq_price
        )
        shares = max(
            0,
            min(target_shares - self.qqq_shares, math.floor(self.cash / qqq_price)),
        )
        self.qqq_shares += shares
        self.cash -= shares * qqq_price

    def open_spread(
        self,
        day: date,
        qqq_price: float,
        quotes: dict,
        prior_lookback_return: Optional[float] = None,
    ):
        selected = self.choose_spread(day)
        if selected is None:
            self.skipped_entries += 1
            self.skipped_entry_dates.append(day)
            return
        long_leg, short_leg, expiry, debit = selected
        spread_width = float(short_leg[2]) - float(long_leg[2])
        debit_ratio = debit / spread_width
        if (
            self.cfg.debit_ratio_min is not None
            and debit_ratio < self.cfg.debit_ratio_min
        ) or (
            self.cfg.debit_ratio_max is not None
            and debit_ratio > self.cfg.debit_ratio_max
        ):
            self.rule_filtered_entries += 1
            return
        if (
            prior_lookback_return is not None
            and self.cfg.skip_return_min is not None
            and self.cfg.skip_return_max is not None
            and self.cfg.skip_return_min
            <= prior_lookback_return
            <= self.cfg.skip_return_max
        ):
            self.rule_filtered_entries += 1
            return
        equity = self.portfolio_value(qqq_price, quotes)
        cost_per_contract = debit * 100
        if self.cfg.fixed_contracts is not None:
            contracts = self.cfg.fixed_contracts
        else:
            percentage_size = math.floor(
                (equity * self.cfg.entry_equity_fraction - 1e-9) / cost_per_contract
            )
            contracts = max(1, percentage_size, self.contract_floor)
        fees = 2 * self.cfg.fee_per_contract_per_leg * contracts
        total_cost = cost_per_contract * contracts
        if self.cfg.max_open_debit_fraction is not None:
            open_debit = sum(
                position.debit * 100 * position.contracts
                for position in self.positions
            )
            if (
                open_debit + total_cost
                > equity * self.cfg.max_open_debit_fraction
            ):
                self.rule_filtered_entries += 1
                return
        self.raise_cash(total_cost + fees, qqq_price)
        self.cash -= total_cost + fees

        position = Position(
            position_id=self.next_position_id,
            opened=day,
            expiration=expiry,
            long_id=str(long_leg[0]),
            short_id=str(short_leg[0]),
            long_strike=float(long_leg[2]),
            short_strike=float(short_leg[2]),
            long_delta=float(long_leg[5]),
            short_delta=float(short_leg[5]),
            contracts=contracts,
            debit=debit,
            entry_underlying=qqq_price,
            entry_equity=equity,
            entry_dte=(expiry - day).days,
            entry_fees=fees,
            last_value=debit,
        )
        self.positions.append(position)
        self.next_position_id += 1
        self.contract_floor = max(self.contract_floor, contracts)

    def close_position(
        self,
        position: Position,
        day: date,
        qqq_price: float,
        value: float,
        reason: str,
    ):
        exit_fees = 2 * self.cfg.fee_per_contract_per_leg * position.contracts
        proceeds = value * 100 * position.contracts
        self.cash += proceeds - exit_fees
        gross_pnl = (value - position.debit) * 100 * position.contracts
        net_pnl = gross_pnl - position.entry_fees - exit_fees
        cost = position.debit * 100 * position.contracts
        row = {
            "opened": position.opened.isoformat(),
            "closed": day.isoformat(),
            "px_open": position.entry_underlying,
            "px_close": qqq_price,
            "long_strike": position.long_strike,
            "short_strike": position.short_strike,
            "long_delta": position.long_delta,
            "short_delta": position.short_delta,
            "expiration": position.expiration.isoformat(),
            "dte": position.entry_dte,
            "days_held": (day - position.opened).days,
            "contracts": position.contracts,
            "debit": position.debit,
            "cost": cost,
            "entry_equity": position.entry_equity,
            "equity_fraction": cost / position.entry_equity,
            "value_at_close": value,
            "gross_pnl": gross_pnl,
            "fees": position.entry_fees + exit_fees,
            "net_pnl": net_pnl,
            "reason": reason,
            "multiple": value / position.debit,
        }
        self.trades.append(row)
        if self.verbose:
            print(self.format_trade(row))
        self.positions.remove(position)

    def process_exits(
        self, day: date, qqq_price: float, quotes: dict, week_end: bool, final: bool
    ):
        vix = self.vix.get(day) if self.cfg.use_vix_rules else None
        for position in list(self.positions):
            value = self.liquidation_value(position, quotes)
            if value is not None:
                position.last_value = value
            reason = None
            if final:
                reason = "END"
            elif day >= position.expiration:
                reason = "EXP"
            elif week_end and (position.expiration - day).days <= 3:
                reason = "EXP"
            elif value is not None and (
                week_end or self.cfg.check_tp_sl_daily
            ):
                spread_return = (value - position.debit) / position.debit
                if (
                    week_end
                    and self.cfg.use_vix_rules
                    and vix is not None
                    and vix >= self.cfg.vix_exit
                ):
                    reason = "VIX"
                elif spread_return <= self.cfg.stop_return:
                    reason = "SL"
                elif spread_return >= self.cfg.profit_return:
                    reason = "TP"
            if reason:
                close_value = value
                if close_value is None:
                    close_value = max(
                        0.0,
                        min(
                            position.short_strike - position.long_strike,
                            max(qqq_price - position.long_strike, 0.0)
                            - max(qqq_price - position.short_strike, 0.0),
                        ),
                    ) if day >= position.expiration else position.last_value
                self.close_position(position, day, qqq_price, close_value, reason)

    @classmethod
    def format_trade(cls, row: dict) -> str:
        strikes = f"{row['long_strike']:.1f}/{row['short_strike']:.1f}"
        deltas = f"{row['long_delta']:.2f}/{row['short_delta']:.2f}"
        return (
            f"{row['opened']:<10} | {row['closed']:<10} | {row['px_open']:7.2f} | "
            f"{row['px_close']:8.2f} | {strikes:<11} | {deltas:<11} | "
            f"{row['expiration']:<10} | {row['dte']:3d} | {row['contracts']:3d} | "
            f"{row['debit']:5.2f} | {row['cost']:9.2f} | {row['entry_equity']:10.2f} | "
            f"{row['equity_fraction']:6.2%} | {row['value_at_close']:6.2f} | "
            f"{row['net_pnl']:9.2f} | {row['reason']:<3} | {row['multiple']:5.2f}"
        )

    def write_results(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        trade_path = self.output_dir / "trades.csv"
        daily_path = self.output_dir / "daily_equity.csv"
        with trade_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.trades[0]) if self.trades else [])
            if self.trades:
                writer.writeheader()
                writer.writerows(self.trades)
        with daily_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(self.daily[0]) if self.daily else [])
            if self.daily:
                writer.writeheader()
                writer.writerows(self.daily)
        return trade_path, daily_path

    def print_summary(self):
        ending = self.daily[-1]["equity"]
        net_profit = ending - self.cfg.initial_cash
        wins = sum(t["net_pnl"] > 0 for t in self.trades)
        peak = 0.0
        max_drawdown = 0.0
        for row in self.daily:
            peak = max(peak, row["equity"])
            if peak:
                max_drawdown = min(max_drawdown, row["equity"] / peak - 1)
        years = (self.cfg.end_date - self.cfg.start_date).days / 365.2425
        cagr = (ending / self.cfg.initial_cash) ** (1 / years) - 1 if ending > 0 else -1
        avg_pnl = sum(t["net_pnl"] for t in self.trades) / len(self.trades) if self.trades else 0
        total_fees = sum(t["fees"] for t in self.trades)
        print("\n=== LOCAL QQQ 21/7 CALL-SPREAD SUMMARY ===")
        print(f"Completed trades : {len(self.trades):,}")
        print(f"Win rate         : {wins / len(self.trades):.2%}" if self.trades else "Win rate         : n/a")
        print(f"Average net P/L  : ${avg_pnl:,.2f}")
        print(f"Ending equity    : ${ending:,.2f}")
        print(f"Total net P/L    : ${net_profit:,.2f}")
        print(f"CAGR             : {cagr:.2%}")
        print(f"Max drawdown     : {max_drawdown:.2%}")
        print(f"Total fees       : ${total_fees:,.2f}")
        print(f"Contract floor   : {self.contract_floor}")
        qqq_allocation = (
            f"{self.cfg.qqq_equity_fraction:.0%} target (buy-only)"
            if self.cfg.hold_idle_cash_in_qqq
            else "disabled"
        )
        print(f"QQQ allocation   : {qqq_allocation}")
        sizing = (
            f"fixed {self.cfg.fixed_contracts}"
            if self.cfg.fixed_contracts is not None
            else f"dynamic {self.cfg.entry_equity_fraction:.2%} with ratchet"
        )
        print(f"Position sizing  : {sizing}")
        print(
            "TP/SL checks     : "
            + ("daily" if self.cfg.check_tp_sl_daily else "Friday/week-end")
        )
        print(f"Skipped entries  : {self.skipped_entries}")
        print(f"VIX-filtered     : {self.vix_filtered_entries}")
        print(f"Rule-filtered    : {self.rule_filtered_entries}")
        if self.skipped_entry_dates:
            print(
                "Missing-chain days: "
                + ", ".join(day.isoformat() for day in self.skipped_entry_dates)
            )
        print(f"Open positions   : {len(self.positions)}")

    def write_equity_chart(self) -> Path:
        """Write the daily portfolio equity curve as a standalone SVG file."""
        if not self.daily:
            raise RuntimeError("Cannot draw an equity chart without daily results")

        width, height = 1200, 700
        left, right, top, bottom = 105, 35, 55, 80
        plot_width = width - left - right
        plot_height = height - top - bottom
        equities = [float(row["equity"]) for row in self.daily]
        minimum = min(equities)
        maximum = max(equities)
        padding = max((maximum - minimum) * 0.05, maximum * 0.01, 1.0)
        y_min = minimum - padding
        y_max = maximum + padding
        y_span = y_max - y_min
        point_count = len(equities)

        def x_for(index: int) -> float:
            return left + plot_width * index / max(point_count - 1, 1)

        def y_for(value: float) -> float:
            return top + plot_height * (y_max - value) / y_span

        points = " ".join(
            f"{x_for(index):.2f},{y_for(value):.2f}"
            for index, value in enumerate(equities)
        )
        elements = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}">',
            '<rect width="100%" height="100%" fill="#ffffff"/>',
            f'<text x="{width / 2:.0f}" y="31" text-anchor="middle" '
            'font-family="sans-serif" font-size="22" font-weight="bold" '
            'fill="#172033">QQQ Call-Spread Backtest — Portfolio Equity</text>',
        ]

        for tick in range(6):
            value = y_min + y_span * tick / 5
            y = y_for(value)
            elements.extend(
                [
                    f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" '
                    f'y2="{y:.2f}" stroke="#dce2ea" stroke-width="1"/>',
                    f'<text x="{left - 12}" y="{y + 5:.2f}" text-anchor="end" '
                    'font-family="sans-serif" font-size="13" fill="#4b5563">'
                    f'${value:,.0f}</text>',
                ]
            )

        date_tick_count = min(7, point_count)
        for tick in range(date_tick_count):
            index = round((point_count - 1) * tick / max(date_tick_count - 1, 1))
            x = x_for(index)
            label = self.daily[index]["date"]
            elements.extend(
                [
                    f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" '
                    f'y2="{height - bottom}" stroke="#eef1f5" stroke-width="1"/>',
                    f'<text x="{x:.2f}" y="{height - bottom + 28}" text-anchor="middle" '
                    'font-family="sans-serif" font-size="13" fill="#4b5563">'
                    f'{label}</text>',
                ]
            )

        elements.extend(
            [
                f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height - bottom}" '
                'stroke="#6b7280" stroke-width="1.5"/>',
                f'<line x1="{left}" y1="{height - bottom}" x2="{width - right}" '
                f'y2="{height - bottom}" stroke="#6b7280" stroke-width="1.5"/>',
                f'<polyline points="{points}" fill="none" stroke="#2563eb" '
                'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>',
                f'<circle cx="{x_for(point_count - 1):.2f}" '
                f'cy="{y_for(equities[-1]):.2f}" r="4" fill="#2563eb"/>',
                f'<text x="{width / 2:.0f}" y="{height - 18}" text-anchor="middle" '
                'font-family="sans-serif" font-size="14" fill="#374151">Date</text>',
                f'<text x="22" y="{height / 2:.0f}" text-anchor="middle" '
                'font-family="sans-serif" font-size="14" fill="#374151" '
                f'transform="rotate(-90 22 {height / 2:.0f})">Portfolio equity</text>',
                '</svg>',
            ]
        )

        chart_path = self.output_dir / "equity_chart.svg"
        chart_path.write_text("\n".join(elements) + "\n", encoding="utf-8")
        return chart_path

    def run(self):
        days = self.trading_days()
        if not days:
            raise RuntimeError("No QQQ underlying data found for the requested period")
        if self.verbose:
            print(self.LOG_HEADER)
        for index, (day, qqq_price) in enumerate(days):
            week_end = self.is_week_end(days, index)
            final = day == days[-1][0]
            quotes = self.quotes(day)
            self.process_exits(day, qqq_price, quotes, week_end, final)

            entry_vix_ok = self.entry_vix_allowed(days, index)
            if week_end and not final and entry_vix_ok:
                quotes = self.quotes(day)
                prior_lookback_return = None
                if index >= self.cfg.return_lookback + 1:
                    prior_close = days[index - 1][1]
                    lookback_close = days[
                        index - self.cfg.return_lookback - 1
                    ][1]
                    prior_lookback_return = prior_close / lookback_close - 1
                self.open_spread(
                    day, qqq_price, quotes, prior_lookback_return
                )
            elif week_end and not final:
                self.vix_filtered_entries += 1
            self.sweep_cash(qqq_price)
            quotes = self.quotes(day)
            equity = self.portfolio_value(qqq_price, quotes)
            self.daily.append(
                {
                    "date": day.isoformat(),
                    "qqq_close": qqq_price,
                    "cash": self.cash,
                    "qqq_shares": self.qqq_shares,
                    "open_spreads": len(self.positions),
                    "equity": equity,
                }
            )
        paths = self.write_results()
        chart_path = self.write_equity_chart()
        self.print_summary()
        print(f"Trades CSV       : {paths[0]}")
        print(f"Daily equity CSV : {paths[1]}")
        print(f"Equity chart     : {chart_path}")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parent
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=root / "data/historical_options/qqq",
    )
    parser.add_argument("--output-dir", type=Path, default=root / "results/qqq_local")
    parser.add_argument("--entry-fraction", type=float, default=0.01)
    parser.add_argument("--initial-cash", type=float, default=12_330.0)
    parser.add_argument("--long-delta", type=float, default=0.21)
    parser.add_argument("--short-delta", type=float, default=0.07)
    parser.add_argument(
        "--contracts",
        type=int,
        default=10,
        help="Fixed contracts per trade (default: 10); use 0 for dynamic sizing",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2013, 1, 2))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 6, 30))
    parser.add_argument("--hold-qqq", action="store_true")
    parser.add_argument(
        "--qqq-equity-percentage",
        type=float,
        default=50.0,
        help=(
            "Target percentage of equity held in QQQ when --hold-qqq is enabled; "
            "QQQ is sold only as needed to fund a trade (default: 50)"
        ),
    )
    parser.add_argument("--use-vix", action="store_true")
    parser.add_argument("--vix-entry-min", type=float, default=8.0)
    parser.add_argument("--vix-entry-max", type=float, default=27.0)
    parser.add_argument("--vix-exit", type=float, default=28.0)
    parser.add_argument(
        "--vix-trend-threshold",
        type=float,
        default=None,
        help=(
            "Skip an entry only when prior-day VIX is above this level and "
            "above its trailing five-session average; does not enable VIX exits"
        ),
    )
    parser.add_argument("--debit-ratio-min", type=float, default=None)
    parser.add_argument("--debit-ratio-max", type=float, default=None)
    parser.add_argument("--skip-return-min", type=float, default=None)
    parser.add_argument("--skip-return-max", type=float, default=None)
    parser.add_argument("--return-lookback", type=int, default=20)
    parser.add_argument("--max-open-debit-fraction", type=float, default=None)
    parser.add_argument(
        "--exit-check-frequency",
        choices=("friday", "daily"),
        default="friday",
        help="Check take-profit/stop-loss daily or only at week-end",
    )
    parser.add_argument("--quiet", action="store_true", help="Print only the final summary")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.contracts < 0:
        raise SystemExit("--contracts must be 0 or a positive integer")
    if not 0 <= args.qqq_equity_percentage <= 100:
        raise SystemExit("--qqq-equity-percentage must be between 0 and 100")
    config = Config(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_cash=args.initial_cash,
        fixed_contracts=args.contracts or None,
        entry_equity_fraction=args.entry_fraction,
        long_delta=args.long_delta,
        short_delta=args.short_delta,
        hold_idle_cash_in_qqq=args.hold_qqq,
        qqq_equity_fraction=args.qqq_equity_percentage / 100,
        use_vix_rules=args.use_vix,
        vix_trend_entry_threshold=args.vix_trend_threshold,
        entry_vix_min=args.vix_entry_min,
        entry_vix_max=args.vix_entry_max,
        vix_exit=args.vix_exit,
        debit_ratio_min=args.debit_ratio_min,
        debit_ratio_max=args.debit_ratio_max,
        skip_return_min=args.skip_return_min,
        skip_return_max=args.skip_return_max,
        return_lookback=args.return_lookback,
        max_open_debit_fraction=args.max_open_debit_fraction,
        check_tp_sl_daily=args.exit_check_frequency == "daily",
    )
    QqqCallSpreadBacktest(
        args.data_dir, args.output_dir, config, verbose=not args.quiet
    ).run()


if __name__ == "__main__":
    main()
