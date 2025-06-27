from AlgorithmImports import *

class OvernightCalendarCallSafe(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2024, 1, 8)
        self.SetEndDate(2024, 1, 28)
        self.SetCash(30000)  # Adjust based on short call margin
        self.contract_qty = 1

        self.ticker = "SPY"
        self.equity = self.AddEquity(self.ticker, Resolution.Minute)
        self.option = self.AddOption(self.ticker, Resolution.Minute)
        self.option.SetFilter(self.OptionFilter)

        self.long_call = None
        self.short_call = None
        self.selected_strike = None
        self.last_trade_date = None
        self.open_time = time(15, 45)  # Place trades after 3:45 PM

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-5, 5).Expiration(0, 7)

    def OnData(self, slice: Slice):     
        today = self.Time.date()
        weekday = self.Time.weekday()  # Monday=0, Friday=4

        # Reset daily trade lock
        if self.last_trade_date != today:
            self.last_trade_date = None

        # Close short call at 9:35 AM on expiration day
        if self.short_call and self.short_call.Expiry.date() == today and self.Time.hour == 9 and self.Time.minute == 35:
            if self.Portfolio[self.short_call.Symbol].Invested:
                self.Liquidate(self.short_call.Symbol)
                self.Debug(f"{self.Time} Liquidated short call at open: {self.short_call.Symbol}")
            self.short_call = None

        if self.Time.time() < self.open_time:
            return

        chain = slice.OptionChains.get(self.option.Symbol)
        if not chain or not slice.ContainsKey(self.equity.Symbol):
            return

        calls = [x for x in chain if x.Right == OptionRight.Call]
        if not calls:
            return

        price = slice[self.equity.Symbol].Price
        atm_strike = min(calls, key=lambda x: abs(x.Strike - price)).Strike

        # Close long call if it's expiring today
        if self.long_call and self.long_call.Expiry.date() == today:
            if self.Portfolio[self.long_call.Symbol].Invested:
                self.Liquidate(self.long_call.Symbol)
                self.Debug(f"{self.Time} Liquidated long call before expiry: {self.long_call.Symbol}")
            self.long_call = None
            self.selected_strike = None

        # No new trades on Friday
        if weekday == 4:
            return

        # Skip duplicate trading in a day
        if self.last_trade_date == today:
            return

        # Get available expiries beyond today
        expiries = sorted(set([c.Expiry.date() for c in calls if c.Expiry.date() > today]))
        if len(expiries) < 2:
            return

        short_expiry = expiries[0]
        long_expiry = expiries[min(6, len(expiries) - 1)]

        # If no long call, open one and lock strike
        if not self.long_call:
            self.selected_strike = atm_strike
            long_contract = self.FindContract(calls, self.selected_strike, long_expiry)
            if long_contract and not self.Portfolio[long_contract.Symbol].Invested:
                self.Buy(long_contract.Symbol, self.contract_qty)
                self.long_call = long_contract
                self.Debug(f"{self.Time} Opened long call: {long_contract.Symbol}")

        # Sell short call at same strike as long call
        if self.selected_strike is not None and self.short_call is None:
            short_contract = self.FindContract(calls, self.selected_strike, short_expiry)
            if short_contract and not self.Portfolio[short_contract.Symbol].Invested:
                self.Sell(short_contract.Symbol, self.contract_qty)
                self.short_call = short_contract
                self.Debug(f"{self.Time} Opened short call: {short_contract.Symbol}")
                self.last_trade_date = today

    def FindContract(self, contracts, strike, target_expiry):
        best_match = None
        smallest_expiry_diff = float('inf')

        for c in contracts:
            contract_expiry = c.Expiry.date()
            if abs(c.Strike - strike) < 0.01 and contract_expiry >= target_expiry:
                expiry_diff = (contract_expiry - target_expiry).days
                if expiry_diff <= 1 and expiry_diff < smallest_expiry_diff:
                    best_match = c
                    smallest_expiry_diff = expiry_diff

        return best_match