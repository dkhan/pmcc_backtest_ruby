# QuantConnect / Lean
# QQQ Low-Delta Bull Call Spread with Time-Based Exits
from AlgorithmImports import *

class QQQLowDeltaBullCallSpreadWithROIClose(QCAlgorithm):

    def Initialize(self):
        # --- Backtest window & cash ---
        self.SetStartDate(2022, 12, 23)
        self.SetEndDate(2025, 7, 18)
        self.SetCash(100000)

        # --- Params ---
        self.ticker = "QQQ"
        self.contracts_to_trade = 1

        self.target_long_delta  = 0.21
        self.target_short_delta = 0.07
        self.long_delta_range   = (0.19, 0.22)
        self.short_delta_range  = (0.06, 0.08)
        self.min_dte, self.max_dte = 28, 42

        # ROI thresholds (spread value vs. entry debit, i.e., multiples)
        self.take_profit_multiple = 5.20   # 1 + 4.20 → +420% profit
        self.stop_loss_multiple   = 0.16   # keep <=16% of debit → ~-84% loss

        # --- Data (use Minute so bid/ask are populated) ---
        equity = self.AddEquity(self.ticker, Resolution.Minute)
        equity.SetDataNormalizationMode(DataNormalizationMode.Adjusted)
        self.underlying = equity.Symbol

        opt = self.AddOption(self.ticker, Resolution.Minute)
        opt.SetFilter(self.OptionFilter)
        opt.SetDataNormalizationMode(DataNormalizationMode.Raw)
        self.option_symbol = opt.Symbol

        # --- State ---
        self.positions = []        # list[dict]
        self.last_entry_date = None

        # --- Schedules ---
        # New trade: every Friday at 15:30 ET
        self.Schedule.On(self.DateRules.Every(DayOfWeek.Friday),
                         self.TimeRules.At(15, 30),
                         self.TryEnter)

        # Exit checks: every trading day at 15:45 ET
        self.Schedule.On(self.DateRules.EveryDay(self.ticker),
                         self.TimeRules.At(15, 45),
                         self.CheckExits)

        self.Debug("Initialized")

    # -------------------- Filters --------------------
    def OptionFilter(self, universe):
        # 28–42 DTE window, allow weeklys, reasonable strikes
        return universe.IncludeWeeklys().Strikes(-60, +60).Expiration(self.min_dte, self.max_dte)

    # -------------------- Helpers --------------------
    def Mid(self, symbol):
        if not self.Securities.ContainsKey(symbol):
            return None
        sec = self.Securities[symbol]
        b, a = sec.BidPrice, sec.AskPrice
        if b > 0 and a > 0:
            return 0.5 * (b + a)
        px = sec.Price
        return px if px > 0 else None

    def SpreadValueNow(self, long_sym, short_sym):
        """Current market value of the vertical (positive number)."""
        long_bid  = self.Securities[long_sym].BidPrice
        short_ask = self.Securities[short_sym].AskPrice
        if long_bid <= 0 or short_ask <= 0:
            # fall back to mids if either side is missing
            long_mid = self.Mid(long_sym)
            short_mid = self.Mid(short_sym)
            if long_mid is None or short_mid is None:
                return None
            return max(0.0, long_mid - short_mid)
        return max(0.0, long_bid - short_ask)

    def PickContracts(self, chain):
        """Pick ~21Δ long and ~7Δ short (same expiry)."""
        calls = [c for c in chain if c.Right == OptionRight.Call and c.Expiry.date() > self.Time.date()]
        if not calls:
            return None, None

        # Group by expiry in desired DTE range
        def dte(c): return (c.Expiry.date() - self.Time.date()).days
        expiries = sorted({c.Expiry for c in calls if self.min_dte <= dte(c) <= self.max_dte},
                          key=lambda e: abs((e.date() - self.Time.date()).days - 35))
        for exp in expiries:
            exp_calls = [c for c in calls if c.Expiry == exp and c.Greeks is not None]
            # Long candidates around 0.19–0.22 delta
            long_cands = [c for c in exp_calls
                          if c.Greeks is not None and c.Greeks.Delta is not None
                          and self.long_delta_range[0] <= c.Greeks.Delta <= self.long_delta_range[1]]
            if not long_cands:
                continue
            long_cands.sort(key=lambda c: abs(c.Greeks.Delta - self.target_long_delta))
            long_call = long_cands[0]

            # Short candidates (higher strike, ~0.06–0.08 delta)
            short_cands = [c for c in exp_calls
                           if c.Strike > long_call.Strike
                           and c.Greeks is not None and c.Greeks.Delta is not None
                           and self.short_delta_range[0] <= c.Greeks.Delta <= self.short_delta_range[1]]
            if not short_cands:
                continue
            short_cands.sort(key=lambda c: abs(c.Greeks.Delta - self.target_short_delta))
            short_call = short_cands[0]
            return long_call, short_call
        return None, None

    # -------------------- Entry --------------------
    def TryEnter(self):
        # one trade per Friday
        if self.last_entry_date == self.Time.date():
            return

        chain = self.CurrentSlice.OptionChains.get(self.option_symbol)
        if chain is None or len(chain) == 0:
            self.Log("No option chain at entry time.")
            return

        long_call, short_call = self.PickContracts(chain)
        if long_call is None or short_call is None:
            self.Log("No suitable contracts near target deltas.")
            return

        # Entry debit = Long Ask − Short Bid (positive)
        long_ask  = self.Securities[long_call.Symbol].AskPrice
        short_bid = self.Securities[short_call.Symbol].BidPrice
        if long_ask <= 0 or short_bid <= 0:
            self.Log("Missing quotes for entry; skipping.")
            return
        entry_debit = max(0.01, long_ask - short_bid)

        # Place orders (market)
        qty = self.contracts_to_trade
        self.MarketOrder(long_call.Symbol,  qty)
        self.MarketOrder(short_call.Symbol, -qty)

        entry_underlying = self.Securities[self.underlying].Price

        self.positions.append({
            "long": long_call.Symbol,
            "short": short_call.Symbol,
            "entry_date": self.Time.date(),
            "expiry": long_call.Expiry.date(),
            "entry_debit": entry_debit,
            "entry_underlying": entry_underlying,
            "exit_value": None,
            "exit_underlying": None,
            "exit_date": None,
            "close_reason": None,
            "closed": False
        })
        self.last_entry_date = self.Time.date()

        self.Debug(f"{self.Time.date()} | PX {entry_underlying:.2f} | BCS: Buy {long_call.Strike}C Δ~{(long_call.Greeks.Delta if long_call.Greeks else float('nan')):.2f} "
                   f"/ Sell {short_call.Strike}C Δ~{(short_call.Greeks.Delta if short_call.Greeks else float('nan')):.2f} "
                   f"Exp {long_call.Expiry.date()} | Debit {entry_debit:.2f}")

    # -------------------- Exits --------------------
    def CheckExits(self):
        to_close = []
        for pos in self.positions:
            if pos["closed"]:
                continue

            long = pos["long"]
            short = pos["short"]
            if not (self.Securities.ContainsKey(long) and self.Securities.ContainsKey(short)):
                continue

            curr_val = self.SpreadValueNow(long, short)
            if curr_val is None:
                continue

            mult = curr_val / max(0.01, pos["entry_debit"])
            # TP: spread value >= 5.2 × debit  (≈ +420% profit)
            if mult >= self.take_profit_multiple:
                pos["close_reason"] = "take profit"
                to_close.append(pos)
                continue
            # SL: spread value <= 0.16 × debit (≈ -84% loss)
            if mult <= self.stop_loss_multiple:
                pos["close_reason"] = "stop loss"
                to_close.append(pos)
                continue
            # Expiration day closeout
            if (pos["expiry"] - self.Time.date()).days <= 0:
                pos["close_reason"] = "expiration"
                to_close.append(pos)

        for pos in to_close:
            self.Liquidate(pos["long"])
            self.Liquidate(pos["short"])
            pos["exit_value"] = self.SpreadValueNow(pos["long"], pos["short"])
            pos["exit_underlying"] = self.Securities[self.underlying].Price
            pos["exit_date"] = self.Time.date()
            pos["closed"] = True

    # -------------------- Slice hook --------------------
    def OnData(self, slice: Slice):
        # We operate via schedules; keep slice for access inside TryEnter
        self.CurrentSlice = slice

    # -------------------- Summary --------------------
    def OnEndOfAlgorithm(self):
        self.Debug("====== TRADE SUMMARY ======")
        self.Debug("OPENED     | CLOSED     | PX OPEN  | PX CLOSE | L/S STRIKES | EXP        | DTE | DEBIT  | VALUE@CLOSE | P/L      | REASON     | MULT")
        for pos in sorted(self.positions, key=lambda x: x["entry_date"]):
            if not pos["closed"]:
                continue
            dte = (pos["expiry"] - pos["entry_date"]).days
            debit = pos["entry_debit"]
            value_close = (pos["exit_value"] if pos["exit_value"] is not None else 0.0)
            pnl = (value_close - debit) * 100  # per spread, $ per contract
            mult = (value_close / debit) if debit > 0 else 0.0

            longK  = pos["long"].ID.StrikePrice
            shortK = pos["short"].ID.StrikePrice

            self.Debug(f"{pos['entry_date']} | {pos['exit_date']} | "
                       f"{pos['entry_underlying']:7.2f} | {pos['exit_underlying']:8.2f} | "
                       f"{longK:.1f}/{shortK:.1f}   | {pos['expiry']} | {dte:3d} | "
                       f"{debit:6.2f} | {value_close:11.2f} | {pnl:8.2f} | {pos['close_reason']:<10} | {mult:4.2f}")