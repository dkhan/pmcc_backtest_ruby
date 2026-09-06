from AlgorithmImports import *
import time

class RSIRebalanceStrategy(QCAlgorithm):
    
    def Initialize(self):
        self.set_start_date(2016, 1, 1)
        # self.set_end_date(2026, 9, 5)
        self.settings.free_portfolio_value_percentage = 0.001
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.CASH)

        self.qqq = self.add_equity_symbol("QQQ")
        self.tqqq = self.add_equity_symbol("TQQQ")
        self.qld = self.add_equity_symbol("QLD")
        self.spy = self.add_equity_symbol("SPY")
        self.gld = self.add_equity_symbol("GLD")
        self.xlu = self.add_equity_symbol("XLU")
        self.vixy = self.add_equity_symbol("UVXY")

        self.qqqm = self.add_equity_symbol("QQQM")
        self.live_mode_symbols: dict[str, Symbol] = {
            self.qqq.value: self.qqqm,
        }

        self.strat_position_symbols: list[Symbol] = [self.tqqq, self.qqq, self.vixy, self.xlu, self.gld]

        self.schedule.on(self.date_rules.every_day(), self.time_rules.before_market_close(self.spy, 3), self.rebalance)

        if not self.live_mode:
            self.set_warmup(timedelta(days=250))
            self.set_cash(10000)

            # self.schedule.on(self.date_rules.week_end(), self.time_rules.after_market_close(self.spy, 0), self.add_cash)
            # self.schedule.on(self.date_rules.month_end(1), self.time_rules.after_market_close(self.spy, 0), self.add_cash)
            # self.schedule.on(self.date_rules.month_end(15), self.time_rules.after_market_close(self.spy, 0), self.add_cash)

        self.schedule.on(self.date_rules.every_day(), self.time_rules.before_market_close(self.spy, 6), self.reset_liquidated)            
        self.schedule.on(self.date_rules.every_day(), self.time_rules.before_market_close(self.spy, 3), self.rebalance)
        self.schedule.on(self.date_rules.every_day(), self.time_rules.before_market_close(self.spy, 2), self.rebalance)
        self.schedule.on(self.date_rules.every_day(), self.time_rules.before_market_close(self.spy, 1), self.rebalance)

        self.cashInvested = self.portfolio.cash_book["USD"].amount
        self.liquidated = False

    # def on_data(self, slice: Slice) -> None:
    #     # Obtain the mapped TradeBar of the symbol if any
    #     # if not self.portfolio[self.spy].invested:
    #     #     self.set_holdings(self.spy, 1, True)
    #     if self.live_mode:
    #         self.set_holdings_2(self.qqq, 1)

    def reset_liquidated(self):
        self.liquidated = False

    def add_equity_symbol(self, symbol: str) -> Symbol:
        s = self.add_equity(symbol, Resolution.MINUTE, data_normalization_mode=DataNormalizationMode.RAW)
        s.set_settlement_model(ImmediateSettlementModel())
        return s.Symbol

    def add_cash(self):     
        dcaCash = 200
        self.portfolio.cash_book["USD"].add_amount(dcaCash)
        self.cashInvested += dcaCash

    def check_and_get_live_symbol(self, symbol: Symbol):
        if self.portfolio.total_portfolio_value < 20000 and self.live_mode and symbol.value in self.live_mode_symbols: 
            return self.live_mode_symbols[symbol.value]
        return symbol

    def rsi_2(self,equity,period):
        extension = min(period*5,250)
        r_w = RollingWindow[float](extension)
        history = self.history(equity,extension - 1,Resolution.DAILY)
        for historical_bar in history:
            r_w.add(historical_bar.close)
        while r_w.count < extension:
            current_price = self.securities[equity].price
            r_w.add(current_price)
        if r_w.is_ready:
            average_gain = 0
            average_loss = 0
            gain = 0
            loss = 0
            for i in range(extension - 1,extension - period -1,-1):
                gain += max(r_w[i-1] - r_w[i],0)
                loss += abs(min(r_w[i-1] - r_w[i],0))
            average_gain = gain/period
            average_loss = loss/period
            for i in range(extension - period - 1,0,-1):
                average_gain = (average_gain*(period-1) + max(r_w[i-1] - r_w[i],0))/period
                average_loss = (average_loss*(period-1) + abs(min(r_w[i-1] - r_w[i],0)))/period
            if average_loss == 0:
                return 100
            else:
                rsi = 100 - (100/(1 + average_gain / average_loss))
                return rsi
        else:
            return None

    def sma_2(self,equity,period):
        r_w = RollingWindow[float](period)
        history = self.history(equity,period - 1,Resolution.DAILY)
        for historical_bar in history:
            r_w.add(historical_bar.close)
        while r_w.count < period:
            current_price = self.securities[equity].price
            r_w.add(current_price)        
        if r_w.is_ready:
            sma = sum(r_w) / period
            return sma
        else:
            return 0
    
    def get_current_strat_positions(self):
        """
        Returns:
            list[Symbol]: A list of stock symbols that this strategy buys and sells, not symbols for indicators.
        """
        current_positions = []
        for strat_symbol in self.strat_position_symbols:
            strat_symbol = self.check_and_get_live_symbol(strat_symbol)

            if self.portfolio[strat_symbol].invested:
                current_positions.append(strat_symbol)

        return current_positions

    def set_holdings_2(self, symbol: Symbol, portion):
        """IBKR Brokage Model somehow doesn't wait till liquidation finishes in set_holdings(symbol, 1, True)
        So we liquidate explicitly first and set_holdings after
        """
        symbol = self.check_and_get_live_symbol(symbol)
        liquidate_others = not self.portfolio[symbol].invested

        # liquidate any other symbols when switching
        if not self.liquidated and not self.portfolio[symbol].invested and self.portfolio.invested: 
            # for curr_pos in current_positions: 
            #     self.liquidate(curr_pos, tag="Liquidated")
            self.liquidate()
            self.liquidated = True
            return

        # after liquidating
        open_orders = self.transactions.get_open_orders()
        if (self.liquidated and len(open_orders) == 0 and 
                not self.portfolio[symbol].invested and 
                not self.portfolio.invested and 
                self.portfolio.total_portfolio_value == self.portfolio.cash): 
            self.set_holdings(symbol, 1)
            return
        
        # DCA
        if not self.liquidated and self.portfolio[symbol].invested:
            self.set_holdings(symbol, 1)
            return

        # algo first time buy
        if not self.liquidated and not self.portfolio.invested:
            self.set_holdings(symbol, 1)
            return    

    def rebalance(self):
        if self.time < self.start_date:
            return

        if not self.securities[self.spy].has_data:
            return

        rsi_value = self.rsi_2(self.qqq.value, 10)
        spy_price = self.securities[self.spy].price
        spy_sma_200 = self.sma_2(self.spy.value, 200)
        spy_sma_30 = self.sma_2(self.spy.value, 30)

        if rsi_value > 79:
            self.set_holdings_2(self.vixy, 1)
        elif rsi_value < 31:
            self.set_holdings_2(self.tqqq, 1)
        elif spy_price > spy_sma_200:
            if spy_price > spy_sma_30:
                self.set_holdings_2(self.qqq, 1)
            else:
                self.set_holdings_2(self.xlu, 1)

        else:
            if spy_price > spy_sma_30:
                self.set_holdings_2(self.qqq, 1)
            else:
                self.set_holdings_2(self.gld, 1)

    def on_end_of_algorithm(self) -> None:
        self.debug(f"Cash invested: {self.cashInvested}")