from AlgorithmImports import *
from datetime import datetime, timedelta, time


class MomentumStockLeapPutCoveredCall(QCAlgorithm):
    """100 shares + short LEAP put + short covered call, selected by 30D momentum."""

    # ====================== EASY-TO-CHANGE PARAMETERS ======================
    TICKERS = ["AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA",
               "TSLA", "AMD", "AVGO"]
    CALL_DTE, CALL_DELTA = 30, 0.30
    LEAP_DTE, LEAP_DELTA = 730, 0.30       # LEAP_DELTA is absolute put delta
    MOMENTUM_DAYS_CALENDAR = 30
    MOMENTUM_MODE = "weakest"             # "weakest" or "strongest"
    PROFIT_TARGET = 0.10
    CONTRACTS = 1
    STARTING_TICKER = None                 # None ranks immediately

    CALL_DTE_TOLERANCE = 14
    CALL_ROLL_DAYS_BEFORE_EXPIRY = 3
    CALL_ROLL_MAX_DTE = 120              # wider search, but still exact strike
    LEAP_DTE_TOLERANCE = 120
    START_DATE, END_DATE = (2021, 5, 1), (2026, 4, 30)
    STARTING_CASH = 1_000_000              # portfolio-secures the short put
    ENTRY_TIME = time(10, 0)
    EXPIRY_EXIT_TIME = time(15, 45)
    # ======================================================================

    def initialize(self):
        self.set_start_date(*self.START_DATE)
        self.set_end_date(*self.END_DATE)
        self.set_cash(self.STARTING_CASH)
        self.set_time_zone(TimeZones.NEW_YORK)
        self.set_brokerage_model(BrokerageName.QUANT_CONNECT_BROKERAGE)
        if self.MOMENTUM_MODE not in ("weakest", "strongest"):
            raise ValueError("MOMENTUM_MODE must be 'weakest' or 'strongest'")
        if self.CONTRACTS < 1:
            raise ValueError("CONTRACTS must be >= 1")

        self.equities = {}
        for ticker in self.TICKERS:
            # Option strike prices are raw, so their underlying subscriptions
            # must also use RAW normalization.
            security = self.add_equity(
                ticker, Resolution.MINUTE,
                data_normalization_mode=DataNormalizationMode.RAW
            )
            self.equities[ticker] = security.symbol

        self.state = "FLAT"  # also ROLLING, COVERING_ASSIGNMENT, EXITING
        self.option_canonical = self.selected_ticker = self.stock_symbol = None
        self.put_symbol = self.call_symbol = None
        self.put_contract = self.call_contract = None
        self.roll_requested = False
        self.roll_old_call_symbol = None
        self.required_call_contracts = self.CONTRACTS
        self.first_cycle, self.cycle_number = True, 0
        self.last_start_date, self.next_cycle_time = None, datetime.min
        self.reset_accounting()

        self.schedule.on(self.date_rules.every_day("AAPL"),
                         self.time_rules.at(10, 0), self.try_start_cycle)
        self.schedule.on(self.date_rules.every_day("AAPL"),
                         self.time_rules.at(15, 45), self.expiration_exit_check)
        self.set_warm_up(timedelta(days=self.MOMENTUM_DAYS_CALENDAR + 10))
        self.debug(f"STRATEGY INITIALIZED | entry={self.ENTRY_TIME} | tickers={len(self.TICKERS)}")

    def emit(self, message):
        """Store the full message and stream a compact copy to Cloud Terminal."""
        self.log(message)
        terminal_message = message.replace("\n", " | ")
        self.debug(f"STRATEGY | {terminal_message[:185]}")

    def reset_accounting(self):
        self.cycle_cash_flow = 0.0
        self.capital_basis = self.target_profit = None
        self.stock_entry_cost = self.put_entry_credit = 0.0
        self.stock_cost_shares = 0
        self.entry_fills, self.exit_reason = {}, None

    def try_start_cycle(self):
        if (self.is_warming_up or self.state != "FLAT" or
                self.time < self.next_cycle_time or self.last_start_date == self.time.date()):
            return
        ranking = self.build_ranking()
        if len(ranking) != len(self.TICKERS):
            self.emit(f"CYCLE SKIPPED | ranking has {len(ranking)}/{len(self.TICKERS)} stocks")
            return
        self.log_ranking(ranking)
        forced = self.STARTING_TICKER if self.first_cycle else None
        if forced is not None and forced not in self.equities:
            raise ValueError("STARTING_TICKER must be None or in TICKERS")
        selected = forced or ranking[0][0]
        if forced:
            self.emit(f"STARTING_TICKER override applied: {forced}")
        self.first_cycle, self.last_start_date = False, self.time.date()
        self.selected_ticker, self.stock_symbol = selected, self.equities[selected]
        option = self.add_option(selected, Resolution.MINUTE)
        option.price_model = OptionPriceModels.binomial_cox_ross_rubinstein()
        # Subscribe only to the two expiry bands we can actually trade. A single
        # 1-to-850 DTE filter can select 1,000+ minute contracts per ticker.
        option.set_filter(self.option_filter)
        self.option_canonical, self.state = option.symbol, "WAIT_CHAIN"
        row = next(x for x in ranking if x[0] == selected)
        self.emit(f"SELECTED {selected} | stock={row[3]:.2f} | 30D={row[1]:+.2%} | waiting for chain")

    def option_filter(self, universe):
        call_min = self.time.date() + timedelta(days=max(1, self.CALL_DTE - self.CALL_DTE_TOLERANCE))
        call_max = self.time.date() + timedelta(days=self.CALL_DTE + self.CALL_DTE_TOLERANCE)
        leap_min = self.time.date() + timedelta(days=max(1, self.LEAP_DTE - self.LEAP_DTE_TOLERANCE))
        leap_max = self.time.date() + timedelta(days=self.LEAP_DTE + self.LEAP_DTE_TOLERANCE)

        def wanted(symbols):
            dated = [
                symbol for symbol in symbols
                if self.option_symbol_is_wanted(
                    symbol, call_min, call_max, leap_min, leap_max
                )
            ]
            # A custom Contracts filter receives the full chain, so explicitly
            # cap each expiry to nearby strikes. Otherwise Strikes() can be
            # superseded and a volatile ticker may still add 1,000+ contracts.
            price = float(self.securities[self.stock_symbol].price)
            by_expiry_and_right = {}
            for symbol in dated:
                key = (symbol.id.date, symbol.id.option_right)
                by_expiry_and_right.setdefault(key, []).append(symbol)
            selected = []
            old_strike = float(self.call_contract.strike) if self.call_contract else None
            for (_, right), expiry_symbols in by_expiry_and_right.items():
                closest = sorted(
                    expiry_symbols,
                    key=lambda s: abs(float(s.id.strike_price) - price)
                )[:31]
                selected.extend(closest)
                if old_strike is not None and right == OptionRight.CALL:
                    # Always retain the strike closest to the old call. On an
                    # equal-distance tie, prefer the higher strike (59.5 -> 60).
                    nearest_roll_strike = min(
                        expiry_symbols,
                        key=lambda s: (
                            abs(float(s.id.strike_price) - old_strike),
                            -float(s.id.strike_price)
                        )
                    )
                    selected.append(nearest_roll_strike)
            return list(dict.fromkeys(selected))

        # `wanted` performs its own strike cap and nearest-roll-strike inclusion.
        # Applying Strikes() first could discard a far OTM legacy roll strike.
        return universe.include_weeklys().contracts(wanted)

    def option_symbol_is_wanted(self, symbol, call_min, call_max, leap_min, leap_max):
        expiry = symbol.id.date.date()
        right = symbol.id.option_right
        if right == OptionRight.PUT:
            return leap_min <= expiry <= leap_max
        if right != OptionRight.CALL:
            return False
        if call_min <= expiry <= call_max:
            return True
        # While a call is active, expose later call expirations so the filter
        # can retain the nearest listed strike if the exact strike disappears.
        if self.call_contract is None:
            return False
        roll_max = self.time.date() + timedelta(days=self.CALL_ROLL_MAX_DTE)
        return self.time.date() < expiry <= roll_max

    def build_ranking(self):
        target = self.time.date() - timedelta(days=self.MOMENTUM_DAYS_CALENDAR)
        history = self.history(list(self.equities.values()),
                               timedelta(days=self.MOMENTUM_DAYS_CALENDAR + 10),
                               Resolution.DAILY,
                               data_normalization_mode=DataNormalizationMode.SPLIT_ADJUSTED)
        rows = []
        if history.empty:
            return rows
        for ticker, symbol in self.equities.items():
            live_price = float(self.securities[symbol].price)
            try:
                frame = history.loc[symbol]
                # Both ends of the performance calculation must be on the same
                # split-adjusted scale. Daily history only supplies completed
                # closes, so `latest` is normally the prior trading close.
                completed = frame[frame.index.date < self.time.date()]
                eligible = frame[frame.index.date <= target]
            except (KeyError, TypeError):
                continue
            if live_price <= 0 or completed.empty or eligible.empty:
                continue
            old = float(eligible.iloc[-1]["close"])
            latest = float(completed.iloc[-1]["close"])
            if old > 0:
                rows.append((ticker, latest / old - 1, old, live_price))
        return sorted(rows, key=lambda x: x[1], reverse=self.MOMENTUM_MODE == "strongest")

    def log_ranking(self, ranking):
        target = self.time.date() - timedelta(days=self.MOMENTUM_DAYS_CALENDAR)
        lines = [f"FULL RANKING {self.time:%Y-%m-%d %H:%M} | {self.MOMENTUM_MODE} | close on/before {target}"]
        for i, (ticker, ret, old, now) in enumerate(ranking, 1):
            lines.append(f"{i:02d}. {ticker:<5} {ret:+8.2%} | reference={old:.2f} current={now:.2f}")
        self.emit("\n".join(lines))

    def on_data(self, data):
        if self.is_warming_up:
            return
        if self.handle_split_event(data):
            return
        if self.state == "FLAT":
            if (self.time >= self.next_cycle_time and
                    self.ENTRY_TIME <= self.time.time() < time(15, 15)):
                self.try_start_cycle()
        elif self.state == "WAIT_CHAIN":
            chain = data.option_chains.get(self.option_canonical)
            if chain:
                self.try_enter(list(chain))
        elif self.state == "WAIT_ASSIGNMENT_CALL":
            chain = data.option_chains.get(self.option_canonical)
            if chain:
                self.try_cover_assignment(list(chain))
        elif self.state == "ACTIVE":
            chain = data.option_chains.get(self.option_canonical)
            if self.roll_requested and chain:
                self.try_roll_call(list(chain))
            elif (self.cycle_pnl() >= self.target_profit and
                  self.stock_at_or_above_entry()):
                self.submit_exit("PROFIT TARGET")

    def handle_split_event(self, data):
        """Exit option structures on split warning and rescale stock cost units."""
        if self.stock_symbol is None:
            return False
        split = data.splits.get(self.stock_symbol)
        if split is None:
            return False

        if split.type == SplitType.WARNING:
            if self.state == "ACTIVE":
                self.emit(
                    f"UPCOMING SPLIT | {self.selected_ticker} | factor={split.split_factor} | "
                    "closing structure because LEAN cannot adjust option contracts"
                )
                self.submit_exit("UPCOMING STOCK SPLIT")
                return True
            return False

        if split.type == SplitType.SPLIT_OCCURRED:
            # LEAN automatically adjusts RAW equity holdings and open stock
            # orders. Total dollars paid don't change; only the share units do.
            adjusted_shares = abs(int(round(self.portfolio[self.stock_symbol].quantity)))
            if adjusted_shares > 0:
                self.stock_cost_shares = adjusted_shares
                self.emit(
                    f"STOCK SPLIT APPLIED | {self.selected_ticker} | factor={split.split_factor} | "
                    f"shares={adjusted_shares} adjusted_cost={self.stock_entry_price():.2f}"
                )
            return False
        return False

    def try_enter(self, contracts):
        put = self.select_contract(contracts, OptionRight.PUT, self.LEAP_DTE,
                                   self.LEAP_DTE_TOLERANCE, -abs(self.LEAP_DELTA))
        call = self.select_contract(contracts, OptionRight.CALL, self.CALL_DTE,
                                    self.CALL_DTE_TOLERANCE, abs(self.CALL_DELTA))
        if put is None or call is None or not self.valid_quote(put) or not self.valid_quote(call):
            return
        self.put_contract, self.call_contract = put, call
        self.put_symbol, self.call_symbol = put.symbol, call.symbol
        self.cycle_number += 1
        self.reset_accounting()
        self.entry_fills = {self.stock_symbol: 0, self.put_symbol: 0, self.call_symbol: 0}
        self.required_call_contracts = self.CONTRACTS
        self.state = "ENTERING"
        n = self.CONTRACTS
        self.market_order(self.stock_symbol, 100 * n, tag=f"C{self.cycle_number} BUY STOCK")
        self.market_order(self.put_symbol, -n, tag=f"C{self.cycle_number} SELL LEAP PUT")
        self.market_order(self.call_symbol, -n, tag=f"C{self.cycle_number} SELL CALL")
        stock = float(self.securities[self.stock_symbol].price)
        self.emit(f"ENTRY SUBMITTED | stock={stock:.2f} | "
                 f"PUT {put.strike:.2f} {put.expiry:%Y-%m-%d} mid={self.mid(put):.2f} delta={self.delta_text(put)} | "
                 f"CALL {call.strike:.2f} {call.expiry:%Y-%m-%d} mid={self.mid(call):.2f} delta={self.delta_text(call)}")

    def select_contract(self, contracts, right, target_dte, tolerance, target_delta):
        choices = []
        for contract in contracts:
            dte = (contract.expiry.date() - self.time.date()).days
            if contract.right != right or abs(dte - target_dte) > tolerance:
                continue
            try:
                delta_error = abs(float(contract.greeks.delta) - target_delta)
            except Exception:
                delta_error = 99.0
            choices.append((abs(dte - target_dte), delta_error,
                            0 if self.valid_quote(contract) else 1, contract))
        return min(choices, key=lambda x: x[:3])[3] if choices else None

    def try_roll_call(self, contracts):
        """Roll near CALL_DTE at the same strike, or the closest listed strike."""
        old_call = self.call_contract
        old_strike = float(old_call.strike)
        choices = []
        for contract in contracts:
            dte = (contract.expiry.date() - self.time.date()).days
            if (contract.right != OptionRight.CALL or
                    contract.symbol == self.call_symbol or
                    dte < 1 or dte > self.CALL_ROLL_MAX_DTE or
                    not self.valid_quote(contract)):
                continue
            strike = float(contract.strike)
            choices.append((
                abs(strike - old_strike),        # exact strike wins first
                abs(dte - self.CALL_DTE),        # then closest target DTE
                0 if strike >= old_strike else 1,  # ties roll upward
                contract.expiry,
                contract
            ))
        if not choices:
            return

        new_call = min(choices, key=lambda x: x[:4])[4]
        old_symbol = self.call_symbol
        self.roll_old_call_symbol = old_symbol
        self.call_symbol, self.call_contract = new_call.symbol, new_call
        self.roll_requested = False
        self.state = "ROLLING"
        old_quantity = self.portfolio[old_symbol].quantity
        if old_quantity < 0:
            self.market_order(old_symbol, -old_quantity,
                              tag=f"C{self.cycle_number} BUY BACK CALL FOR ROLL")
        roll_quantity = self.required_call_contracts
        self.market_order(self.call_symbol, -roll_quantity,
                          tag=f"C{self.cycle_number} SELL ROLLED CALL")
        stock = float(self.securities[self.stock_symbol].price)
        self.emit(
            f"CALL ROLL SUBMITTED | stock={stock:.2f} entry={self.stock_entry_price():.2f} | "
            f"old_strike={old_strike:.2f} new_strike={new_call.strike:.2f} | "
            f"old_expiry={old_call.expiry:%Y-%m-%d} | "
            f"new_expiry={new_call.expiry:%Y-%m-%d} mid={self.mid(new_call):.2f} "
            f"delta={self.delta_text(new_call)}"
        )

    def try_cover_assignment(self, contracts):
        """Find a fresh call when assignment occurs without a live call leg."""
        call = self.select_contract(
            contracts, OptionRight.CALL, self.CALL_DTE,
            self.CALL_DTE_TOLERANCE, abs(self.CALL_DELTA)
        )
        if call is None or not self.valid_quote(call):
            return
        self.call_symbol, self.call_contract = call.symbol, call
        existing = int(self.portfolio[self.call_symbol].quantity)
        contracts_to_sell = self.required_call_contracts + existing
        if contracts_to_sell <= 0:
            self.state = "ACTIVE"
            return
        self.state = "COVERING_ASSIGNMENT"
        self.market_order(
            self.call_symbol, -contracts_to_sell,
            tag=f"C{self.cycle_number} COVER PUT-ASSIGNED SHARES"
        )
        self.emit(
            f"ASSIGNMENT COVER SUBMITTED | sell={contracts_to_sell} call(s) | "
            f"strike={call.strike:.2f} expiry={call.expiry:%Y-%m-%d} "
            f"delta={self.delta_text(call)}"
        )

    @staticmethod
    def valid_quote(c):
        return c.bid_price > 0 and c.ask_price > 0

    @staticmethod
    def mid(c):
        return (float(c.bid_price) + float(c.ask_price)) / 2

    @staticmethod
    def delta_text(c):
        try:
            return f"{float(c.greeks.delta):+.3f}"
        except Exception:
            return "n/a"

    def on_order_event(self, event):
        # Assignment has its own handler. Counting it here as an ordinary
        # option trade would double-count the assigned stock purchase.
        if getattr(event, "is_assignment", False):
            return
        if event.status not in (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED):
            return
        if event.symbol not in (self.stock_symbol, self.put_symbol, self.call_symbol,
                                self.roll_old_call_symbol):
            return
        multiplier = 100.0 if event.symbol.security_type == SecurityType.OPTION else 1.0
        fee = float(event.order_fee.value.amount) if event.order_fee else 0.0
        self.cycle_cash_flow += -float(event.fill_quantity) * float(event.fill_price) * multiplier - fee
        stock = float(self.securities[self.stock_symbol].price)
        self.emit(f"FILL | stock={stock:.2f} | {event.symbol} | qty={event.fill_quantity} "
                 f"price={event.fill_price:.2f} fee={fee:.2f}")
        if self.state == "ENTERING":
            self.entry_fills[event.symbol] += int(event.fill_quantity)
            if event.symbol == self.stock_symbol and event.fill_quantity > 0:
                self.stock_entry_cost += event.fill_quantity * event.fill_price
                self.stock_cost_shares += int(event.fill_quantity)
            elif event.symbol == self.put_symbol and event.fill_quantity < 0:
                self.put_entry_credit += -event.fill_quantity * event.fill_price * 100
            if (self.entry_fills[self.stock_symbol] == 100 * self.CONTRACTS and
                    self.entry_fills[self.put_symbol] == -self.CONTRACTS and
                    self.entry_fills[self.call_symbol] == -self.CONTRACTS):
                # Per specification, call premium is P&L but not a basis reduction.
                self.capital_basis = self.stock_entry_cost - self.put_entry_credit
                self.target_profit = self.capital_basis * self.PROFIT_TARGET
                self.state = "ACTIVE"
                self.log_snapshot("ENTRY COMPLETE")
        elif self.state == "EXITING" and self.structure_is_flat():
            self.finish_cycle()
        elif (self.state == "ROLLING" and
              self.portfolio[self.roll_old_call_symbol].quantity == 0 and
              self.portfolio[self.call_symbol].quantity == -self.required_call_contracts):
            self.state = "ACTIVE"
            self.log_snapshot("CALL ROLL COMPLETE")
            self.roll_old_call_symbol = None
        elif (self.state == "COVERING_ASSIGNMENT" and
              self.portfolio[self.call_symbol].quantity == -self.required_call_contracts):
            self.state = "ACTIVE"
            self.log_snapshot("PUT ASSIGNMENT COVERED")

    def on_assignment_order_event(self, event):
        """Accept assigned put shares and cover them with additional calls."""
        if (event.status != OrderStatus.FILLED or event.symbol != self.put_symbol or
                event.symbol.id.option_right != OptionRight.PUT):
            return

        assigned_contracts = abs(int(event.fill_quantity))
        if assigned_contracts == 0:
            return
        assigned_shares = 100 * assigned_contracts
        strike = float(event.symbol.id.strike_price)
        assignment_cost = strike * assigned_shares

        # LEAN updates portfolio cash/equity holdings for the assignment. Add
        # the same stock purchase to this strategy's independent cycle ledger.
        self.cycle_cash_flow -= assignment_cost
        self.stock_entry_cost += assignment_cost
        self.stock_cost_shares += assigned_shares
        self.required_call_contracts += assigned_contracts
        self.capital_basis = self.stock_entry_cost - self.put_entry_credit
        self.target_profit = self.capital_basis * self.PROFIT_TARGET
        self.roll_requested = False

        self.emit(
            f"LEAP PUT ASSIGNED | {assigned_contracts} contract(s) | "
            f"bought={assigned_shares} shares strike={strike:.2f} | "
            f"total_shares={self.stock_cost_shares} avg_cost={self.stock_entry_price():.2f} | "
            f"new_basis={self.capital_basis:.2f} new_target={self.target_profit:.2f}"
        )

        # The currently active call is the fastest and cleanest way to cover
        # the newly assigned shares. It also keeps all covered calls aligned
        # for subsequent rolls.
        if (self.call_symbol is not None and
                self.call_contract.expiry.date() > self.time.date()):
            self.state = "COVERING_ASSIGNMENT"
            self.market_order(
                self.call_symbol, -assigned_contracts,
                tag=f"C{self.cycle_number} COVER PUT-ASSIGNED SHARES"
            )
        else:
            # No live call is available. Reuse the normal chain-selection path
            # on the next Slice and do not treat the cycle as safely active.
            self.state = "WAIT_ASSIGNMENT_CALL"
            self.emit("ASSIGNMENT COVER PENDING | waiting for a live call contract")

    def cycle_pnl(self):
        value = 0.0
        for symbol in (self.stock_symbol, self.put_symbol, self.call_symbol,
                       self.roll_old_call_symbol):
            if symbol is not None and symbol in self.securities:
                security = self.securities[symbol]
                quantity = float(self.portfolio[symbol].quantity)
                multiplier = 100.0 if symbol.security_type == SecurityType.OPTION else 1.0
                # Mark at the side needed to liquidate (bid for longs, ask for
                # shorts), falling back to LEAN's price if that quote is absent.
                mark = float(security.bid_price if quantity > 0 else security.ask_price)
                if mark <= 0:
                    mark = float(security.price)
                value += quantity * mark * multiplier
        return self.cycle_cash_flow + value

    def log_snapshot(self, label):
        pnl = self.cycle_pnl()
        roc = pnl / self.capital_basis if self.capital_basis else 0
        stock = float(self.securities[self.stock_symbol].price)
        self.emit(f"{label} | cycle={self.cycle_number} {self.selected_ticker} stock={stock:.2f} | "
                 f"PUT {self.put_contract.strike:.2f} {self.put_contract.expiry:%Y-%m-%d} delta={self.delta_text(self.put_contract)} | "
                 f"CALL {self.call_contract.strike:.2f} {self.call_contract.expiry:%Y-%m-%d} delta={self.delta_text(self.call_contract)} | "
                 f"basis={self.capital_basis:.2f} target={self.target_profit:.2f} pnl={pnl:.2f} ROC={roc:.2%}")

    def expiration_exit_check(self):
        if self.state != "ACTIVE" or self.call_contract is None:
            return
        dte = (self.call_contract.expiry.date() - self.time.date()).days
        if (dte <= self.CALL_ROLL_DAYS_BEFORE_EXPIRY and
                not self.stock_at_or_above_entry() and
                not self.roll_requested):
            self.roll_requested = True
            self.emit(
                f"CALL ROLL REQUESTED | stock={self.securities[self.stock_symbol].price:.2f} "
                f"below entry={self.stock_entry_price():.2f} | DTE={dte} | "
                f"target strike={self.call_contract.strike:.2f} (nearest listed allowed)"
            )
        elif dte <= 0:
            self.submit_exit("CALL EXPIRATION-DAY EXIT")

    def stock_entry_price(self):
        return (self.stock_entry_cost / self.stock_cost_shares
                if self.stock_cost_shares else 0.0)

    def stock_at_or_above_entry(self):
        return float(self.securities[self.stock_symbol].price) >= self.stock_entry_price()

    def submit_exit(self, reason):
        if self.state != "ACTIVE":
            return
        self.exit_reason = reason
        self.log_snapshot(f"EXIT TRIGGER: {reason}")
        self.state = "EXITING"
        for symbol in (self.call_symbol, self.put_symbol):
            quantity = self.portfolio[symbol].quantity
            if quantity:
                self.market_order(symbol, -quantity, tag=f"C{self.cycle_number} {reason}")
        # A sell limit at the original share fill price enforces the rule that
        # shares are never sold below what this cycle paid for them.
        stock_quantity = self.portfolio[self.stock_symbol].quantity
        if stock_quantity:
            self.limit_order(self.stock_symbol, -stock_quantity, self.stock_entry_price(),
                             tag=f"C{self.cycle_number} {reason} | DO NOT SELL BELOW COST")

    def structure_is_flat(self):
        return all(s is None or self.portfolio[s].quantity == 0
                   for s in (self.stock_symbol, self.put_symbol, self.call_symbol))

    def finish_cycle(self):
        pnl = self.cycle_cash_flow
        roc = pnl / self.capital_basis if self.capital_basis else 0
        stock = float(self.securities[self.stock_symbol].price)
        self.emit(f"CYCLE CLOSED | cycle={self.cycle_number} reason={self.exit_reason} "
                 f"{self.selected_ticker} stock={stock:.2f} basis={self.capital_basis:.2f} pnl={pnl:.2f} ROC={roc:.2%}")
        # add_option creates a universe. Remove its canonical symbol after all
        # positions and orders are gone so old ticker chains don't accumulate.
        old_canonical = self.option_canonical
        if old_canonical is not None:
            self.remove_security(old_canonical)
            self.emit(f"OPTION UNIVERSE REMOVAL REQUESTED | {self.selected_ticker}")
        self.option_canonical = None
        self.state, self.next_cycle_time = "FLAT", self.time + timedelta(minutes=1)
        self.put_symbol = self.call_symbol = self.put_contract = self.call_contract = None
        self.roll_requested = False
        self.roll_old_call_symbol = None
        self.required_call_contracts = self.CONTRACTS

    def on_end_of_algorithm(self):
        if self.state in ("ACTIVE", "EXITING") and self.capital_basis:
            self.log_snapshot("BACKTEST END (OPEN CYCLE MARK-TO-MARKET)")
