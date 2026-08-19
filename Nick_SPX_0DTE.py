from AlgorithmImports import *
from datetime import timedelta, time as clock_time


class NickSpxZeroDteV1(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2026, 1, 1)
        self.set_end_date(2026, 8, 18)

        self.set_cash(5_000_000)
        self.set_time_zone(TimeZones.NEW_YORK)

        self.set_brokerage_model(
            BrokerageName.QUANT_CONNECT_BROKERAGE
        )

        # SPX underlying.
        index = self.add_index(
            "SPX",
            Resolution.MINUTE
        )
        self.spx = index.symbol

        # Daily-expiring SPXW options.
        option = self.add_index_option(
            self.spx,
            "SPXW",
            Resolution.MINUTE
        )
        self.option_symbol = option.symbol

        option.set_filter(
            lambda universe: universe
                .include_weeklys()
                .expiration(0, 0)
                .strikes(-200, 200)
        )

        # Position settings.
        self.spread_quantity = 20
        self.short_target_price = 0.60
        self.hedge_price_discount = 0.50
        self.target_net_credit = 0.50

        # The sold option must be at least 1% OTM.
        self.minimum_otm_percent = 0.01

        # No entries after 12:30 PM ET.
        self.last_entry_time = clock_time(12, 30)

        # Active spread information.
        self.active_short_symbol = None
        self.active_long_symbol = None
        self.active_short_strike = None
        self.active_long_strike = None
        self.active_spread_type = None

        # Build 5-minute SPX candles.
        self.consolidator = TradeBarConsolidator(
            timedelta(minutes=5)
        )
        self.consolidator.data_consolidated += (
            self.on_five_minute_bar
        )
        self.subscription_manager.add_consolidator(
            self.spx,
            self.consolidator
        )

        # Prepare before each session.
        self.schedule.on(
            self.date_rules.every_day(self.spx),
            self.time_rules.at(9, 29),
            self.prepare_for_session
        )

        # Check the spread at 3:45 PM ET.
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
        """Load the previous completed SPX daily close."""
        self.reset_daily_state()

        # Clear expired spread tracking after settlement.
        if (
            self.active_short_symbol is not None
            and self.portfolio[
                self.active_short_symbol
            ].quantity == 0
        ):
            self.clear_active_spread()

        history = list(
            self.history[TradeBar](
                self.spx,
                5,
                Resolution.DAILY
            )
        )

        today = self.time.date()

        completed_bars = [
            bar for bar in history
            if bar.end_time.date() < today
        ]

        if not completed_bars:
            self.skip_today = True
            return

        self.previous_close = completed_bars[-1].close

    def on_five_minute_bar(self, sender, bar):
        """
        Process completed 5-minute SPX candles.

        Use self.time because SPX bar timestamps can use Chicago time,
        while the strategy rules use New York time.
        """
        algorithm_time = self.time
        algorithm_clock = algorithm_time.time()

        if self.skip_today or self.traded_today:
            return

        if self.previous_close is None:
            self.skip_today = True
            return

        # The opening candle completes at 9:35 AM ET.
        if algorithm_clock < clock_time(9, 35):
            return

        # No signals after 12:30 PM ET.
        if algorithm_clock > self.last_entry_time:
            return

        # Process the 9:30-9:35 AM opening candle.
        if not self.first_bar_seen:
            self.first_bar_seen = True
            self.today_open = bar.open

            if self.today_open > self.previous_close:
                self.gap_direction = "UP"

            elif self.today_open < self.previous_close:
                self.gap_direction = "DOWN"

            else:
                self.skip_today = True
                return

            first_bar_is_green = bar.close > bar.open
            first_bar_is_red = bar.close < bar.open

            # First candle must continue in the gap direction.
            if self.gap_direction == "DOWN":
                self.first_bar_valid = first_bar_is_red
            else:
                self.first_bar_valid = first_bar_is_green

            if not self.first_bar_valid:
                # Silently skip the day.
                self.skip_today = True
                return

            self.previous_bar = bar
            return

        if not self.first_bar_valid or self.previous_bar is None:
            return

        current_is_green = bar.close > bar.open
        current_is_red = bar.close < bar.open

        # Gap down: bullish reversal.
        if (
            self.gap_direction == "DOWN"
            and current_is_green
            and bar.close > self.previous_bar.high
        ):
            self.pending_signal = "BULL_PUT"
            self.signal_time = algorithm_time
            self.signal_spx_price = bar.close

        # Gap up: bearish reversal.
        elif (
            self.gap_direction == "UP"
            and current_is_red
            and bar.close < self.previous_bar.low
        ):
            self.pending_signal = "BEAR_CALL"
            self.signal_time = algorithm_time
            self.signal_spx_price = bar.close

        self.previous_bar = bar

    def on_data(self, data):
        """Enter a spread after a valid reversal signal."""
        if self.pending_signal is None:
            return

        if self.skip_today or self.traded_today:
            self.pending_signal = None
            return

        if self.time.time() > self.last_entry_time:
            self.pending_signal = None
            return

        chain = data.option_chains.get(self.option_symbol)

        if chain is None:
            return

        today = self.time.date()

        contracts = [
            contract for contract in chain
            if contract.expiry.date() == today
        ]

        if not contracts:
            return

        selected = self.select_spread(
            contracts,
            self.pending_signal,
            self.signal_spx_price
        )

        if selected is None:
            self.debug(
                f"{self.time} | No valid spread satisfied the "
                "1% OTM and premium requirements."
            )
            self.pending_signal = None
            return

        short_contract, long_contract, estimated_credit = selected

        spread_type = self.pending_signal

        width = abs(
            short_contract.strike - long_contract.strike
        )

        short_distance_points = abs(
            short_contract.strike - self.signal_spx_price
        )

        short_distance_percent = (
            short_distance_points / self.signal_spx_price
        )

        estimated_total_credit = (
            estimated_credit
            * 100
            * self.spread_quantity
        )

        maximum_loss = (
            width - estimated_credit
        ) * 100 * self.spread_quantity

        opening_legs = [
            Leg.create(short_contract.symbol, -1),
            Leg.create(long_contract.symbol, 1)
        ]

        tag = (
            f"OPEN {spread_type} | "
            f"SPX {self.signal_spx_price:.2f} | "
            f"SELL {short_contract.strike:.0f} "
            f"@ {short_contract.bid_price:.2f} | "
            f"BUY {long_contract.strike:.0f} "
            f"@ {long_contract.ask_price:.2f} | "
            f"CREDIT {estimated_credit:.2f}"
        )

        self.combo_market_order(
            opening_legs,
            self.spread_quantity,
            tag=tag
        )

        # Save the active position.
        self.active_short_symbol = short_contract.symbol
        self.active_long_symbol = long_contract.symbol
        self.active_short_strike = short_contract.strike
        self.active_long_strike = long_contract.strike
        self.active_spread_type = spread_type

        self.traded_today = True
        self.pending_signal = None

        # Concise, visible trade log.
        self.log(
            f"TRADE PLACED | "
            f"{self.signal_time:%Y-%m-%d %H:%M} ET | "
            f"{spread_type} | "
            f"SPX {self.signal_spx_price:.2f} | "
            f"SELL {short_contract.strike:.0f} "
            f"@ {short_contract.bid_price:.2f} | "
            f"BUY {long_contract.strike:.0f} "
            f"@ {long_contract.ask_price:.2f} | "
            f"PRICE DIFFERENCE/CREDIT {estimated_credit:.2f} | "
            f"20 SPREADS = ${estimated_total_credit:,.0f} | "
            f"OTM {short_distance_points:.2f} POINTS "
            f"({short_distance_percent:.2%}) | "
            f"WIDTH {width:.0f} | "
            f"MAX RISK ${maximum_loss:,.0f}"
        )

    def select_spread(self, contracts, signal, spot):
        """
        Select a spread using these requirements:

        1. Short strike must be at least 1% OTM.
        2. Short bid should be near $0.60.
        3. Hedge ask should be about $0.50 below the short bid.
        4. Hedge must be farther OTM.
        """
        if signal == "BULL_PUT":
            maximum_short_strike = (
                spot * (1 - self.minimum_otm_percent)
            )

            short_candidates = [
                contract for contract in contracts
                if contract.right == OptionRight.PUT
                and contract.strike <= maximum_short_strike
                and contract.bid_price > 0
                and contract.ask_price > 0
            ]

        else:
            minimum_short_strike = (
                spot * (1 + self.minimum_otm_percent)
            )

            short_candidates = [
                contract for contract in contracts
                if contract.right == OptionRight.CALL
                and contract.strike >= minimum_short_strike
                and contract.bid_price > 0
                and contract.ask_price > 0
            ]

        if not short_candidates:
            return None

        best_spread = None
        best_score = float("inf")
        best_credit_difference = float("inf")

        for short_contract in short_candidates:
            hedge_target_price = max(
                short_contract.bid_price
                - self.hedge_price_discount,
                0.05
            )

            for long_contract in contracts:
                if (
                    long_contract.right
                    != short_contract.right
                ):
                    continue

                if (
                    long_contract.bid_price <= 0
                    or long_contract.ask_price <= 0
                ):
                    continue

                # Hedge must be farther OTM.
                if signal == "BULL_PUT":
                    long_is_farther_otm = (
                        long_contract.strike
                        < short_contract.strike
                    )
                else:
                    long_is_farther_otm = (
                        long_contract.strike
                        > short_contract.strike
                    )

                if not long_is_farther_otm:
                    continue

                estimated_credit = (
                    short_contract.bid_price
                    - long_contract.ask_price
                )

                if estimated_credit <= 0:
                    continue

                short_price_difference = abs(
                    short_contract.bid_price
                    - self.short_target_price
                )

                hedge_price_difference = abs(
                    long_contract.ask_price
                    - hedge_target_price
                )

                score = (
                    short_price_difference
                    + hedge_price_difference
                )

                credit_difference = abs(
                    estimated_credit
                    - self.target_net_credit
                )

                if (
                    score < best_score
                    or (
                        score == best_score
                        and credit_difference
                        < best_credit_difference
                    )
                ):
                    best_score = score
                    best_credit_difference = credit_difference

                    best_spread = (
                        short_contract,
                        long_contract,
                        estimated_credit
                    )

        return best_spread

    def check_spread_before_close(self):
        """
        At 3:45 PM ET:

        - If the short option is ITM, close the complete spread.
        - If the short option is OTM, do nothing and let it expire.
        """
        if (
            self.active_short_symbol is None
            or self.active_long_symbol is None
            or self.active_short_strike is None
            or self.active_spread_type is None
        ):
            return

        short_quantity = self.portfolio[
            self.active_short_symbol
        ].quantity

        long_quantity = self.portfolio[
            self.active_long_symbol
        ].quantity

        if short_quantity == 0 and long_quantity == 0:
            self.clear_active_spread()
            return

        current_spx = self.securities[self.spx].price

        if current_spx <= 0:
            self.debug(
                f"{self.time} | Unable to evaluate 3:45 exit: "
                "SPX price unavailable."
            )
            return

        # Determine whether the sold option is ITM.
        if self.active_spread_type == "BULL_PUT":
            short_is_itm = (
                current_spx < self.active_short_strike
            )
        else:
            short_is_itm = (
                current_spx > self.active_short_strike
            )

        if not short_is_itm:
            self.log(
                f"HOLD TO EXPIRY | "
                f"{self.time:%Y-%m-%d %H:%M} ET | "
                f"{self.active_spread_type} | "
                f"SPX {current_spx:.2f} | "
                f"SHORT STRIKE {self.active_short_strike:.0f} OTM"
            )
            return

        self.log(
            f"CLOSE ITM SPREAD | "
            f"{self.time:%Y-%m-%d %H:%M} ET | "
            f"{self.active_spread_type} | "
            f"SPX {current_spx:.2f} | "
            f"SHORT STRIKE {self.active_short_strike:.0f} ITM | "
            f"LONG STRIKE {self.active_long_strike:.0f}"
        )

        if (
            short_quantity < 0
            and long_quantity > 0
            and abs(short_quantity) == abs(long_quantity)
        ):
            closing_legs = [
                Leg.create(self.active_short_symbol, 1),
                Leg.create(self.active_long_symbol, -1)
            ]

            number_of_spreads = int(abs(short_quantity))

            self.combo_market_order(
                closing_legs,
                number_of_spreads,
                tag="CLOSE ITM SPREAD AT 3:45 PM ET"
            )

        else:
            # Defensive fallback for mismatched holdings.
            if short_quantity != 0:
                self.market_order(
                    self.active_short_symbol,
                    -short_quantity,
                    tag="CLOSE ITM SHORT LEG AT 3:45 PM ET"
                )

            if long_quantity != 0:
                self.market_order(
                    self.active_long_symbol,
                    -long_quantity,
                    tag="CLOSE ITM LONG LEG AT 3:45 PM ET"
                )

        self.clear_active_spread()

    def clear_active_spread(self):
        """Clear stored information for the completed spread."""
        self.active_short_symbol = None
        self.active_long_symbol = None
        self.active_short_strike = None
        self.active_long_strike = None
        self.active_spread_type = None