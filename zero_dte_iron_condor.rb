from AlgorithmImports import *

class ZeroDTEIronCondorSPY(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2025, 1, 3)
        self.SetEndDate(2025, 7, 20)
        self.SetCash(100000)

        self.spy = self.AddEquity("SPY", Resolution.Minute).Symbol

        self.option = self.AddOption("SPY", Resolution.Minute)
        self.option.SetFilter(lambda u: u.Strikes(-40, 40).Expiration(0, 1))
        self.option_symbol = self.option.Symbol

        self.contracts = {}
        self.trade_placed_today = False

        # Schedule trade attempt at 9:32 AM every day
        self.Schedule.On(self.DateRules.EveryDay("SPY"), self.TimeRules.At(9, 32), self.TryPlaceIronCondor)

    def TryPlaceIronCondor(self):
        if self.trade_placed_today:
            return

        chain = self.CurrentSlice.OptionChains.get(self.option_symbol)
        if not chain:
            self.Debug("No option chain available.")
            return

        contracts = list(chain)
        if not contracts:
            self.Debug("No contracts found in option chain.")
            return

        # Log expiration dates and strike ranges
        expirations = sorted(set([c.Expiry.date() for c in contracts]))
        puts = [c for c in contracts if c.Right == OptionRight.Put]
        calls = [c for c in contracts if c.Right == OptionRight.Call]

        self.Debug(f"{len(contracts)} contracts found at {self.Time}")
        self.Debug(f"Expirations available: {expirations}")
        if puts:
            self.Debug(f"Put strikes: {min(p.Strike for p in puts)} to {max(p.Strike for p in puts)}")
        if calls:
            self.Debug(f"Call strikes: {min(c.Strike for c in calls)} to {max(c.Strike for c in calls)}")

        # Filter to only 0DTE
        today = self.Time.date()
        contracts = [c for c in contracts if c.Expiry.date() == today]
        if not contracts:
            self.Debug(f"No 0DTE contracts available at {self.Time}")
            return

        price = self.Securities[self.spy].Price

        # Find strikes around ±$5, wings ±2
        short_put = min((c for c in contracts if c.Right == OptionRight.Put and c.Strike < price),
                        key=lambda x: abs(x.Strike - (price - 5)), default=None)
        long_put = min((c for c in contracts if c.Right == OptionRight.Put and c.Strike < short_put.Strike),
                       key=lambda x: abs(x.Strike - (short_put.Strike - 2)), default=None) if short_put else None

        short_call = min((c for c in contracts if c.Right == OptionRight.Call and c.Strike > price),
                         key=lambda x: abs(x.Strike - (price + 5)), default=None)
        long_call = min((c for c in contracts if c.Right == OptionRight.Call and c.Strike > short_call.Strike),
                        key=lambda x: abs(x.Strike - (short_call.Strike + 2)), default=None) if short_call else None

        if not all([short_put, long_put, short_call, long_call]):
            self.Debug(f"Couldn't find all Iron Condor legs at {self.Time}")
            return

        # Submit all 4 legs of Iron Condor
        self.MarketOrder(short_call.Symbol, -1)
        self.MarketOrder(long_call.Symbol, 1)
        self.MarketOrder(short_put.Symbol, -1)
        self.MarketOrder(long_put.Symbol, 1)

        self.trade_placed_today = True
        self.Debug(f"Entered 0DTE Iron Condor at {self.Time}")

    def OnEndOfDay(self, symbol):
        if symbol != self.spy:
            return

        if self.Portfolio.Invested:
            self.Liquidate()
            self.Debug(f"Exited all positions at end of day: {self.Time}")

        self.trade_placed_today = False