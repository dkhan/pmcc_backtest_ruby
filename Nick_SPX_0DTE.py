from AlgorithmImports import *
from datetime import date, datetime, timedelta, time as clock_time


class NickSpxZeroDteV1(QCAlgorithm):
    """SPXW 0DTE gap-reversal credit-spread strategy."""

    def initialize(self):
        # Change only these dates when selecting a different test period.
        self.set_start_date(2026, 1, 1)
        self.set_end_date(2026, 5, 22)
        self.set_cash(50_000)
        self.set_time_zone(TimeZones.NEW_YORK)
        self.set_brokerage_model(BrokerageName.QUANT_CONNECT_BROKERAGE)

        index = self.add_index("SPX", Resolution.MINUTE)
        self.spx = index.symbol

        option = self.add_index_option(self.spx, "SPXW", Resolution.MINUTE)
        self.option_symbol = option.symbol
        option.set_filter(lambda u: u.include_weeklys().expiration(0, 0).strikes(-200, 200))

        # Position and entry rules.
        self.spread_quantity = 2
        self.last_entry_time = clock_time(12, 30)
        self.minimum_short_otm_percent = 0.01
        self.maximum_hedge_otm_percent = 0.03

        # Signal candle timeframe. Supported values: 5, 15, or 30 minutes.
        # All opening-candle confirmation and reversal rules use this timeframe.
        self.bar_minutes = 5
        if self.bar_minutes not in (5, 15, 30):
            raise ValueError("bar_minutes must be 5, 15, or 30")

        # Gap-closure filter. Set to False to allow entries even if SPX has
        # already touched the previous session's closing price.
        self.skip_if_gap_closes = False

        # HARD premium limits. These prevent another April 7, 2025 outlier.
        self.minimum_short_price = 0.35
        self.maximum_short_price = 1.00
        self.maximum_hedge_price = 0.25
        self.minimum_net_credit = 0.35
        self.maximum_net_credit = 0.75
        self.short_target_price = 0.60
        self.hedge_target_price = 0.10
        self.credit_target = 0.50

        # Adjustable stop-loss multiple. A $0.50 opening credit with 3.0
        # triggers when the estimated spread-closing debit reaches $1.50.
        self.stop_loss_multiple = 100.0

        # Scheduled high-impact days on which the strategy does not trade.
        self.fomc_dates = self.build_fomc_dates()
        self.pmi_dates = self.build_ism_pmi_dates(date(2020, 1, 1), date(2030, 12, 31))

        # Active spread.
        self.active_short_symbol = None
        self.active_long_symbol = None
        self.active_short_strike = None
        self.active_long_strike = None
        self.active_spread_type = None
        self.active_entry_credit = None
        self.stop_order_submitted = False

        # Combined-spread statistics. QC's normal win rate counts each leg.
        self.spread_starting_equity = None
        self.spread_trade_count = 0
        self.spread_win_count = 0
        self.spread_loss_count = 0
        self.spread_total_profit = 0.0

        self.consolidator = TradeBarConsolidator(
            timedelta(minutes=self.bar_minutes)
        )
        self.consolidator.data_consolidated += self.on_signal_bar
        self.subscription_manager.add_consolidator(self.spx, self.consolidator)

        self.schedule.on(
            self.date_rules.every_day(self.spx),
            self.time_rules.at(9, 29),
            self.prepare_for_session
        )
        self.schedule.on(
            self.date_rules.every_day(self.spx),
            self.time_rules.at(15, 45),
            self.check_spread_before_close
        )
        self.reset_daily_state()

    def reset_daily_state(self):
        self.previous_close = None
        self.today_open = None
        self.gap_direction = None
        self.first_bar_seen = False
        self.first_bar_valid = False
        self.skip_today = False
        self.traded_today = False
        self.previous_bar = None
        self.pending_signal = None
        self.signal_time = None
        self.signal_spx_price = None

    def prepare_for_session(self):
        # The prior 0DTE spread has now settled; record it as one trade.
        if self.spread_starting_equity is not None:
            self.finalize_previous_spread()

        self.reset_daily_state()

        if self.active_short_symbol is not None:
            if self.portfolio[self.active_short_symbol].quantity == 0:
                self.clear_active_spread()

        today = self.time.date()
        if today in self.fomc_dates or today in self.pmi_dates:
            self.skip_today = True
            return

        history = list(self.history[TradeBar](self.spx, 5, Resolution.DAILY))
        completed = [bar for bar in history if bar.end_time.date() < today]
        if not completed:
            self.skip_today = True
            return
        self.previous_close = completed[-1].close

    def on_signal_bar(self, sender, bar):
        now = self.time.time()
        if self.skip_today or self.traded_today or self.previous_close is None:
            return
        # The first regular-session signal candle completes at 9:30 plus the
        # selected timeframe: 9:35, 9:45, or 10:00 ET.
        opening_bar_end = (
            datetime.combine(self.time.date(), clock_time(9, 30))
            + timedelta(minutes=self.bar_minutes)
        ).time()
        if now < opening_bar_end or now > self.last_entry_time:
            return

        # The first candle must continue in the direction of the overnight gap.
        if not self.first_bar_seen:
            self.first_bar_seen = True
            self.today_open = bar.open
            if self.today_open > self.previous_close:
                self.gap_direction = "UP"
                self.first_bar_valid = bar.close > bar.open
            elif self.today_open < self.previous_close:
                self.gap_direction = "DOWN"
                self.first_bar_valid = bar.close < bar.open
            else:
                self.skip_today = True
                return

            if not self.first_bar_valid:
                self.skip_today = True
                return

            # opening gap closes, the complete day is permanently disqualified.
            # A touch of yesterday's close counts as a closed gap. Once the
            # opening gap closes, the complete day is permanently disqualified.
            # opening gap closes, the complete day is permanently disqualified.
            if self.gap_has_closed(bar):
                self.skip_today = True
                return

            self.previous_bar = bar
            return

        if self.previous_bar is None:
            return

        # This check must happen before looking for a reversal entry. It uses
        # the bar's full high/low, not merely its closing price.
        if self.gap_has_closed(bar):
            self.skip_today = True
            self.pending_signal = None
            return

        is_green = bar.close > bar.open
        is_red = bar.close < bar.open

        if self.gap_direction == "DOWN" and is_green and bar.close > self.previous_bar.high:
            self.pending_signal = "BULL_PUT"
        elif self.gap_direction == "UP" and is_red and bar.close < self.previous_bar.low:
            self.pending_signal = "BEAR_CALL"

        if self.pending_signal is not None:
            self.signal_time = self.time
            self.signal_spx_price = bar.close
        self.previous_bar = bar

    def gap_has_closed(self, bar):
        """Return True once SPX touches the previous session's close."""
        if not self.skip_if_gap_closes:
            return False
        if self.gap_direction == "DOWN":
            return bar.high >= self.previous_close
        if self.gap_direction == "UP":
            return bar.low <= self.previous_close
        return False

    def on_data(self, data):
        # Monitor an existing position every minute. The stop is based on the
        # complete spread price, not on either option leg by itself.
        self.check_three_x_stop()

        if self.pending_signal is None or self.skip_today or self.traded_today:
            return
        if self.time.time() > self.last_entry_time:
            self.pending_signal = None
            return

        chain = data.option_chains.get(self.option_symbol)
        if chain is None:
            return

        contracts = [c for c in chain if c.expiry.date() == self.time.date()]
        selected = self.select_spread(contracts, self.pending_signal, self.signal_spx_price)
        if selected is None:
            return

        short_contract, long_contract, credit = selected
        spread_type = self.pending_signal
        width = abs(short_contract.strike - long_contract.strike)
        short_otm = abs(short_contract.strike - self.signal_spx_price) / self.signal_spx_price
        hedge_otm = abs(long_contract.strike - self.signal_spx_price) / self.signal_spx_price
        total_credit = credit * 100 * self.spread_quantity
        maximum_loss = (width - credit) * 100 * self.spread_quantity

        legs = [
            Leg.create(short_contract.symbol, -1),
            Leg.create(long_contract.symbol, 1)
        ]
        tag = (
            f"OPEN {spread_type} | SPX {self.signal_spx_price:.2f} | "
            f"SELL {short_contract.strike:.0f} @ {short_contract.bid_price:.2f} | "
            f"BUY {long_contract.strike:.0f} @ {long_contract.ask_price:.2f} | "
            f"CREDIT {credit:.2f}"
        )
        tickets = self.combo_market_order(legs, self.spread_quantity, tag=tag)
        if any(ticket.status == OrderStatus.INVALID for ticket in tickets):
            return

        self.active_short_symbol = short_contract.symbol
        self.active_long_symbol = long_contract.symbol
        self.active_short_strike = short_contract.strike
        self.active_long_strike = long_contract.strike
        self.active_spread_type = spread_type
        self.active_entry_credit = credit
        self.stop_order_submitted = False
        self.spread_starting_equity = float(self.portfolio.total_portfolio_value)
        self.traded_today = True
        self.pending_signal = None

        # Only actual trades are logged.
        self.log(
            f"OPEN TRADE | {self.signal_time:%Y-%m-%d %H:%M} ET | {spread_type} | "
            f"SPX {self.signal_spx_price:.2f} | "
            f"SELL {short_contract.strike:.0f} @ {short_contract.bid_price:.2f} | "
            f"BUY {long_contract.strike:.0f} @ {long_contract.ask_price:.2f} | "
            f"NET {credit:.2f} | TOTAL ${total_credit:,.0f} | "
            f"SHORT OTM {short_otm:.2%} | HEDGE OTM {hedge_otm:.2%} | "
            f"WIDTH {width:.0f} | MAX RISK ${maximum_loss:,.0f}"
        )

    def select_spread(self, contracts, signal, spot):
        """Return the valid spread closest to the desired $0.60/$0.10/$0.50 prices."""
        if signal == "BULL_PUT":
            short_limit = spot * (1 - self.minimum_short_otm_percent)
            hedge_limit = spot * (1 - self.maximum_hedge_otm_percent)
            short_candidates = [
                c for c in contracts
                if c.right == OptionRight.PUT
                and hedge_limit < c.strike <= short_limit
                and self.minimum_short_price <= c.bid_price <= self.maximum_short_price
            ]
        else:
            short_limit = spot * (1 + self.minimum_short_otm_percent)
            hedge_limit = spot * (1 + self.maximum_hedge_otm_percent)
            short_candidates = [
                c for c in contracts
                if c.right == OptionRight.CALL
                and short_limit <= c.strike < hedge_limit
                and self.minimum_short_price <= c.bid_price <= self.maximum_short_price
            ]

        best = None
        best_score = float("inf")
        for short in short_candidates:
            for long in contracts:
                if long.right != short.right:
                    continue
                if long.ask_price <= 0 or long.ask_price > self.maximum_hedge_price:
                    continue

                if signal == "BULL_PUT":
                    valid_strike = hedge_limit <= long.strike < short.strike
                else:
                    valid_strike = short.strike < long.strike <= hedge_limit
                if not valid_strike:
                    continue

                credit = short.bid_price - long.ask_price
                if not self.minimum_net_credit <= credit <= self.maximum_net_credit:
                    continue

                score = (
                    abs(short.bid_price - self.short_target_price)
                    + abs(long.ask_price - self.hedge_target_price)
                    + abs(credit - self.credit_target)
                )
                if score < best_score:
                    best_score = score
                    best = (short, long, credit)
        return best

    def check_three_x_stop(self):
        """
        Close the complete spread when its estimated closing debit reaches
        the configured multiple of the opening credit.

        Example: a spread opened for $0.50 is stopped at an estimated $1.50.
        Closing debit = short option ask - long option bid.
        """
        if (
            self.active_short_symbol is None
            or self.active_long_symbol is None
            or self.active_entry_credit is None
            or self.stop_order_submitted
        ):
            return

        short_qty = self.portfolio[self.active_short_symbol].quantity
        long_qty = self.portfolio[self.active_long_symbol].quantity
        if short_qty == 0 and long_qty == 0:
            return

        short_security = self.securities[self.active_short_symbol]
        long_security = self.securities[self.active_long_symbol]
        short_ask = float(short_security.ask_price)
        long_bid = float(long_security.bid_price)

        # Do not act on incomplete or stale zero quotes.
        if short_ask <= 0 or long_bid < 0:
            return

        closing_debit = max(short_ask - long_bid, 0)
        stop_price = (
            self.active_entry_credit
            * self.stop_loss_multiple
        )
        if closing_debit < stop_price:
            return

        if not (
            short_qty < 0
            and long_qty > 0
            and abs(short_qty) == abs(long_qty)
        ):
            return

        closing_legs = [
            Leg.create(self.active_short_symbol, 1),
            Leg.create(self.active_long_symbol, -1)
        ]
        tickets = self.combo_market_order(
            closing_legs,
            int(abs(short_qty)),
            tag=(
                f"{self.stop_loss_multiple:g}X STOP | "
                f"ENTRY CREDIT {self.active_entry_credit:.2f} | "
                f"EST CLOSE {closing_debit:.2f}"
            )
        )
        if any(ticket.status == OrderStatus.INVALID for ticket in tickets):
            return

        self.stop_order_submitted = True
        self.log(
            f"STOP LOSS | {self.time:%Y-%m-%d %H:%M} ET | "
            f"{self.active_spread_type} | "
            f"ENTRY CREDIT {self.active_entry_credit:.2f} | "
            f"{self.stop_loss_multiple:g}X LEVEL {stop_price:.2f} | "
            f"ESTIMATED CLOSE {closing_debit:.2f}"
        )
        self.clear_active_spread()

    def check_spread_before_close(self):
        """At 3:45 ET close only when the sold option is ITM; winners expire."""
        if self.active_short_symbol is None or self.active_long_symbol is None:
            return

        short_qty = self.portfolio[self.active_short_symbol].quantity
        long_qty = self.portfolio[self.active_long_symbol].quantity
        if short_qty == 0 and long_qty == 0:
            self.clear_active_spread()
            return

        spot = self.securities[self.spx].price
        if spot <= 0:
            return
        short_is_itm = (
            spot < self.active_short_strike
            if self.active_spread_type == "BULL_PUT"
            else spot > self.active_short_strike
        )
        if not short_is_itm:
            return

        if short_qty < 0 and long_qty > 0 and abs(short_qty) == abs(long_qty):
            legs = [
                Leg.create(self.active_short_symbol, 1),
                Leg.create(self.active_long_symbol, -1)
            ]
            tickets = self.combo_market_order(
                legs, int(abs(short_qty)), tag="CLOSE ITM SPREAD AT 3:45 PM ET"
            )
        else:
            tickets = []
            if short_qty != 0:
                tickets.append(self.market_order(self.active_short_symbol, -short_qty))
            if long_qty != 0:
                tickets.append(self.market_order(self.active_long_symbol, -long_qty))

        if any(ticket.status == OrderStatus.INVALID for ticket in tickets):
            return
        self.log(
            f"CLOSE TRADE | {self.time:%Y-%m-%d %H:%M} ET | "
            f"{self.active_spread_type} | SPX {spot:.2f} | "
            f"SHORT {self.active_short_strike:.0f} ITM | LONG {self.active_long_strike:.0f}"
        )
        self.clear_active_spread()

    def clear_active_spread(self):
        self.active_short_symbol = None
        self.active_long_symbol = None
        self.active_short_strike = None
        self.active_long_strike = None
        self.active_spread_type = None
        self.active_entry_credit = None
        self.stop_order_submitted = False

    def finalize_previous_spread(self):
        ending_equity = float(self.portfolio.total_portfolio_value)
        profit = ending_equity - self.spread_starting_equity
        self.spread_trade_count += 1
        self.spread_total_profit += profit
        if profit > 0:
            self.spread_win_count += 1
        elif profit < 0:
            self.spread_loss_count += 1
        self.spread_starting_equity = None
        self.update_spread_statistics()

    def update_spread_statistics(self):
        win_rate = (
            100 * self.spread_win_count / self.spread_trade_count
            if self.spread_trade_count else 0
        )
        self.set_summary_statistic("Spread Trades", str(self.spread_trade_count))
        self.set_summary_statistic("Spread Wins", str(self.spread_win_count))
        self.set_summary_statistic("Spread Losses", str(self.spread_loss_count))
        self.set_summary_statistic("Spread Win Rate", f"{win_rate:.1f}%")
        self.set_summary_statistic("Spread Total P&L", f"${self.spread_total_profit:,.2f}")

    def on_end_of_algorithm(self):
        if self.spread_starting_equity is not None:
            self.finalize_previous_spread()

    @staticmethod
    def build_fomc_dates():
        """Scheduled FOMC decision dates (not minutes-release dates)."""
        raw = """
        2020-01-29 2020-03-03 2020-03-15 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
        2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
        2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
        2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
        2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
        2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
        2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28 2026-12-09
        """
        return {datetime.strptime(x, "%Y-%m-%d").date() for x in raw.split()}

    @staticmethod
    def build_ism_pmi_dates(start, end):
        """
        ISM Manufacturing and Services PMI days.

        ISM normally releases them on the first and third business days. For
        trading purposes a business day is represented by a weekday, with the
        observed New-Year, Independence-Day, Thanksgiving and Christmas shifts
        below. January has historically had special ISM scheduling, so verified
        January dates for 2020-2026 replace the general calculation.
        """
        fixed_holidays = set()
        for year in range(start.year, end.year + 1):
            # New Year's Day, Independence Day and Christmas (observed).
            for month, day_number in ((1, 1), (7, 4), (12, 25)):
                d = date(year, month, day_number)
                if d.weekday() == 5:
                    d -= timedelta(days=1)
                elif d.weekday() == 6:
                    d += timedelta(days=1)
                fixed_holidays.add(d)
            # Thanksgiving.
            d = date(year, 11, 1)
            while d.weekday() != 3:
                d += timedelta(days=1)
            fixed_holidays.add(d + timedelta(days=21))

            # Labor Day: the first Monday in September. This commonly moves
            # September Manufacturing PMI from Monday to Tuesday.
            d = date(year, 9, 1)
            while d.weekday() != 0:
                d += timedelta(days=1)
            fixed_holidays.add(d)

        dates = set()
        for year in range(start.year, end.year + 1):
            for month in range(1, 13):
                business_days = []
                d = date(year, month, 1)
                while d.month == month and len(business_days) < 3:
                    if d.weekday() < 5 and d not in fixed_holidays:
                        business_days.append(d)
                    d += timedelta(days=1)
                dates.add(business_days[0])
                dates.add(business_days[2])

        # Verified ISM January exceptions/schedules.
        january = {
            2020: (date(2020, 1, 3), date(2020, 1, 7)),
            2021: (date(2021, 1, 5), date(2021, 1, 7)),
            2022: (date(2022, 1, 4), date(2022, 1, 6)),
            2023: (date(2023, 1, 4), date(2023, 1, 6)),
            2024: (date(2024, 1, 3), date(2024, 1, 5)),
            2025: (date(2025, 1, 3), date(2025, 1, 7)),
            2026: (date(2026, 1, 5), date(2026, 1, 7)),
        }
        for year, pair in january.items():
            dates = {d for d in dates if not (d.year == year and d.month == 1)}
            dates.update(pair)
        return {d for d in dates if start <= d <= end}
