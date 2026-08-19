            return self.STATE_LONG_STOCK
        return self.STATE_CASH
    def _plot_charts(self):
        equity = self.Portfolio.TotalPortfolioValue
        tsla_price = self.Securities[self.tsla].Price
        state = self._position_state_code()
        benchmark_equity = self._benchmark_equity()
        self.Plot("Golden Wheel vs Buy & Hold", "Strategy Equity", equity)
        if benchmark_equity > 0:
            self.Plot("Golden Wheel vs Buy & Hold", "Buy & Hold Equity", benchmark_equity)
        if tsla_price > 0:
            self.Plot("Golden Wheel vs Buy & Hold", "TSLA Price", tsla_price)
        self.Plot("Golden Wheel vs Buy & Hold", "Position State", state)
    def OnEndOfAlgorithm(self):
        state_names = {
            self.STATE_CASH: "CASH",
            self.STATE_SHORT_PUT: "SHORT_PUT",
            self.STATE_LONG_STOCK_SHORT_CALL: "LONG_STOCK + SHORT_CALL",
            self.STATE_LONG_STOCK: "LONG_STOCK",
        }
        final_state = state_names.get(self._position_state_code(), "UNKNOWN")
        strategy_equity = self.Portfolio.TotalPortfolioValue
        benchmark_equity = self._benchmark_equity()
        outperformance = strategy_equity - benchmark_equity
        self.Log("=" * 72)
        self.Log("TSLA LEAP GOLDEN WHEEL — BACKTEST SUMMARY")
        self.Log(f"Final strategy equity : ${strategy_equity:,.0f}")
        self.Log(f"Final buy & hold equity: ${benchmark_equity:,.0f}")
        self.Log(f"Absolute difference    : ${outperformance:,.0f}")
        if benchmark_equity > 0:
            pct = (strategy_equity / benchmark_equity - 1.0) * 100.0
            self.Log(f"Relative vs benchmark  : {pct:+.1f}%")
        self.Log(f"Final position state   : {final_state}")
        self.Log(f"TSLA shares held       : {self._tsla_shares()}")
        self.Log("=" * 72)
Added quant_connect_leap_wheel.py — a QuantConnect LEAP Golden W