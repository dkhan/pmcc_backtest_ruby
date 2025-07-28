from AlgorithmImports import *

class QQQLowDeltaBullCallSpreadWithROIClose(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2020, 1, 1)
        self.SetEndDate(2022, 1, 1)
        self.SetCash(100000)

        self.ticker = "QQQ"
        self.contracts_to_trade = 1

        self.target_long_delta = 0.21
        self.target_short_delta = 0.07
        self.long_delta_range = (0.17, 0.25)
        self.short_delta_range = (0.05, 0.09)

        self.positions = []

        equity = self.AddEquity(self.ticker, Resolution.Daily)
        equity.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        self.underlying = equity.Symbol

        option = self.AddOption(self.ticker, Resolution.Daily)
        option.SetFilter(self.OptionFilter)
        option.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.option_symbol = option.Symbol

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-60, +60).Expiration(34, 43)

    def OnData(self, data):
        if self.Time.weekday() != 4:
            return

        self.CheckExits(data)

        if any(pos["entry_date"] == self.Time.date() for pos in self.positions if not pos["closed"]):
            return

        for chain in data.OptionChains:
            if chain.Key != self.option_symbol:
                continue

            calls = [c for c in chain.Value if c.Right == OptionRight.Call and c.Expiry.date() > self.Time.date()]
            if not calls:
                continue

            long_candidates = [
                c for c in calls
                if c.Greeks.Delta and self.long_delta_range[0] <= c.Greeks.Delta <= self.long_delta_range[1]
            ]
            long_candidates.sort(key=lambda c: abs(c.Greeks.Delta - self.target_long_delta))

            for long_call in long_candidates:
                short_candidates = [
                    c for c in calls
                    if c.Expiry == long_call.Expiry and
                    c.Strike > long_call.Strike and
                    c.Greeks.Delta and self.short_delta_range[0] <= c.Greeks.Delta <= self.short_delta_range[1]
                ]
                short_candidates.sort(key=lambda c: abs(c.Greeks.Delta - self.target_short_delta))

                if short_candidates:
                    short_call = short_candidates[0]

                    long_price = self.Securities[long_call.Symbol].AskPrice
                    short_price = self.Securities[short_call.Symbol].BidPrice
                    entry_premium = short_price - long_price
                    entry_price = self.Securities[self.underlying].Price

                    self.Buy(long_call.Symbol, self.contracts_to_trade)
                    self.Sell(short_call.Symbol, self.contracts_to_trade)

                    self.positions.append({
                        "long": long_call.Symbol,
                        "short": short_call.Symbol,
                        "entry_date": self.Time.date(),
                        "expiry": long_call.Expiry.date(),
                        "entry_premium": entry_premium,
                        "entry_underlying": entry_price,
                        "exit_premium": None,
                        "exit_underlying": None,
                        "exit_date": None,
                        "close_reason": None,
                        "closed": False
                    })

                    self.Debug(f"{self.Time.date()} | Price: {entry_price:.2f} | Buy {long_call.Strike}C (Δ={long_call.Greeks.Delta:.2f}) / "
                               f"Sell {short_call.Strike}C (Δ={short_call.Greeks.Delta:.2f}) exp {long_call.Expiry.date()}")
                    return

    def CheckExits(self, data):
        for pos in self.positions:
            if pos["closed"]:
                continue

            long = pos["long"]
            short = pos["short"]

            if not self.Securities.ContainsKey(long) or not self.Securities.ContainsKey(short):
                continue

            long_price = self.Securities[long].BidPrice
            short_price = self.Securities[short].AskPrice

            if long_price == 0 and short_price == 0:
                continue

            current_value = short_price - long_price
            roi = current_value / pos["entry_premium"] if pos["entry_premium"] != 0 else 0
            exit_price = self.Securities[self.underlying].Price

            # Take profit
            if roi > 4.20:
                self.Liquidate(long)
                self.Liquidate(short)
                pos.update({
                    "exit_premium": current_value,
                    "exit_underlying": exit_price,
                    "exit_date": self.Time.date(),
                    "close_reason": "take profit",
                    "closed": True
                })

            # Stop loss
            elif roi > 0 and roi < (1 - 0.84):
                self.Liquidate(long)
                self.Liquidate(short)
                pos.update({
                    "exit_premium": current_value,
                    "exit_underlying": exit_price,
                    "exit_date": self.Time.date(),
                    "close_reason": "stop loss",
                    "closed": True
                })

            # Expiration
            elif (pos["expiry"] - self.Time.date()).days == 0:
                self.Liquidate(long)
                self.Liquidate(short)
                pos.update({
                    "exit_premium": current_value,
                    "exit_underlying": exit_price,
                    "exit_date": self.Time.date(),
                    "close_reason": "exercised",
                    "closed": True
                })

    def OnEndOfAlgorithm(self):
        sorted_positions = sorted(self.positions, key=lambda x: x["entry_date"])
        self.Debug("====== TRADE SUMMARY ======")
        self.Debug("OPENED     | CLOSED     | PRICE ON OPEN | PRICE ON CLOSE | LONG/SHORT STRIKE | EXP        | DTE | PREMIUM | P/L     | REASON       | ROI %")
        
        for pos in sorted_positions:
            if not pos["closed"]:
                continue

            entry_price = pos.get("entry_underlying", 0)
            exit_price = pos.get("exit_underlying", 0)
            exp = pos["expiry"]
            dte = (exp - pos["entry_date"]).days

            premium_in = pos["entry_premium"]
            premium_out = pos["exit_premium"]
            profit = (premium_out - premium_in) * 100
            roi = ((premium_out / premium_in) * 100) if premium_in != 0 else 0

            long_strike = pos["long"].ID.StrikePrice
            short_strike = pos["short"].ID.StrikePrice

            self.Debug(f"{pos['entry_date']} | {pos['exit_date']} | {entry_price:14.2f} | {exit_price:15.2f} | "
                       f"{long_strike:.1f}/{short_strike:.1f}         | {exp} | {dte:3} | "
                       f"{-premium_in:7.2f} | {-profit:7.2f} | {pos['close_reason']:12} | {roi:6.2f}")