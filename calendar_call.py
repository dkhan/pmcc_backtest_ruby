from AlgorithmImports import *

class OvernightCalendarCall(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2022, 1, 3)
        self.SetEndDate(2022, 3, 31)
        self.SetCash(100000)

        self.ticker = "SPY"
        self.equity = self.AddEquity(self.ticker, Resolution.Minute)
        self.option = self.AddOption(self.ticker, Resolution.Minute)
        self.option.SetFilter(self.OptionFilter)

        self.long_call = None
        self.short_call = None
        self.last_trade_date = None
        self.open_time = time(15, 0)  # 3:00 PM

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-10, 10).Expiration(0, 10)

    def OnData(self, slice: Slice):
        if self.Time.time() < self.open_time:
            return

        chain = slice.OptionChains.get(self.option.Symbol)
        if not chain or not slice.ContainsKey(self.equity.Symbol):
            return

        calls = [x for x in chain if x.Right == OptionRight.Call]
        if not calls:
            return

        # Find ATM strike
        price = slice[self.equity.Symbol].Price
        atm_strike = min(calls, key=lambda x: abs(x.Strike - price)).Strike

        today = self.Time.date()
        weekday = self.Time.weekday()  # Monday=0, Friday=4

        # Close all positions on Friday before weekend
        if weekday == 4 and (self.short_call or self.long_call):
            self.Debug(f"[{self.Time}] Closing all positions for weekend")
            if self.short_call and self.Portfolio[self.short_call.Symbol].Invested:
                self.Liquidate(self.short_call.Symbol)
            if self.long_call and self.Portfolio[self.long_call.Symbol].Invested:
                self.Liquidate(self.long_call.Symbol)
            self.short_call = None
            self.long_call = None
            self.last_trade_date = None
            return

        # Skip if already traded today
        if self.last_trade_date == today:
            return

        # Roll or open new spread Mon-Thu
        if weekday in [0, 1, 2, 3]:
            # Close old short leg
            if self.short_call and self.Portfolio[self.short_call.Symbol].Invested:
                self.Liquidate(self.short_call.Symbol)
                self.Debug(f"[{self.Time}] Closed short call: {self.short_call.Symbol}")
                self.short_call = None

            # Find new contracts
            short_expiry = today + timedelta(days=1)
            long_expiry = today + timedelta(days=7)

            short_contract = self.FindContract(calls, atm_strike, short_expiry)
            long_contract = self.FindContract(calls, atm_strike, long_expiry)

            if short_contract and long_contract:
                if not self.long_call:
                    self.Buy(long_contract.Symbol, 1)
                    self.long_call = long_contract
                    self.Debug(f"[{self.Time}] Opened long call: {long_contract.Symbol}")

                self.Sell(short_contract.Symbol, 1)
                self.short_call = short_contract
                self.last_trade_date = today
                self.Debug(f"[{self.Time}] Opened short call: {short_contract.Symbol}")

    def FindContract(self, contracts, strike, expiry):
        for c in contracts:
            if abs(c.Strike - strike) < 0.01 and abs((c.Expiry.date() - expiry).days) <= 1:
                return c
        return None