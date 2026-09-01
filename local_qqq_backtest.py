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
    start_date: date = date(2013, 1, 2)
    end_date: date = date(2025, 6, 30)
    initial_cash: float = 12_330.0
    fixed_contracts: Optional[int] = 10
    entry_equity_fraction: float = 0.01
    target_dte: int = 35
    max_dte: int = 42
    long_delta: float = 0.21
    short_delta: float = 0.07
    stop_return: float = -0.84
    profit_return: float = 4.20
    fee_per_contract_per_leg: float = 0.65
    hold_idle_cash_in_qqq: bool = False
    use_vix_rules: bool = False
    entry_vix_min: float = 8.0
    entry_vix_max: float = 27.0
    vix_exit: float = 28.0


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
        self.skipped_entry_dates: list[date] = []
        self.vix = self._load_vix() if config.use_vix_rules else {}

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
        shares = max(0, math.floor(self.cash / qqq_price))
        self.qqq_shares += shares
        self.cash -= shares * qqq_price

    def open_spread(self, day: date, qqq_price: float, quotes: dict):
        selected = self.choose_spread(day)
        if selected is None:
            self.skipped_entries += 1
            self.skipped_entry_dates.append(day)
            return
        long_leg, short_leg, expiry, debit = selected
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
            elif week_end and value is not None:
                spread_return = (value - position.debit) / position.debit
                if self.cfg.use_vix_rules and vix is not None and vix >= self.cfg.vix_exit:
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
        sizing = (
            f"fixed {self.cfg.fixed_contracts}"
            if self.cfg.fixed_contracts is not None
            else f"dynamic {self.cfg.entry_equity_fraction:.2%} with ratchet"
        )
        print(f"Position sizing  : {sizing}")
        print(f"Skipped entries  : {self.skipped_entries}")
        if self.skipped_entry_dates:
            print(
                "Missing-chain days: "
                + ", ".join(day.isoformat() for day in self.skipped_entry_dates)
            )
        print(f"Open positions   : {len(self.positions)}")

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

            entry_vix_ok = (
                not self.cfg.use_vix_rules
                or (
                    day in self.vix
                    and self.cfg.entry_vix_min <= self.vix[day] <= self.cfg.entry_vix_max
                )
            )
            if week_end and not final and entry_vix_ok:
                quotes = self.quotes(day)
                self.open_spread(day, qqq_price, quotes)
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
        self.print_summary()
        print(f"Trades CSV       : {paths[0]}")
        print(f"Daily equity CSV : {paths[1]}")


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
    parser.add_argument(
        "--contracts",
        type=int,
        default=10,
        help="Fixed contracts per trade (default: 10); use 0 for dynamic sizing",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2013, 1, 2))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 6, 30))
    parser.add_argument("--hold-qqq", action="store_true")
    parser.add_argument("--use-vix", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="Print only the final summary")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.contracts < 0:
        raise SystemExit("--contracts must be 0 or a positive integer")
    config = Config(
        start_date=args.start_date,
        end_date=args.end_date,
        fixed_contracts=args.contracts or None,
        entry_equity_fraction=args.entry_fraction,
        hold_idle_cash_in_qqq=args.hold_qqq,
        use_vix_rules=args.use_vix,
    )
    QqqCallSpreadBacktest(
        args.data_dir, args.output_dir, config, verbose=not args.quiet
    ).run()


if __name__ == "__main__":
    main()
