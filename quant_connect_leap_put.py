from AlgorithmImports import *

class BrandonLeapPutPremiumSplit(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2025, 1, 1)
        self.SetCash(300000)

        self.ticker = "NVDA"
        self.underlying = self.AddEquity(self.ticker, Resolution.Daily).Symbol

        opt = self.AddOption(self.ticker, Resolution.Daily)
        self.option = opt.Symbol

        # Better greeks for delta-based selection (needed for LEAPS)
        opt.PriceModel = OptionPriceModels.CrankNicolsonFD()

        # Margin account resembles "portfolio secured" better than cash
        self.SetBrokerageModel(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.Margin)

        # -------- Strategy parameters (tune these) --------
        self.target_dte = 730          # ~2 years
        self.dte_tolerance = 120       # accept +/- this many days
        self.put_delta_target = -0.35  # typical 0.30-0.45 put delta (negative)
        self.call_delta_target = 0.70  # LEAP call tilt (ITM-ish)
        self.take_profit_pct = 0.25    # close short put at +25% of credit
        self.use_premium_split = True  # 50% shares / 50% calls
        self.keep_shares_on_reset = True

        self.contract_qty = 1          # sell 1 put each cycle
        self.min_open_interest = 1     # filter out dead contracts

        # Option universe: include only expirations near our target window
        min_dte = max(1, self.target_dte - self.dte_tolerance)
        max_dte = self.target_dte + self.dte_tolerance
        opt.SetFilter(-20, +20, timedelta(days=min_dte), timedelta(days=max_dte))

        # -------- State --------
        self.active_put = None
        self.active_call = None
        self.put_entry_price = None          # per-share option price
        self.put_entry_credit = None         # total credit in $
        self.put_target_buyback = None       # option price to buy back at
        self.awaiting_allocation = False
        self.last_chain_log = None

        # Run once per day shortly after market open to enter/reset if needed
        self.Schedule.On(
            self.DateRules.EveryDay(self.underlying),
            self.TimeRules.AfterMarketOpen(self.underlying, 5),
            self.DailyCheck
        )

    def DailyCheck(self):
        # If no active cycle, try to open one (contract selection is in OnData)
        if self.active_put is None and not self.awaiting_allocation:
            self.Debug(f"{self.Time.date()} DailyCheck: no active put; will try to open on next chain.")
            return

        # Manage take-profit
        if self.active_put is not None and self.Portfolio[self.active_put].Invested:
            sec = self.Securities[self.active_put]
            if sec.Price > 0 and self.put_entry_price is not None:
                if sec.Price <= self.put_target_buyback:
                    self.Debug(
                        f"{self.Time.date()} TAKE PROFIT triggered: "
                        f"put mark={sec.Price:.2f} <= target={self.put_target_buyback:.2f} "
                        f"(entry={self.put_entry_price:.2f})"
                    )
                    # Close put and call
                    self.Buy(self.active_put, self.contract_qty)
                    if self.active_call is not None and self.Portfolio[self.active_call].Invested:
                        self.Sell(self.active_call, self.Portfolio[self.active_call].Quantity)

                    # Optionally keep shares; if not, liquidate underlying
                    if not self.keep_shares_on_reset:
                        self.Liquidate(self.underlying)

                    # Reset state so we can open a new cycle
                    self.ResetCycleState()

    def OnData(self, slice: Slice):
        # Only act if we have no active put and aren't waiting for allocation
        if self.active_put is not None or self.awaiting_allocation:
            return

        if self.option not in slice.OptionChains:
            return

        chain = slice.OptionChains[self.option]
        if chain is None or len(chain) == 0:
            return

        # Pick expiry closest to target DTE
        dte_map = {}
        for c in chain:
            dte = (c.Expiry.date() - self.Time.date()).days
            dte_map.setdefault(c.Expiry, []).append((dte, c))

        if len(dte_map) == 0:
            return

        # Choose expiry with minimal |DTE - target|
        best_expiry = min(dte_map.keys(), key=lambda e: abs((e.date() - self.Time.date()).days - self.target_dte))
        candidates = [x[1] for x in dte_map[best_expiry]]
        best_dte = (best_expiry.date() - self.Time.date()).days

        # Filter liquidity & get puts/calls
        puts = [c for c in candidates
                if c.Right == OptionRight.Put and c.OpenInterest >= self.min_open_interest and c.Greeks is not None]
        calls = [c for c in candidates
                 if c.Right == OptionRight.Call and c.OpenInterest >= self.min_open_interest and c.Greeks is not None]

        if len(puts) == 0 or len(calls) == 0:
            self.Debug(f"{self.Time.date()} No liquid contracts at expiry {best_expiry.date()} (DTE={best_dte}).")
            return

        # Choose put by delta closest to target (negative deltas)
        put = min(puts, key=lambda c: abs((c.Greeks.Delta or 0) - self.put_delta_target))

        # Choose call by delta closest to target
        call = min(calls, key=lambda c: abs((c.Greeks.Delta or 0) - self.call_delta_target))

        # Log selection occasionally
        if self.last_chain_log != self.Time.date():
            self.last_chain_log = self.Time.date()
            self.Debug(
                f"{self.Time.date()} SELECT expiry={best_expiry.date()} DTE={best_dte} "
                f"PUT {put.Symbol.ID.StrikePrice} Δ={put.Greeks.Delta:.2f} "
                f"CALL {call.Symbol.ID.StrikePrice} Δ={call.Greeks.Delta:.2f} "
                f"spot={self.Securities[self.underlying].Price:.2f}"
            )

        # Enter: sell put first; allocations happen after fill (OnOrderEvent)
        self.active_put = put.Symbol
        self.active_call = call.Symbol

        self.Sell(self.active_put, self.contract_qty)
        self.awaiting_allocation = True

    def OnOrderEvent(self, orderEvent: OrderEvent):
        if orderEvent.Status != OrderStatus.Filled:
            return

        # When the short put fills, record premium and allocate
        if self.active_put is not None and orderEvent.Symbol == self.active_put:
            # Sell fill => Quantity is negative for short sells; price is per-share option price
            fill_price = orderEvent.FillPrice
            fill_qty = orderEvent.FillQuantity

            # We only want the SELL fill that opened the short
            if fill_qty < 0:
                self.put_entry_price = fill_price
                self.put_entry_credit = abs(fill_qty) * fill_price * 100.0

                # Buyback target: option mark <= entry_price * (1 - take_profit_pct)
                self.put_target_buyback = self.put_entry_price * (1.0 - self.take_profit_pct)

                self.Debug(
                    f"{self.Time.date()} PUT FILLED: {self.active_put.Value} "
                    f"price={fill_price:.2f} credit=${self.put_entry_credit:,.0f} "
                    f"target_buyback={self.put_target_buyback:.2f}"
                )

                # Allocate premium: 50% shares, 50% LEAP call
                if self.use_premium_split:
                    self.AllocatePremiumSplit()
                else:
                    self.awaiting_allocation = False

        # If we bought back the put (closing), we don’t need to do anything special here.
        # State reset happens in DailyCheck after submitting closing orders.

    def AllocatePremiumSplit(self):
        if self.put_entry_credit is None:
            self.awaiting_allocation = False
            return

        half = self.put_entry_credit / 2.0

        # 1) Buy shares using half the premium
        spot = self.Securities[self.underlying].Price
        if spot > 0:
            shares_to_buy = int(half // spot)
            if shares_to_buy > 0:
                self.MarketOrder(self.underlying, shares_to_buy)
                self.Debug(f"{self.Time.date()} ALLOC shares: ${half:,.0f} -> {shares_to_buy} shares @ ~{spot:.2f}")
            else:
                self.Debug(f"{self.Time.date()} ALLOC shares: premium half ${half:,.0f} not enough to buy 1 share.")
        else:
            self.Debug(f"{self.Time.date()} ALLOC shares: spot price invalid.")

        # 2) Buy LEAP calls using half the premium
        if self.active_call is not None:
            call_price = self.Securities[self.active_call].Price
            if call_price > 0:
                # calls are 100 multiplier
                calls_to_buy = int(half // (call_price * 100.0))
                if calls_to_buy > 0:
                    self.MarketOrder(self.active_call, calls_to_buy)
                    self.Debug(
                        f"{self.Time.date()} ALLOC calls: ${half:,.0f} -> {calls_to_buy} contracts "
                        f"@ ~{call_price:.2f}"
                    )
                else:
                    self.Debug(
                        f"{self.Time.date()} ALLOC calls: premium half ${half:,.0f} not enough for 1 contract "
                        f"(call ~{call_price:.2f})."
                    )
            else:
                self.Debug(f"{self.Time.date()} ALLOC calls: call price invalid (maybe not priced yet).")

        self.awaiting_allocation = False

    def ResetCycleState(self):
        self.active_put = None
        self.active_call = None
        self.put_entry_price = None
        self.put_entry_credit = None
        self.put_target_buyback = None
        self.awaiting_allocation = False

    def OnEndOfAlgorithm(self):
        self.Debug("Backtest finished.")