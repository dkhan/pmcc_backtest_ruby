# region imports
from AlgorithmImports import *
# endregion

class TslaLeapWheelAlgorithm(QCAlgorithm):
    """
    TSLA 'Golden Wheel' LEAP Strategy
    ----------------------------------
    Cycle:
      1. Sell a cash-secured LEAP put (~10% OTM, ~18 months out)
      2. If assigned -> own 100 shares -> sell a LEAP covered call (~10% OTM, ~18 months out)
      3. If called away -> back to step 1

    Now with $300k capital and maximum contract sizing.
    TSLA 3:1 split on 2022-08-25 is handled automatically in Raw mode.
    """

    # ------------------------------------------------------------------ #
    #  Initialise
    # ------------------------------------------------------------------ #
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(datetime.today())
        self.SetCash(300_000)

        # -- Underlying equity -----------------------------------------
        self.tsla_equity = self.AddEquity("TSLA", Resolution.Daily)
        # Raw mode: splits are applied to share counts automatically.
        # TSLA 3:1 split on 2022-08-25 means 100 shares -> 300 shares.
        self.tsla_equity.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.tsla = self.tsla_equity.Symbol

        # -- Option chain ----------------------------------------------
        option = self.AddOption("TSLA", Resolution.Daily)
        option.SetFilter(self._option_filter)
        self.option_symbol = option.Symbol

        # -- Benchmark: buy-and-hold TSLA -----------------------------
        self.SetBenchmark("TSLA")

        # -- State tracking -------------------------------------------
        self._short_put_symbol:  Symbol | None = None
        self._short_call_symbol: Symbol | None = None
        self.shares_held: int = 0

        # Cooldown: don't re-scan the same day we placed an order
        self.last_trade_date: datetime | None = None

        # -- Trade log storage ----------------------------------------
        self._trade_log: list[str] = []

        # -- Custom charts --------------------------------------------
        equity_chart = Chart("Strategy vs Benchmark")
        equity_chart.AddSeries(Series("Wheel Portfolio",  SeriesType.Line, "$"))
        equity_chart.AddSeries(Series("TSLA Price",       SeriesType.Line, "$"))
        self.AddChart(equity_chart)

        state_chart = Chart("Position State")
        state_chart.AddSeries(Series("State", SeriesType.Line, ""))
        self.AddChart(state_chart)

        self.Log("=== TSLA LEAP Wheel Initialized (300k, Multi-Contract) ===")

    # ------------------------------------------------------------------ #
    #  Option filter: LEAPs 12-24 months out, wide strike range
    # ------------------------------------------------------------------ #
    def _option_filter(self, universe: OptionFilterUniverse) -> OptionFilterUniverse:
        return (universe
                .Strikes(-20, 20)
                .Expiration(timedelta(days=365), timedelta(days=730)))

    # ------------------------------------------------------------------ #
    #  OnData  - main logic runs once per day
    # ------------------------------------------------------------------ #
    def OnData(self, data: Slice):
        today = self.Time.date()
        if self.last_trade_date == today:
            return

        if not data.ContainsKey(self.tsla):
            return
        tsla_price = self.Securities[self.tsla].Price
        if tsla_price <= 0:
            return

        # -- Refresh share count from portfolio -----------------------
        self.shares_held = int(self.Portfolio[self.tsla].Quantity)

        # -- Check open option positions -------------------------------
        self._reconcile_open_options()

        # -- Decide what to do ----------------------------------------
        has_short_put  = self._short_put_symbol  is not None
        has_short_call = self._short_call_symbol is not None
        has_shares     = self.shares_held >= 100

        # Log state only when it changes
        state_name = "CASH"
        if has_shares and has_short_call:
            state_name = "COVERED_CALL"
        elif has_shares:
            state_name = "LONG_STOCK"
        elif has_short_put:
            state_name = "SHORT_PUT"

        if not hasattr(self, '_last_logged_state') or self._last_logged_state != state_name:
            self.Log(f"[STATE-CHANGE] {today} | {state_name} | "
                     f"TSLA=${tsla_price:.2f} | Shares={self.shares_held} | "
                     f"Portfolio=${self.Portfolio.TotalPortfolioValue:,.0f}")
            self._last_logged_state = state_name

        # --- State 0: cash - open short puts --------------------------
        if not has_short_put and not has_short_call and not has_shares:
            self._sell_leap_put(tsla_price, today)

        # --- State 1: own shares, no call yet - sell covered calls ----
        elif has_shares and not has_short_call:
            self._sell_leap_call(tsla_price, today)

        # -- Charts ---------------------------------------------------
        portfolio_value = self.Portfolio.TotalPortfolioValue
        self.Plot("Strategy vs Benchmark", "Wheel Portfolio", portfolio_value)
        self.Plot("Strategy vs Benchmark", "TSLA Price",      tsla_price)

        state = 1 if has_shares else 0
        self.Plot("Position State", "State", state)

    # ------------------------------------------------------------------ #
    #  Sell maximum affordable LEAP puts
    # ------------------------------------------------------------------ #
    def _sell_leap_put(self, tsla_price: float, today):
        target_strike = tsla_price * 0.90
        target_expiry = today + timedelta(days=540)

        contract = self._best_contract(
            OptionRight.Put, target_strike, target_expiry, below=True
        )
        if contract is None:
            self.Log(f"[SKIP] {today} | No suitable LEAP put found.")
            return

        # Calculate max contracts based on cash
        required_per_contract = contract.Strike * 100
        max_contracts = int(self.Portfolio.Cash / required_per_contract)

        if max_contracts <= 0:
            self.Log(f"[SKIP] {today} | Insufficient cash for even 1 contract.")
            return

        premium = contract.BidPrice * 100 * max_contracts
        self.MarketOrder(contract.Symbol, -max_contracts)
        self._short_put_symbol = contract.Symbol
        self.last_trade_date = today

        log_entry = (f"[TRADE-OPEN] {today} | SELL PUT x{max_contracts} | "
                     f"Strike={contract.Strike} | Expiry={contract.Expiry.date()} | "
                     f"Bid={contract.BidPrice:.2f} | Premium=${premium:,.0f} | "
                     f"TSLA=${tsla_price:.2f} | Portfolio=${self.Portfolio.TotalPortfolioValue:,.0f}")
        self.Log(log_entry)
        self._trade_log.append(log_entry)

    # ------------------------------------------------------------------ #
    #  Sell LEAP covered calls (1 per 100 shares held)
    # ------------------------------------------------------------------ #
    def _sell_leap_call(self, tsla_price: float, today):
        target_strike = tsla_price * 1.10
        target_expiry = today + timedelta(days=540)

        contract = self._best_contract(
            OptionRight.Call, target_strike, target_expiry, below=False
        )
        if contract is None:
            self.Log(f"[SKIP] {today} | No suitable LEAP call found.")
            return

        # Calculate max calls based on shares (1 call per 100 shares)
        max_calls = self.shares_held // 100

        if max_calls <= 0:
            self.Log(f"[SKIP] {today} | Not enough shares for covered call.")
            return

        premium = contract.BidPrice * 100 * max_calls
        self.MarketOrder(contract.Symbol, -max_calls)
        self._short_call_symbol = contract.Symbol
        self.last_trade_date = today

        log_entry = (f"[TRADE-OPEN] {today} | SELL CALL x{max_calls} | "
                     f"Strike={contract.Strike} | Expiry={contract.Expiry.date()} | "
                     f"Bid={contract.BidPrice:.2f} | Premium=${premium:,.0f} | "
                     f"TSLA=${tsla_price:.2f} | Portfolio=${self.Portfolio.TotalPortfolioValue:,.0f}")
        self.Log(log_entry)
        self._trade_log.append(log_entry)

    # ------------------------------------------------------------------ #
    #  Pick the best contract from the live chain
    # ------------------------------------------------------------------ #
    def _best_contract(self, right, target_strike, target_expiry, below):
        chain = self._get_option_chain()
        if chain is None:
            return None

        candidates = [
            c for c in chain
            if c.Right == right
            and c.BidPrice > 0
            and timedelta(days=365) <= (c.Expiry.date() - self.Time.date()) <= timedelta(days=730)
        ]
        if not candidates:
            return None

        best_expiry = min(
            {c.Expiry for c in candidates},
            key=lambda e: abs((e.date() - target_expiry).days)
        )
        same_expiry = [c for c in candidates if c.Expiry == best_expiry]
        best = min(same_expiry, key=lambda c: abs(c.Strike - target_strike))
        return best

    # ------------------------------------------------------------------ #
    #  Retrieve the current option chain
    # ------------------------------------------------------------------ #
    def _get_option_chain(self):
        chain_provider = self.CurrentSlice
        if chain_provider is None:
            return None
        if not chain_provider.OptionChains.ContainsKey(self.option_symbol):
            return None
        chain = chain_provider.OptionChains[self.option_symbol]
        contracts = list(chain)
        return contracts if contracts else None

    # ------------------------------------------------------------------ #
    #  Sync open-option state with actual portfolio
    # ------------------------------------------------------------------ #
    def _reconcile_open_options(self):
        if self._short_put_symbol is not None:
            qty = self.Portfolio[self._short_put_symbol].Quantity
            if qty == 0:
                self.Log(f"[TRADE-CLOSE] {self.Time.date()} | PUT EXPIRED/WORTHLESS | "
                         f"Symbol={self._short_put_symbol}")
                self._short_put_symbol = None

        if self._short_call_symbol is not None:
            qty = self.Portfolio[self._short_call_symbol].Quantity
            if qty == 0:
                self.Log(f"[TRADE-CLOSE] {self.Time.date()} | CALL EXPIRED/WORTHLESS | "
                         f"Symbol={self._short_call_symbol}")
                self._short_call_symbol = None

    # ------------------------------------------------------------------ #
    #  OnOrderEvent - handle fills, expirations, assignments
    # ------------------------------------------------------------------ #
    def OnOrderEvent(self, order_event: OrderEvent):
        if order_event.Status != OrderStatus.Filled:
            return

        symbol   = order_event.Symbol
        qty      = order_event.FillQuantity
        price    = order_event.FillPrice
        date_str = self.Time.date()

        if symbol.SecurityType == SecurityType.Option:

            if symbol == self._short_put_symbol and qty > 0:
                log_entry = (f"[TRADE-ASSIGN] {date_str} | PUT ASSIGNED | {symbol} | "
                             f"FillQty={qty} | FillPrice=${price:.2f} | "
                             f"Acquired {qty * 100} TSLA shares")
                self.Log(log_entry)
                self._trade_log.append(log_entry)
                self._short_put_symbol = None

            elif symbol == self._short_call_symbol and qty > 0:
                log_entry = (f"[TRADE-ASSIGN] {date_str} | CALL ASSIGNED | {symbol} | "
                             f"FillQty={qty} | FillPrice=${price:.2f} | "
                             f"{qty * 100} TSLA shares called away")
                self.Log(log_entry)
                self._trade_log.append(log_entry)
                self._short_call_symbol = None

        elif symbol == self.tsla:
            self.Log(f"[EQUITY-FILL] {date_str} | TSLA qty={qty:+.0f} @ ${price:.2f} | "
                     f"Portfolio=${self.Portfolio.TotalPortfolioValue:,.0f}")

    # ------------------------------------------------------------------ #
    #  OnEndOfAlgorithm - summary
    # ------------------------------------------------------------------ #
    def OnEndOfAlgorithm(self):
        tsla_price = self.Securities[self.tsla].Price
        total_value = self.Portfolio.TotalPortfolioValue
        tsla_shares = self.Portfolio[self.tsla].Quantity

        self.Log("=" * 70)
        self.Log("TSLA LEAP WHEEL - FINAL SUMMARY")
        self.Log(f"  End Date          : {self.Time.date()}")
        self.Log(f"  TSLA Price        : ${tsla_price:.2f}")
        self.Log(f"  Shares held       : {tsla_shares:.0f}")
        self.Log(f"  Total Value       : ${total_value:,.2f}")
        self.Log(f"  Open Short Put    : {self._short_put_symbol}")
        self.Log(f"  Open Short Call   : {self._short_call_symbol}")
        self.Log(f"  Total Trades      : {len(self._trade_log)}")
        self.Log("-" * 70)
        self.Log("TRADE HISTORY:")
        for entry in self._trade_log:
            self.Log(entry)
        self.Log("=" * 70)
