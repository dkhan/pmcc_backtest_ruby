from AlgorithmImports import *

class QQQLeapStrategy(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2015, 1, 1)
        self.SetEndDate(2024, 12, 31)
        self.SetCash(100000)

        self.qqq = self.AddEquity("QQQ", Resolution.Daily).Symbol
        self.option = self.AddOption("QQQ", Resolution.Daily)
        self.option.SetFilter(self.OptionFilter)

        self.last_close = None
        self.entry_prices = {}
        self.entry_times = {}
        self.positions = []
        self.trade_log = []
        self.out_of_money_flags = set()

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-10, 10).Expiration(360, 370)

    def OnData(self, data):
        if self.qqq not in data or data[self.qqq] is None:
            return

        price = data[self.qqq].Close

        # Detect -1% daily drop
        if self.last_close:
            pct_drop = (price - self.last_close) / self.last_close
            if pct_drop <= -0.01:
                self.TryOpenNewPosition(data)

        self.last_close = price

        # Check for exits or out-of-money
        to_remove = []
        for symbol in self.positions:
            if symbol not in self.Securities or not self.Securities[symbol].Invested:
                continue

            sec = self.Securities[symbol]
            current_price = sec.Price
            entry_price = self.entry_prices.get(symbol, 0)

            if current_price >= 1.5 * entry_price:
                self.Liquidate(symbol)
                profit_pct = round((current_price - entry_price) / entry_price * 100, 2)
                self.trade_log.append({
                    "EntryDate": self.entry_times[symbol].strftime('%Y-%m-%d'),
                    "ExitDate": self.Time.strftime('%Y-%m-%d'),
                    "EntryPrice": entry_price,
                    "ExitPrice": current_price,
                    "Profit%": profit_pct
                })
                to_remove.append(symbol)

            elif current_price < entry_price:
                if symbol not in self.out_of_money_flags:
                    self.Debug(f"{symbol} is out of the money at {current_price:.2f} < {entry_price:.2f}")
                    self.out_of_money_flags.add(symbol)
                return

        for symbol in to_remove:
            if symbol in self.positions:
                self.positions.remove(symbol)
            if symbol in self.entry_prices:
                del self.entry_prices[symbol]
            if symbol in self.entry_times:
                del self.entry_times[symbol]

    def TryOpenNewPosition(self, data):
        portfolio_value = self.Portfolio.TotalPortfolioValue
        margin_limit = portfolio_value * 0.10  # 10% cap
        used_margin = sum([self.Securities[s].Price * 100 for s in self.positions if s in self.Securities])
        available_margin = margin_limit - used_margin

        for chain in data.OptionChains:
            if chain.Key != self.option.Symbol:
                continue
            contracts = sorted(chain.Value, key=lambda x: abs(x.Greeks.Delta - 0.7))
            for c in contracts:
                if 0.60 < c.Greeks.Delta < 0.80 and c.AskPrice * 100 <= available_margin:
                    self.MarketOrder(c.Symbol, 1)
                    self.entry_prices[c.Symbol] = c.AskPrice
                    self.entry_times[c.Symbol] = self.Time
                    self.positions.append(c.Symbol)
                    self.Debug(f"Bought LEAP {c.Symbol} at {c.AskPrice:.2f}, delta {c.Greeks.Delta:.2f}")
                    return

    def OnEndOfAlgorithm(self):
        if len(self.trade_log) == 0:
            self.Debug("No trades closed profitably. No CSV to display.")
            return

        header = "\nTRADE SUMMARY\nEntry Date | Exit Date | Entry Price | Exit Price | Profit %\n" + "-"*60
        self.Debug(header)
        self.Log(header)
        for trade in self.trade_log:
            row = f"{trade['EntryDate']} | {trade['ExitDate']} | {trade['EntryPrice']:.2f} | {trade['ExitPrice']:.2f} | {trade['Profit%']:.2f}%"
            self.Debug(row)
            self.Log(row)
