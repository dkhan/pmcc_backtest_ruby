# QQQ Bull Call Spread - Buy 21Δ (17–25), Sell 7Δ (5–9), 35 DTE, Trade on Fridays Only

from AlgorithmImports import *

class QQQLowDeltaBullCallSpread(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2022, 1, 1)
        self.SetCash(100000)

        self.ticker = "QQQ"
        self.contracts_to_trade = 1
        self.long_delta_range = (0.17, 0.25)
        self.short_delta_range = (0.05, 0.09)
        self.target_long_delta = 0.21
        self.target_short_delta = 0.07

        equity = self.AddEquity(self.ticker, Resolution.Daily)
        equity.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        self.underlying = equity.Symbol

        option = self.AddOption(self.ticker, Resolution.Daily)
        option.SetFilter(self.OptionFilter)
        option.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.option_symbol = option.Symbol

        self.open_positions = []

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-60, +60).Expiration(34, 43)

    def OnData(self, data):
        if self.Time.weekday() != 4:  # Only trade on Fridays
            return

        self.CheckExits()

        # Avoid duplicate entries on same day
        if any(pos["entry_date"] == self.Time.date() for pos in self.open_positions):
            return

        for chain in data.OptionChains:
            if chain.Key != self.option_symbol:
                continue

            calls = [c for c in chain.Value if c.Right == OptionRight.Call and c.Expiry.date() > self.Time.date()]
            if not calls:
                continue

            # Filter and sort long calls by delta closeness
            long_candidates = [
                c for c in calls
                if c.Greeks.Delta and self.long_delta_range[0] <= c.Greeks.Delta <= self.long_delta_range[1]
            ]
            long_candidates.sort(key=lambda c: abs(c.Greeks.Delta - self.target_long_delta))

            for long_call in long_candidates:
                # Find matching short call with same expiry, higher strike, and delta in range
                short_candidates = [
                    c for c in calls
                    if c.Expiry == long_call.Expiry and
                    c.Strike > long_call.Strike and
                    c.Greeks.Delta and self.short_delta_range[0] <= c.Greeks.Delta <= self.short_delta_range[1]
                ]
                short_candidates.sort(key=lambda c: abs(c.Greeks.Delta - self.target_short_delta))

                if short_candidates:
                    short_call = short_candidates[0]

                    self.Buy(long_call.Symbol, self.contracts_to_trade)
                    self.Sell(short_call.Symbol, self.contracts_to_trade)

                    self.open_positions.append({
                        "long": long_call.Symbol,
                        "short": short_call.Symbol,
                        "expiry": long_call.Expiry.date(),
                        "entry_date": self.Time.date()
                    })

                    price = self.Securities[self.underlying].Price
                    self.Debug(f"{self.Time.date()} | Price: {price:.2f} | Buy {long_call.Strike}C (Δ={long_call.Greeks.Delta:.2f}) / " +
                               f"Sell {short_call.Strike}C (Δ={short_call.Greeks.Delta:.2f}) exp {long_call.Expiry.date()}")
                    return  # Enter one spread per Friday

    def CheckExits(self):
        to_close = []
        for pos in self.open_positions:
            if (pos["expiry"] - self.Time.date()).days <= 0:
                self.Liquidate(pos["long"])
                self.Liquidate(pos["short"])
                to_close.append(pos)

        for pos in to_close:
            self.open_positions.remove(pos)