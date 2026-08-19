from QuantConnect import *
from QuantConnect.Algorithm import *
from QuantConnect.Data import *
from QuantConnect.Orders import OrderStatus
from datetime import timedelta   # ← Added for TimeSpan equivalent

class ZeroDTEBullPutSpreadSPX(QCAlgorithm):
    def Initialize(self):
        self.SetStartDate(2024, 1, 1)
        self.SetEndDate(2026, 4, 2)
        self.SetCash(100_000)

        # Underlying SPX
        self.spx = self.AddIndex("SPX", Resolution.Minute).Symbol

        # VIX for volatility filter
        self.vix = self.AddIndex("VIX", Resolution.Minute).Symbol

        # SPXW 0DTE options
        self.option = self.AddIndexOption(self.spx, "SPXW", Resolution.Minute)
        self.option.SetFilter(lambda u: u
            .Strikes(-100, 100)
            .Expiration(0, 1)
            .IncludeWeeklys()
        )

        self.option_symbol = self.option.Symbol
        self.spread_active = False
        self.entry_credit = 0.0
        self.short_leg = None
        self.long_leg = None
        self.last_debug_day = None

        # Entry at 10:00 AM ET
        self.Schedule.On(
            self.DateRules.EveryDay(self.spx),
            self.TimeRules.At(10, 0),
            self.TryEnterSpread
        )

        # Check profit/stop-loss every minute after entry
        self.Schedule.On(
            self.DateRules.EveryDay(self.spx),
            self.TimeRules.Every(timedelta(minutes=1)),   # Fixed: use timedelta
            self.ManagePosition
        )

        # Forced exit near close
        self.Schedule.On(
            self.DateRules.EveryDay(self.spx),
            self.TimeRules.At(15, 55),
            self.ExitPositions
        )

    def OnData(self, slice: Slice):
        if self.spread_active or self.Portfolio.Invested:
            return

        # VIX Filter
        if self.vix in slice.Bars:
            vix_price = slice.Bars[self.vix].Close
            if vix_price > 25:
                if self.Time.hour == 9 and self.Time.minute == 31:
                    self.Debug(f"Skipping day - VIX too high: {vix_price:.2f}")
                return

        if self.option_symbol not in slice.OptionChains:
            return

        chain = slice.OptionChains[self.option_symbol]
        underlying_price = chain.Underlying.Price
        if underlying_price <= 0:
            return

        # Log once per day at open
        today = self.Time.date()
        if today != self.last_debug_day and self.Time.hour == 9 and self.Time.minute == 31:
            self.Debug(f"=== NEW TRADING DAY: {today} | SPX {underlying_price:.2f} | VIX {vix_price if 'vix_price' in locals() else 'N/A':.2f} ===")
            self.last_debug_day = today

        if self.Time.hour >= 13:   # Too late in the day
            return

        # Filter 0DTE puts
        today_date = self.Time.date()
        puts = [c for c in chain if c.Right == OptionRight.Put and c.Expiry.date() == today_date]
        if len(puts) < 20:
            return

        puts.sort(key=lambda x: x.Strike)

        # Delta-based selection: Short put closest to -0.15 delta
        target_delta = -0.15
        short_put = min(puts, key=lambda x: abs(getattr(x.Greeks, 'Delta', 0) - target_delta) 
                       if hasattr(x.Greeks, 'Delta') and x.Greeks.Delta is not None else 999)

        # Long put: 25 points lower
        target_long = short_put.Strike - 25
        long_put_candidates = [p for p in puts if p.Strike <= target_long + 2]
        if not long_put_candidates:
            return
        long_put = max(long_put_candidates, key=lambda x: x.Strike)

        if abs(short_put.Strike - long_put.Strike) not in (20, 25, 30):
            return

        # Quote validation
        short_bid = short_put.BidPrice
        short_ask = short_put.AskPrice
        long_bid = long_put.BidPrice
        long_ask = long_put.AskPrice

        if short_bid <= 0 or short_ask <= 0 or long_bid <= 0 or long_ask <= 0:
            return

        mid_short = (short_bid + short_ask) / 2
        mid_long = (long_bid + long_ask) / 2
        net_credit = mid_short - mid_long

        if net_credit < 0.02:
            return

        # Enter the spread
        self.Debug(f"ENTERING Bull Put Spread | Short {short_put.Strike} (Delta {getattr(short_put.Greeks, 'Delta', 0):.3f}) | "
                   f"Long {long_put.Strike} | SPX {underlying_price:.2f} | Credit {net_credit:.2f}")

        self.Sell(short_put.Symbol, 1)
        self.Buy(long_put.Symbol, 1)

        self.short_leg = short_put.Symbol
        self.long_leg = long_put.Symbol
        self.entry_credit = net_credit
        self.spread_active = True

    def ManagePosition(self):
        if not self.spread_active or not self.Portfolio.Invested:
            return

        # Current P/L calculation (in dollars per spread)
        short_value = self.Portfolio[self.short_leg].HoldingsValue if self.short_leg and self.short_leg in self.Portfolio else 0
        long_value = self.Portfolio[self.long_leg].HoldingsValue if self.long_leg and self.long_leg in self.Portfolio else 0
        current_pnl = (short_value + long_value) / 100.0

        max_profit = self.entry_credit * 100
        profit_target = 0.5 * max_profit          # 50% profit target
        stop_loss = -2.0 * max_profit             # 2x credit stop loss

        if current_pnl >= profit_target:
            self.Debug(f"Profit target hit ({current_pnl:.0f}) - Closing spread")
            self.ExitPositions()
        elif current_pnl <= stop_loss:
            self.Debug(f"Stop-loss hit ({current_pnl:.0f}) - Closing spread")
            self.ExitPositions()

    def TryEnterSpread(self):
        pass

    def ExitPositions(self):
        if self.Portfolio.Invested:
            self.Liquidate()
            self.spread_active = False
            self.short_leg = None
            self.long_leg = None
            self.entry_credit = 0.0
            self.Debug(f"Exited positions at {self.Time}")

    def OnOrderEvent(self, order_event):
        if order_event.Status == OrderStatus.Filled:
            self.Debug(f"Filled: {order_event.Symbol} | Qty: {order_event.Quantity} @ {order_event.FillPrice}")