#!/usr/bin/env python3
"""Local test of the increasing-contract QQQ 21/7 call-spread strategy."""

from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from pathlib import Path

from local_qqq_backtest import Config, QqqCallSpreadBacktest


class IncreasingContractQqqBacktest(QqqCallSpreadBacktest):
    """Local equivalent of bull_call_spread_codex_v2_sane.py."""

    MAX_DELTA_DISTANCE = 0.03

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.delta_rejections = 0
        self.invalid_quote_rejections = 0
        self.insufficient_cash_rejections = 0

    def entry_vix_allowed(self, days, index: int) -> bool:
        if not self.cfg.use_vix_rules:
            return True
        # Daily files contain one observation per session. Use that session's
        # VIX value as the local EOD approximation of QC's 15:45 observation.
        day = days[index][0]
        value = self.vix.get(day)
        return (
            value is not None
            and self.cfg.entry_vix_min <= value <= self.cfg.entry_vix_max
        )

    def choose_spread(self, day: date):
        rows = self.db.execute(
            """
            SELECT contract_id, expiry_date, strike, bid, ask, delta
            FROM options
            WHERE quote_date = ? AND type = 'call'
              AND expiry_date > ? AND expiry_date <= ?
              AND delta > 0 AND bid > 0 AND ask > 0 AND ask >= bid
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
        long_leg = min(
            eligible, key=lambda row: abs(float(row[5]) - self.cfg.long_delta)
        )
        short_eligible = [
            row for row in eligible if float(row[2]) > float(long_leg[2])
        ]
        if not short_eligible:
            return None
        short_leg = min(
            short_eligible,
            key=lambda row: abs(float(row[5]) - self.cfg.short_delta),
        )

        long_delta = float(long_leg[5])
        short_delta = float(short_leg[5])
        if (
            abs(long_delta - self.cfg.long_delta) > self.MAX_DELTA_DISTANCE
            or abs(short_delta - self.cfg.short_delta) > self.MAX_DELTA_DISTANCE
        ):
            self.delta_rejections += 1
            return None

        debit = float(long_leg[4]) - float(short_leg[3])
        width = float(short_leg[2]) - float(long_leg[2])
        if not math.isfinite(debit) or debit <= 0 or debit >= width:
            self.invalid_quote_rejections += 1
            return None
        return long_leg, short_leg, target_expiry, debit

    def open_spread(self, day, qqq_price, quotes, prior_lookback_return=None):
        positions_before = len(self.positions)
        cash_before = self.cash
        floor_before = self.contract_floor
        super().open_spread(day, qqq_price, quotes, prior_lookback_return=None)

        if len(self.positions) == positions_before:
            return

        # The shared engine models filled EOD debit orders immediately. Undo an
        # entry that could not have been paid from cash; never reduce the
        # strategy's persistent contract floor to force an affordable trade.
        position = self.positions[-1]
        if self.cash >= -1e-9:
            return
        self.positions.pop()
        self.cash = cash_before
        self.next_position_id -= 1
        self.contract_floor = floor_before
        self.insufficient_cash_rejections += 1
        self.skipped_entries += 1

    def process_exits(self, day, qqq_price, quotes, week_end, final):
        vix = self.vix.get(day) if self.cfg.use_vix_rules else None
        for position in list(self.positions):
            value = self.liquidation_value(position, quotes)
            if value is not None:
                position.last_value = value

            reason = None
            dte = (position.expiration - day).days
            if value is not None:
                spread_return = (value - position.debit) / position.debit
                if vix is not None and vix >= self.cfg.vix_exit:
                    reason = "VIX"
                elif spread_return <= self.cfg.stop_return:
                    reason = "SL"
                elif spread_return >= self.cfg.profit_return:
                    reason = "TP"
                elif dte <= 1:
                    reason = "EXP"
            elif day >= position.expiration:
                # Missing expiration quote: settle at bounded intrinsic value.
                value = max(
                    0.0,
                    min(
                        position.short_strike - position.long_strike,
                        max(qqq_price - position.long_strike, 0.0)
                        - max(qqq_price - position.short_strike, 0.0),
                    ),
                )
                reason = "EXP"

            if reason:
                self.close_position(position, day, qqq_price, value, reason)

    def print_summary(self):
        # The shared engine calls every failed selection a missing-chain day;
        # here most are intentionally rejected by MAX_DELTA_DISTANCE.
        selection_dates = self.skipped_entry_dates
        self.skipped_entry_dates = []
        super().print_summary()
        self.skipped_entry_dates = selection_dates
        print(f"Delta rejects    : {self.delta_rejections}")
        print(f"Invalid quotes   : {self.invalid_quote_rejections}")
        print(f"Cash rejects     : {self.insufficient_cash_rejections}")
        other_selection_rejects = max(
            0,
            self.skipped_entries
            - self.delta_rejections
            - self.invalid_quote_rejections
            - self.insufficient_cash_rejections,
        )
        print(f"No-chain rejects : {other_selection_rejects}")

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
            quotes = self.quotes(day)
            self.process_exits(day, qqq_price, quotes, week_end=False, final=False)

            # Match QC exactly: Friday only, with no Thursday holiday substitute.
            if day.weekday() == 4 and self.entry_vix_allowed(days, index):
                self.open_spread(day, qqq_price, self.quotes(day))
            elif day.weekday() == 4:
                self.vix_filtered_entries += 1

            quotes = self.quotes(day)
            equity = self.portfolio_value(qqq_price, quotes)
            self.daily.append(
                {
                    "date": day.isoformat(),
                    "qqq_close": qqq_price,
                    "cash": self.cash,
                    "qqq_shares": 0,
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
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=root / "data/historical_options/qqq",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=root / "results/qqq_local_v2_sane",
    )
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2013, 1, 2))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 6, 30))
    parser.add_argument("--initial-cash", type=float, default=25_000.0)
    parser.add_argument("--entry-fraction", type=float, default=0.007)
    parser.add_argument("--entry-vix-min", type=float, default=8.0)
    parser.add_argument("--entry-vix-max", type=float, default=27.0)
    parser.add_argument("--vix-exit", type=float, default=28.0)
    parser.add_argument(
        "--use-vix-rules",
        action="store_true",
        help="enable the VIX entry filter and VIX exit rule (disabled by default)",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if args.initial_cash <= 0:
        raise SystemExit("--initial-cash must be positive")
    if not 0 < args.entry_fraction < 1:
        raise SystemExit("--entry-fraction must be between 0 and 1")
    if args.entry_vix_min > args.entry_vix_max:
        raise SystemExit("--entry-vix-min cannot exceed --entry-vix-max")

    config = Config(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_cash=args.initial_cash,
        fixed_contracts=None,
        entry_equity_fraction=args.entry_fraction,
        target_dte=35,
        max_dte=42,
        long_delta=0.21,
        short_delta=0.07,
        stop_return=-0.84,
        profit_return=4.20,
        fee_per_contract_per_leg=0.65,
        hold_idle_cash_in_qqq=False,
        use_vix_rules=args.use_vix_rules,
        entry_vix_min=args.entry_vix_min,
        entry_vix_max=args.entry_vix_max,
        vix_exit=args.vix_exit,
        check_tp_sl_daily=True,
    )
    IncreasingContractQqqBacktest(
        args.data_dir, args.output_dir, config, verbose=not args.quiet
    ).run()


if __name__ == "__main__":
    main()
