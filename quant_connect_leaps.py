from AlgorithmImports import *

class LeapStrategy(QCAlgorithm):
    def Initialize(self):
        self.ticker = "SPY"  # Change ticker here
        self.contracts_to_buy = 10  # Change number of contracts here

        self.SetStartDate(2015, 1, 1)
        self.SetEndDate(2025, 6, 16)
        self.SetCash(100000)

        self.underlying = self.AddEquity(self.ticker, Resolution.Daily).Symbol
        self.option_contract = self.AddOption(self.ticker, Resolution.Daily)
        self.option_contract.SetFilter(self.OptionFilter)

        self.open_trades = []
        self.trade_log = []
        self.trade_counter = 0
        self.last_trade_date = None

    def OptionFilter(self, universe):
        return universe.IncludeWeeklys().Strikes(-10, 10).Expiration(360, 391)

    def OnData(self, data):
        if self.underlying not in data or data[self.underlying] is None:
            return

        if self.Time.weekday() != 4 or self.Time.hour < 10:
            return
        if self.last_trade_date and (self.Time.date() - self.last_trade_date).days < 14:
            return

        self.CheckExits()
        self.TryOpenNewPositions(data)
        self.last_trade_date = self.Time.date()

    def CheckExits(self):
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
                trade["exit_price"] = current_price
                trade["exit_date"] = self.Time.strftime('%Y-%m-%d')
                trade["profit_pct"] = profit_pct
                self.trade_log.append(trade)
                to_remove.append(trade)

        for trade in to_remove:
            self.open_trades.remove(trade)

    def TryOpenNewPositions(self, data):
        portfolio_value = self.Portfolio.TotalPortfolioValue
        margin_limit = portfolio_value * 0.99
        used_margin = sum([self.Securities[t["symbol"]].Price * 100 for t in self.open_trades if t["symbol"] in self.Securities])
        available_margin = margin_limit - used_margin
        open_contracts_now = sum(sec.Holdings.Quantity for sec in self.Securities.Values if sec.Holdings.Quantity > 0)

        for chain in data.OptionChains:
            if chain.Key != self.option_contract.Symbol:
                continue

            contracts = sorted(chain.Value, key=lambda x: abs(x.Greeks.Delta - 0.7))
            for c in contracts:
                days_to_expiry = (c.Expiry.date() - self.Time.date()).days
                total_cost = c.AskPrice * 100 * self.contracts_to_buy
                if 0.60 < c.Greeks.Delta < 0.80 and 360 <= days_to_expiry <= 391 and total_cost <= available_margin:
                    self.MarketOrder(c.Symbol, self.contracts_to_buy)
                    self.trade_counter += 1
                    equity_now = self.Portfolio.TotalPortfolioValue
                    trade_info = {
                        "trade_number": self.trade_counter,
                        "symbol": c.Symbol,
                        "entry_price": c.AskPrice,
                        "entry_time": self.Time,
                        "delta": c.Greeks.Delta,
                        "contracts": self.contracts_to_buy,
                        "equity_at_entry": equity_now,
                        "free_margin_at_entry": available_margin,
                        "open_contracts_at_entry": open_contracts_now
                    }
                    self.open_trades.append(trade_info)
                    log_msg = f"{self.Time.strftime('%Y-%m-%d')} Trade#{self.trade_counter} Bought {self.contracts_to_buy}x {self.ticker} {c.Expiry.strftime('%Y-%m-%d')} Call ${c.Strike:.0f} at ${c.AskPrice:.2f}, delta {c.Greeks.Delta:.2f} | Equity: ${equity_now:.2f}, Contracts: {open_contracts_now}, Free Margin: ${available_margin:.2f}"
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
                trade["exit_price"] = current_price
                trade["exit_date"] = self.Time.strftime('%Y-%m-%d')
                trade["profit_pct"] = profit_pct
                self.trade_log.append(trade)
                self.Liquidate(symbol)

        self.open_trades.clear()

        header = f"\nTRADE SUMMARY for {self.ticker}\nTrade# | Entry Date | Exit Date | Expiry | Strike | Entry Price | Exit Price | Delta | Profit % | Equity | Open Contracts | Free Margin\n" + "-"*140
        self.Debug(header)
        self.Log(header)

        for trade in self.trade_log:
            row = (
                f"{trade['trade_number']} | {trade['entry_time'].strftime('%Y-%m-%d')} | {trade.get('exit_date', '')} | {trade['symbol'].ID.Date.strftime('%Y-%m-%d')} | "
                f"{trade['symbol'].ID.StrikePrice:.0f} | {trade['entry_price']:.2f} | {trade.get('exit_price', 0):.2f} | {trade['delta']:.2f} | "
                f"{trade.get('profit_pct', '')}% | ${trade['equity_at_entry']:.2f} | {trade['open_contracts_at_entry']} | ${trade['free_margin_at_entry']:.2f}"
            )
            self.Debug(row)
            self.Log(row)

        final_equity = round(self.Portfolio.TotalPortfolioValue, 2)
        final_contracts = sum(sec.Holdings.Quantity for sec in self.Securities.Values if sec.Holdings.Quantity > 0)
        footer = f"\nFINAL EQUITY: ${final_equity}, OPEN CONTRACTS: {final_contracts}"
        self.Debug(footer)
        self.Log(footer)
