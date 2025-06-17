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
        self.open_trades = []  # List of dicts with trade metadata
        self.trade_log = []
        self.trade_counter = 0

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-10, 10).Expiration(360, 391)

    def OnData(self, data):
        if self.qqq not in data or data[self.qqq] is None:
            return

        price = data[self.qqq].Close

        # Detect -1% daily drop
        # if self.last_close:
        #     pct_drop = (price - self.last_close) / self.last_close
        #     if pct_drop <= -0.005:
        self.TryOpenNewPosition(data)

        self.last_close = price

        # Check for exits: profit target or near expiration
        to_remove = []
        for trade in self.open_trades:
            symbol = trade["symbol"]
            if symbol not in self.Securities or not self.Securities[symbol].Invested:
                continue

            current_price = self.Securities[symbol].Price
            entry_price = trade["entry_price"]
            expiry_days_remaining = (symbol.ID.Date.date() - self.Time.date()).days

            if current_price >= 1.5 * entry_price or expiry_days_remaining <= 30:
                self.Liquidate(symbol)
                profit_pct = round((current_price - entry_price) / entry_price * 100, 2)
                self.trade_log.append({
                    "Trade#": trade["trade_number"],
                    "EntryDate": trade["entry_time"].strftime('%Y-%m-%d'),
                    "ExitDate": self.Time.strftime('%Y-%m-%d'),
                    "Expiry": symbol.ID.Date.strftime('%Y-%m-%d'),
                    "Strike": symbol.ID.StrikePrice,
                    "EntryPrice": entry_price,
                    "ExitPrice": current_price,
                    "Delta": trade["delta"],
                    "Profit%": profit_pct
                })
                to_remove.append(trade)

        for trade in to_remove:
            self.open_trades.remove(trade)

    def TryOpenNewPosition(self, data):
        portfolio_value = self.Portfolio.TotalPortfolioValue
        margin_limit = portfolio_value * 0.99
        used_margin = sum([self.Securities[t["symbol"]].Price * 100 for t in self.open_trades if t["symbol"] in self.Securities])
        available_margin = margin_limit - used_margin

        for chain in data.OptionChains:
            if chain.Key != self.option.Symbol:
                continue
            contracts = sorted(chain.Value, key=lambda x: abs(x.Greeks.Delta - 0.7))
            for c in contracts:
                days_to_expiry = (c.Expiry.date() - self.Time.date()).days
                if (
                    0.60 < c.Greeks.Delta < 0.80 and 
                    360 <= days_to_expiry <= 391 and 
                    c.AskPrice * 100 <= available_margin
                ):
                    self.MarketOrder(c.Symbol, 1)
                    self.trade_counter += 1
                    self.open_trades.append({
                        "trade_number": self.trade_counter,
                        "symbol": c.Symbol,
                        "entry_price": c.AskPrice,
                        "entry_time": self.Time,
                        "delta": c.Greeks.Delta
                    })
                    log_msg = f"{self.Time.strftime('%Y-%m-%d')} Trade#{self.trade_counter} Bought LEAP QQQ {c.Expiry.strftime('%Y-%m-%d')} Call ${c.Strike:.0f} at ${c.AskPrice:.2f}, delta {c.Greeks.Delta:.2f}"
                    self.Debug(log_msg)
                    self.Log(log_msg)
                    return

    def OnEndOfAlgorithm(self):
        for trade in list(self.open_trades):
            symbol = trade["symbol"]
            if symbol in self.Securities and self.Securities[symbol].Invested:
                current_price = self.Securities[symbol].Price
                entry_price = trade["entry_price"]
                profit_pct = round((current_price - entry_price) / entry_price * 100, 2) if entry_price > 0 else 0
                self.trade_log.append({
                    "Trade#": trade["trade_number"],
                    "EntryDate": trade["entry_time"].strftime('%Y-%m-%d'),
                    "ExitDate": self.Time.strftime('%Y-%m-%d'),
                    "Expiry": symbol.ID.Date.strftime('%Y-%m-%d'),
                    "Strike": symbol.ID.StrikePrice,
                    "EntryPrice": entry_price,
                    "ExitPrice": current_price,
                    "Delta": trade["delta"],
                    "Profit%": profit_pct
                })
                self.Liquidate(symbol)
        self.open_trades.clear()

        # Output all trade logs
        header = "\nTRADE SUMMARY\nTrade# | Entry Date | Exit Date | Expiry | Strike | Entry Price | Exit Price | Delta | Profit %\n" + "-"*95
        self.Debug(header)
        self.Log(header)
        for i, trade in enumerate(self.trade_log):
            row = f"{trade['Trade#']} | {trade['EntryDate']} | {trade['ExitDate']} | {trade['Expiry']} | {trade['Strike']} | {trade['EntryPrice']:.2f} | {trade['ExitPrice']:.2f} | {trade['Delta']:.2f} | {trade['Profit%']:.2f}%"
            self.Debug(row)
            self.Log(row)
            
            if (i + 1) % 50 == 0:
                self.Debug(f"... pausing after {i + 1} logs ...")
                self.Sleep(1000)  # wait 1 second (1000 ms)
