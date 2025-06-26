# QQQ Bull Call Spread Strategy - Buy 21 Delta / Sell 7 Delta, 35 DTE, Every Friday
from AlgorithmImports import *

class QQQLowDeltaBullCallSpread(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2013, 1, 2)
        self.SetEndDate(2025, 6, 18)
        self.SetCash(14330)

        self.ticker = "QQQ"
        self.contracts_to_trade = 10
        self.long_delta_target = 0.21
        self.short_delta_target = 0.07

        equity = self.AddEquity(self.ticker, Resolution.Daily)
        equity.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        self.underlying = equity.Symbol

        option = self.AddOption(self.ticker, Resolution.Daily)
        option.SetFilter(self.OptionFilter)
        option.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.option_symbol = option.Symbol

        self.open_positions = []

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-15, +15).Expiration(26,30)

    def OnData(self, data):
        if self.Time.weekday() != 4:  # Only enter on Fridays
            return

        self.CheckExits()

        if len(self.open_positions) >= 1:
            return

        for chain in data.OptionChains:
            if chain.Key != self.option_symbol:
                continue

            calls = [x for x in chain.Value if x.Right == OptionRight.Call and x.Expiry.date() > self.Time.date()]
            if not calls:
                return

            # Sort by how close deltas are to the targets
            sorted_long_calls = sorted(calls, key=lambda c: abs(c.Greeks.Delta - self.long_delta_target))
            sorted_short_calls = sorted(calls, key=lambda c: abs(c.Greeks.Delta - self.short_delta_target))

            # Find the first valid combination: short strike > long strike and same expiry
            for long in sorted_long_calls:
                for short in sorted_short_calls:
                    if (
                        long.Expiry == short.Expiry and
                        short.ID.StrikePrice > long.ID.StrikePrice
                    ):
                        self.Sell(short.Symbol, self.contracts_to_trade)
                        self.Buy(long.Symbol, self.contracts_to_trade)

                        self.open_positions.append({
                            "long": long.Symbol,
                            "short": short.Symbol,
                            "expiry": long.Expiry.date()
                        })

                        self.Debug(f"{self.Time.date()} | Entered Bull Call Spread: Buy {long.ID.StrikePrice}C ({long.Greeks.Delta:.2f}) / " +
                                   f"Sell {short.ID.StrikePrice}C ({short.Greeks.Delta:.2f}) exp {long.Expiry.date()}")
                        return  # Exit after one valid spread

    def CheckExits(self):
        to_close = []
        for pos in self.open_positions:
            if (pos["expiry"] - self.Time.date()).days <= 0:
                self.Liquidate(pos["long"])
                self.Liquidate(pos["short"])
                to_close.append(pos)

        for pos in to_close:
            self.open_positions.remove(pos)
