from AlgorithmImports import *

class WheelStrategyTSLA(QCAlgorithm):

    def Initialize(self):
        self.SetStartDate(2022, 9, 1)
        self.SetEndDate(2025, 10, 15)
        self.SetCash(100000)

        self.symbol = self.AddEquity("TSLA", Resolution.Minute).Symbol
        self.option = self.AddOption("TSLA", Resolution.Minute)
        self.option.SetFilter(self.OptionFilter)

        self.contract = None
        self.position = None  # 'PUT' or 'CALL'
        self.last_trade_date = None
        self.holding_stock = False
        self.option_quantity = 0
        self.buy_price = 0
        self.state = 'WAITING_TO_TRADE'

        self.last_log_date = None
        self.last_state = None

    def OptionFilter(self, universe):
        return universe.Strikes(-60, 60).Expiration(timedelta(28), timedelta(35)).IncludeWeeklys()

    def OnData(self, data: Slice):
        # Only log once per day to avoid rate limiting
        if not hasattr(self, "last_log_date") or self.last_log_date != self.Time.date():
            #self.Debug(f"[OnData] {self.Time} | State: {self.state}, Holding Stock: {self.holding_stock}, Position: {self.position}")
            self.last_log_date = self.Time.date()
        # Handle expiry
        if self.contract and self.Time.date() >= self.contract.Expiry.date():
            stock_price = self.Securities[self.symbol].Price
            status = "Expired Worthless"

            # Correct handling for PUTs
            if self.position == "PUT" and stock_price < self.contract.Strike:
                status = "Executed (PUT Assigned)"
                self.holding_stock = True
                self.buy_price = self.contract.Strike

            # Correct handling for CALLs
            elif self.position == "CALL" and stock_price > self.contract.Strike:
                status = "Executed (CALL Assigned)"
                self.holding_stock = False

            self.Debug(f"[EXPIRY] {self.Time.date()} | {status}, Price: {stock_price:.2f}, Balance: {self.RollingBalance():.2f}")

            self.contract = None
            self.position = None
            self.option_quantity = 0
            self.last_trade_date = self.Time.date()
            self.state = 'WAITING_TO_TRADE'
            return

        # Wait at least 1 day after previous expiry before trying again
        if self.last_trade_date and self.Time.date() <= self.last_trade_date:
            return

        if self.state == 'WAITING_TO_TRADE' and not self.contract:
            chain = data.OptionChains.get(self.option.Symbol)
            if not chain:
                #self.Debug(f"[OnData] {self.Time} | No option chain available yet")
                return

            if not self.holding_stock:
                traded = self.SellPutOption(chain)
            else:
                traded = self.SellCallOption(chain)

            if traded:
                self.state = 'TRADED'
            # else:
            #     self.Debug(f"[OnData] {self.Time} | No valid option found to trade")

    def SellPutOption(self, chain):
        affordable_puts = [x for x in chain
                           if x.Right == OptionRight.Put
                           and x.Greeks.Delta is not None
                           and self.Portfolio.Cash >= x.Strike * 100]

        puts = [x for x in affordable_puts if 0.25 <= abs(x.Greeks.Delta) <= 0.35]
        if not puts:
            return False

        contract = sorted(puts, key=lambda x: x.Expiry)[0]
        quantity = int(self.Portfolio.Cash // (contract.Strike * 100))
        if quantity < 1:
            return False

        self.contract = contract
        self.position = 'PUT'
        self.option_quantity = quantity
        self.MarketOrder(contract.Symbol, -quantity)
        self.LogTrade("PUT", contract, quantity)
        return True

    def SellCallOption(self, chain):
        calls = [x for x in chain if x.Right == OptionRight.Call and x.Greeks.Delta is not None]

        preferred = [x for x in calls if 0.25 <= abs(x.Greeks.Delta) <= 0.35 and x.Strike >= self.buy_price]
        if preferred:
            contract = sorted(preferred, key=lambda x: x.Expiry)[0]
        else:
            fallback = [x for x in calls if x.Strike >= self.buy_price]
            if not fallback:
                return False
            contract = sorted(fallback, key=lambda x: x.Expiry)[0]

        quantity = int(self.Portfolio[self.symbol].Quantity / 100)
        if quantity < 1:
            return False

        self.contract = contract
        self.position = 'CALL'
        self.option_quantity = quantity
        self.MarketOrder(contract.Symbol, -quantity)
        self.LogTrade("CALL", contract, quantity)
        return True

    def OnAssignment(self, assignmentEvent):
        if assignmentEvent.Direction == OrderDirection.Buy:
            self.holding_stock = True
        elif assignmentEvent.Direction == OrderDirection.Sell:
            self.holding_stock = False

        self.state = 'WAITING_TO_TRADE'

    def LogTrade(self, option_type, contract, quantity):
        premium = self.Securities[contract.Symbol].Price
        delta = contract.Greeks.Delta
        dte = (contract.Expiry.date() - self.Time.date()).days
        price = self.Securities[self.symbol].Price

        self.Debug(f"[TRADE] {self.Time.date()} | {option_type}, Strike: {contract.Strike}, "
                   f"DTE: {dte}, Delta: {delta:.2f}, Premium: {premium:.2f}, "
                   f"Price: {price:.2f}, Qty: {quantity}, Balance: {self.RollingBalance():.2f}")

    def RollingBalance(self):
        return self.Portfolio.TotalPortfolioValue