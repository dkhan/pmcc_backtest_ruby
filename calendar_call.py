from AlgorithmImports import *

class OvernightCalendarCallSafe(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2024, 1, 3)
        self.SetEndDate(2024, 12, 31)
        self.SetCash(100000)

        self.ticker = "SPY"
        self.equity = self.AddEquity(self.ticker, Resolution.Minute)
        self.option = self.AddOption(self.ticker, Resolution.Minute)
        self.option.SetFilter(self.OptionFilter)

        self.long_call = None
        self.short_call = None
        self.last_trade_date = None
        self.open_time = time(15, 0)  # Trade after 3:00 PM

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-5, 5).Expiration(0, 10)

    def OnData(self, slice: Slice):
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

        today = self.Time.date()
        weekday = self.Time.weekday()  # Monday=0, Friday=4

        # Prevent long call from being exercised
        if self.long_call and self.long_call.Expiry.date() <= self.Time.date() + timedelta(days=1):
            if self.Portfolio[self.long_call.Symbol].Invested:
                self.Liquidate(self.long_call.Symbol)
                self.Debug(f"{self.Time} Liquidated long call before expiry: {self.long_call.Symbol}")
            self.long_call = None

        # Prevent short call from being assigned
        if self.short_call and self.short_call.Expiry.date() <= self.Time.date() + timedelta(days=1):
            if self.Portfolio[self.short_call.Symbol].Invested:
                self.Liquidate(self.short_call.Symbol)
                self.Debug(f"{self.Time} Liquidated short call before expiry: {self.short_call.Symbol}")
            self.short_call = None

        # On Friday, close all positions
        if weekday == 4:
            if self.short_call and self.Portfolio[self.short_call.Symbol].Invested:
                self.Liquidate(self.short_call.Symbol)
                self.Debug(f"{self.Time} Closed short call on Friday: {self.short_call.Symbol}")
            if self.long_call and self.Portfolio[self.long_call.Symbol].Invested:
                self.Liquidate(self.long_call.Symbol)
                self.Debug(f"{self.Time} Closed long call on Friday: {self.long_call.Symbol}")
            self.short_call = None
            self.long_call = None
            self.last_trade_date = None
            return

        # Skip if already traded today
        if self.last_trade_date == today:
            return

        # Ensure we have enough margin to open a new spread
        if self.Portfolio.MarginRemaining < 5000:
            self.Debug(f"{self.Time} Skipping trade due to low margin.")
            return

        # Close existing short call if still open
        if self.short_call and self.Portfolio[self.short_call.Symbol].Invested:
            self.Liquidate(self.short_call.Symbol)
            self.Debug(f"{self.Time} Closed previous short call: {self.short_call.Symbol}")
            self.short_call = None

        # Find short and long contracts
        short_expiry = today + timedelta(days=1)
        long_expiry = today + timedelta(days=7)

        short_contract = self.FindContract(calls, atm_strike, short_expiry)
        long_contract = self.FindContract(calls, atm_strike, long_expiry)

        if short_contract and (self.long_call or long_contract):
            if not self.long_call:
                self.Buy(long_contract.Symbol, 1)
                self.long_call = long_contract
                self.Debug(f"{self.Time} Opened long call: {long_contract.Symbol}")

            self.Sell(short_contract.Symbol, 1)
            self.short_call = short_contract
            self.last_trade_date = today
            self.Debug(f"{self.Time} Opened short call: {short_contract.Symbol}")

    def FindContract(self, contracts, strike, target_expiry):
        for c in contracts:
            if abs(c.Strike - strike) < 0.01 and abs((c.Expiry.date() - target_expiry).days) <= 1:
                return c
        return None
