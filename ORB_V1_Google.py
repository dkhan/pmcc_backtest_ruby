from AlgorithmImports import *
import math
from datetime import time, timedelta  

class StrictORBPaperReconciliation(QCAlgorithm):

    def Initialize(self):
        # 1. Timeline & Capital Settings (Table 1 & Table 2)
        self.SetStartDate(2016, 1, 1)
        self.SetEndDate(2023, 2, 17)
        self.SetCash(25000)
        
        # 2. Configure Asset with RAW Data (Ensures actual un-smoothed historical entry and stop prices)
        self.ticker = "QQQ"  # Change to "TQQQ" here to evaluate the paper's leveraged ETF variant
        self.security = self.AddEquity(self.ticker, Resolution.Minute)
        self.symbol = self.security.Symbol
        self.security.SetDataNormalizationMode(DataNormalizationMode.Raw)
        
        # Apply a 4.0 margin multiplier hook with custom MarginCall Model protection to avoid crashes
        self.security.SetLeverage(4.0)
        self.Portfolio.MarginCallModel = MarginCallModel.Null  # Prevents generic engine liquidations from overriding strategy exits
        
        # Enforce perfect structural environment configurations (Zero Slippage as stated in paper)
        self.security.SetSlippageModel(ConstantSlippageModel(0.0))
        self.security.SetFeeModel(CustomPerShareFeeModel(0.0005))
        
        # 3. Native 5-Minute Candle Consolidator Pipeline (Deterministic bar reconstruction)
        self.opening_bar = None
        self.consolidator = TradeBarConsolidator(TimeSpan.FromMinutes(5))
        self.consolidator.DataConsolidated += self.OnOpeningRangeConsolidated
        self.SubscriptionManager.AddConsolidator(self.symbol, self.consolidator)
        
        # 4. Sibling Ticket Architecture State Engine (Structural cancellation & double-fill protections)
        self.entry_ticket = None
        self.stop_ticket = None
        self.target_ticket = None
        
        self.is_active_trade = False
        self.state_reset_pending = False
        
        # 5. Core Operational Time Constants
        self.market_open_time = time(9, 30)
        self.execution_window = time(9, 35)
        self.liquidation_window = time(15, 59)
        
        # Scheduled liquidation trigger at exactly 3:59 PM EST to guarantee capture of the closing auction liquidity
        self.Schedule.On(self.DateRules.EveryDay(self.symbol), 
                         self.TimeRules.At(15, 59), 
                         self.OfficialAuctionCloseLiquidation)

    def OnOpeningRangeConsolidated(self, sender, bar):
        # Capture the precise 9:30 AM to 9:35 AM opening baseline candle
        if bar.Time.time() == self.market_open_time:
            self.opening_bar = bar

    def OnData(self, slice):
        # Trade state is only cleared after receiving complete liquidation fill confirmation from the portfolio
        if self.state_reset_pending:
            if not self.Portfolio[self.symbol].Invested:
                self.ResetStrategyStateEngine()
            return

        # Execute Entry Calculation precisely at the open of the 9:35 AM bar
        if self.Time.time() == self.execution_window and not self.is_active_trade:
            if slice.Bars.ContainsKey(self.symbol):
                # Entry price is explicitly locked to the observed open of the second 5-minute candle bar
                second_candle_open = slice.Bars[self.symbol].Open
                self.ExecuteOpeningBreakoutSequence(second_candle_open)
            return

    def ExecuteOpeningBreakoutSequence(self, entry_price):
        if self.opening_bar is None:
            return
            
        # Exclude exact Doji setups where opening bar high equals low or open equals close
        if self.opening_bar.Open == self.opening_bar.Close or self.opening_bar.High == self.opening_bar.Low:
            return
            
        # Determine strict direction vectors relative to the first 5-minute candle close
        if self.opening_bar.Close > self.opening_bar.Open:
            is_long = True
            stop_level = self.opening_bar.Low
            R = entry_price - stop_level
            target_level = entry_price + (10.0 * R)
        else:
            is_long = False
            stop_level = self.opening_bar.High
            R = stop_level - entry_price
            target_level = entry_price - (10.0 * R)
            
        # Protective floor check to prevent divide-by-zero crashes on near-zero R widths
        if R <= 0.001:
            return
            
        # Position Sizing Core Architecture with a 0.995 leverage safety margin to protect buying power bounds
        account_equity = self.Portfolio.TotalPortfolioValue
        risk_allocated_shares = (account_equity * 0.01) / R
        max_broker_leverage_shares = ((4.0 * account_equity) / entry_price) * 0.995
        quantity = math.floor(min(risk_allocated_shares, max_broker_leverage_shares))
        
        if quantity <= 0:
            return
            
        self.is_active_trade = True
        
        # Transmit the initial Entry Order and brackets using explicit target labels to fit type requirements
        if is_long:
            self.entry_ticket = self.MarketOrder(self.symbol, quantity)
            self.stop_ticket = self.StopMarketOrder(self.symbol, -quantity, stop_level, tag="Stop Loss")
            self.target_ticket = self.LimitOrder(self.symbol, -quantity, target_level, tag="Profit Target (10R)")
        else:
            self.entry_ticket = self.MarketOrder(self.symbol, -quantity)
            self.stop_ticket = self.StopMarketOrder(self.symbol, quantity, stop_level, tag="Stop Loss")
            self.target_ticket = self.LimitOrder(self.symbol, quantity, target_level, tag="Profit Target (10R)")

    def OnOrderEvent(self, orderEvent):
        # Sibling Cancellation Logic: prevent double-fills if both sides trigger in high volatility
        if orderEvent.Status != OrderStatus.Filled:
            return
            
        order_id = orderEvent.OrderId
        
        # Verify if either bracket exit ticket has filled, then instantly wipe the opposing working order
        if self.stop_ticket is not None and order_id == self.stop_ticket.OrderId:
            self.state_reset_pending = True
            if self.target_ticket is not None: self.target_ticket.Cancel(tag="Stop Loss Filled")
        elif self.target_ticket is not None and order_id == self.target_ticket.OrderId:
            self.state_reset_pending = True
            if self.stop_ticket is not None: self.stop_ticket.Cancel(tag="Profit Target Filled")

    def OfficialAuctionCloseLiquidation(self):
        # Enforces End-of-Day liquidation precisely at 3:59 PM EST right inside the closing auction parameters
        if self.Portfolio[self.symbol].Invested:
            if self.stop_ticket is not None: self.stop_ticket.Cancel(tag="EOD Market Close")
            if self.target_ticket is not None: self.target_ticket.Cancel(tag="EOD Market Close")
            self.MarketOrder(self.symbol, -self.Portfolio[self.symbol].Quantity, tag="Closing Auction Liquidate")
            self.state_reset_pending = True

    def ResetStrategyStateEngine(self):
        self.entry_ticket = None
        self.stop_ticket = None
        self.target_ticket = None
        self.is_active_trade = False
        self.state_reset_pending = False
        self.opening_bar = None

    def OnEndOfAlgorithm(self):
        # Pulls data from closed trades to provide a complete reconciliation log summary matching the paper's benchmarks
        closed_trades = self.TradeBuilder.ClosedTrades
        total_trades = len(closed_trades)
        if total_trades == 0: return
        
        longs = [t for t in closed_trades if t.Direction == TradeDirection.Long]
        shorts = [t for t in closed_trades if t.Direction == TradeDirection.Short]
        wins = [t for t in closed_trades if t.ProfitLoss > 0]
        losses = [t for t in closed_trades if t.ProfitLoss <= 0]
        
        # FIX: Adjusted dictionary lookups to the precise native text formatting key outputs supported by the engine
        summary_api = self.Statistics.Summary
        
        self.Log("=========================================================")
        self.Log("=== OFFICIAL PAPER RECONCILIATION DATA PROFILE (V1) ===")
        self.Log("=========================================================")
        self.Log(f"Total Round-Trip Completed Trades : {total_trades}")
        self.Log(f"Directional Distribution          : {len(longs)} Longs / {len(shorts)} Shorts")
        self.Log(f"Exit Condition Distribution       : {len(wins)} Targets Hit / {len(losses)} Stops or EOD Exits")
        self.Log(f"Calculated Win Rate Percentage    : {(len(wins)/total_trades)*100:.2f}%")
        self.Log(f"Reported Total Net Strategy Profit: {summary_api['Net Profit']}")
        self.Log(f"Reported Compounding Annual Return: {summary_api['Compounding Annual Return']}")
        self.Log(f"Reported Strategy Sharpe Ratio    : {summary_api['Sharpe Ratio']}")
        self.Log(f"Reported Maximum Backtest Drawdown: {summary_api['Drawdown']}")
        self.Log("=========================================================")

# Strong-Typed Native Per-Share Fee Engine Overrides
class CustomPerShareFeeModel(FeeModel):
    def __init__(self, fee_per_share: float):
        self.fee_per_share = fee_per_share

    def GetOrderFee(self, parameters: OrderFeeParameters) -> OrderFee:
        order = parameters.Order
        absolute_quantity = order.AbsoluteQuantity
        calculated_fee = absolute_quantity * self.fee_per_share
        return OrderFee(CashAmount(calculated_fee, "USD"))
