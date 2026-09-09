from AlgorithmImports import *

class ConditionalSectorRotation(QCAlgorithm):
    """Hourly intraday strategy using current-session RSI/SMA metrics.

    The target is checked at 09:45 and then once per hour through 15:45 ET.
    A trade is submitted immediately when the decision tree selects a target
    different from the strategy's current holding.
    """

    CHECK_TIMES = ((9, 45), (10, 45), (11, 45), (12, 45), (13, 45), (14, 45), (15, 45))

    TICKERS = (
        "SPY", "QQQ", "TQQQ", "UVXY", "TECL", "SPXL", "SQQQ", "TECS", "BSV"
    )
    SIGNAL_TICKERS = ("SPY", "QQQ", "TQQQ", "UVXY", "TECL", "SQQQ", "TECS", "BSV")

    def Initialize(self):
        self.SetStartDate(2012, 1, 1)
        self.SetEndDate(2026, 9, 8)
        self.SetCash(100000)

        self.rsi_period = self._integer_parameter("rsi_period", 10, 2)
        self.spy_sma_period = self._integer_parameter("spy_sma_period", 200, 2)
        self.qqq_sma_period = self._integer_parameter("qqq_sma_period", 20, 2)
        self.tqqq_sma_period = self._integer_parameter("tqqq_sma_period", 20, 2)
        self.cash_buffer = self._decimal_parameter("cash_buffer", 0.02, 0.01, 0.25)

        # Leave room for opening gaps, fees, and integer-share rounding.
        self.settings.free_portfolio_value_percentage = self.cash_buffer

        self.symbols = {}
        for ticker in self.TICKERS:
            # Raw minute prices produce real, executable share quantities.
            security = self.AddEquity(
                ticker,
                Resolution.Minute,
                dataNormalizationMode=DataNormalizationMode.Raw,
            )
            self.symbols[ticker] = security.Symbol

        self.SetBenchmark(self.symbols["SPY"])

        self.desired_ticker = None
        self.pending_entry_symbol = None
        self.rebalance_in_progress = False
        self.failed_order_count = 0

        warmup_bars = max(
            self.spy_sma_period,
            self.qqq_sma_period,
            self.tqqq_sma_period,
            self.rsi_period,
        ) + 10
        self.SetWarmUp(warmup_bars, Resolution.Daily)

        # Start at 09:45 to avoid the least stable opening minutes, then check
        # once per hour. DateRules limits callbacks to SPY trading days; the
        # callback also rejects closed and early-close sessions.
        for hour, minute in self.CHECK_TIMES:
            self.Schedule.On(
                self.DateRules.EveryDay(self.symbols["SPY"]),
                self.TimeRules.At(hour, minute),
                self.Rebalance,
            )

    def OnData(self, data):
        # Signal construction and execution happen in the scheduled callback.
        pass

    def Rebalance(self):
        if not self.Securities[self.symbols["SPY"]].Exchange.ExchangeOpen:
            self.Log(
                f"SKIP {self.Time:%Y-%m-%d %H:%M}: SPY market is closed"
            )
            return
        if self.IsWarmingUp or not self._prices_ready():
            return

        if self.rebalance_in_progress or self.Transactions.GetOpenOrders():
            self.Log(f"SKIP {self.Time:%Y-%m-%d}: an earlier rotation is still pending")
            return

        signal = self._signal_snapshot()
        if signal is None:
            return

        target_ticker = self.SelectTarget(signal)
        target_symbol = self.symbols[target_ticker]
        held_symbols = self._held_strategy_symbols()

        if held_symbols == [target_symbol]:
            return

        # Log only actionable changes. Daily HOLD messages previously exhausted
        # QuantConnect's log allowance and hid later execution diagnostics.
        self._log_signal(target_ticker, signal)

        self.desired_ticker = target_ticker
        self.pending_entry_symbol = target_symbol
        self.rebalance_in_progress = True

        positions_to_sell = [symbol for symbol in held_symbols if symbol != target_symbol]
        if not positions_to_sell:
            self._submit_entry_if_ready()
            return

        # Synchronous sell orders return only after LEAN processes the fill, so
        # cash and buying power are current before sizing the replacement.
        for symbol in positions_to_sell:
            quantity = self.Portfolio[symbol].Quantity
            if quantity != 0:
                self.MarketOrder(
                    symbol,
                    -quantity,
                    tag=f"ROTATE SELL -> {target_ticker}",
                )

        if any(symbol != target_symbol for symbol in self._held_strategy_symbols()):
            self.Error(
                f"ROTATION PAUSED: liquidation incomplete; target={target_ticker}"
            )
            self._clear_rotation_state()
            return
        self._submit_entry_if_ready()

    def SelectTarget(self, signal):
        price_spy = signal["SPY_close"]
        price_qqq = signal["QQQ_close"]
        price_tqqq = signal["TQQQ_close"]
        rsi_qqq = signal["QQQ_RSI"]
        rsi_spy = signal["SPY_RSI"]
        rsi_tqqq = signal["TQQQ_RSI"]
        rsi_sqqq = signal["SQQQ_RSI"]
        rsi_uvxy = signal["UVXY_RSI"]

        if price_spy > signal["SPY_SMA"]:
            return "UVXY" if rsi_qqq > 81 or rsi_spy > 80 else "TQQQ"
        if rsi_tqqq < 30:
            return "TECL"
        if rsi_spy < 30:
            return "SPXL"
        if rsi_uvxy > 74:
            if rsi_uvxy <= 84:
                return "UVXY"
            if price_qqq > signal["QQQ_SMA"]:
                return "TECS" if rsi_sqqq < 31 else "TECL"
            return self.GetMaxRsiAsset(("TECS", "BSV"), signal)
        if price_tqqq > signal["TQQQ_SMA"]:
            return "TECS" if rsi_sqqq < 34 else "TECL"
        return self.GetMaxRsiAsset(("TECS", "BSV"), signal)

    def GetMaxRsiAsset(self, ticker_list, signal):
        return max(ticker_list, key=lambda ticker: signal[f"{ticker}_RSI"])

    def OnOrderEvent(self, order_event):
        if order_event.Status in (OrderStatus.Invalid, OrderStatus.Canceled):
            self.failed_order_count += 1
            self.Error(
                f"ORDER FAILURE {order_event.OrderId} {order_event.Symbol}: "
                f"{order_event.Status}; {order_event.Message}"
            )
            self._clear_rotation_state()
            return

        if order_event.Status != OrderStatus.Filled:
            return

        self.Log(
            f"FILL {order_event.Symbol}: quantity={order_event.FillQuantity}, "
            f"price={order_event.FillPrice:.4f}"
        )
        if (
            order_event.FillQuantity > 0
            and order_event.Symbol == self.pending_entry_symbol
        ):
            self.Log(f"ROTATION COMPLETE: holding {self.desired_ticker}")
            self._clear_rotation_state()

    def OnEndOfAlgorithm(self):
        self.Log(f"Completed with {self.failed_order_count} invalid/canceled orders")
        if self.rebalance_in_progress:
            self.Error(f"Unfinished rotation at shutdown; desired target={self.desired_ticker}")

    def _submit_entry_if_ready(self):
        if self.pending_entry_symbol is None or self.desired_ticker is None:
            return
        wrong_holdings = [
            symbol
            for symbol in self._held_strategy_symbols()
            if symbol != self.pending_entry_symbol
        ]
        if wrong_holdings:
            return

        target_weight = self._target_weight(self.desired_ticker)
        quantity = self.CalculateOrderQuantity(self.pending_entry_symbol, target_weight)
        if quantity == 0:
            self.Error(f"ENTRY SKIPPED {self.desired_ticker}: calculated quantity is zero")
            self._clear_rotation_state()
            return
        self.Log(
            f"BUY {self.desired_ticker}: target_weight={target_weight:.0%}, "
            f"quantity={quantity}"
        )
        self.MarketOrder(
            self.pending_entry_symbol,
            quantity,
            tag=f"ROTATE BUY {self.desired_ticker} {target_weight:.0%}",
        )

    def _target_weight(self, ticker):
        defaults = {
            "UVXY": 0.95,
            "TECS": 0.95,
            "BSV": 0.95,
            "TQQQ": 0.95,
            "TECL": 0.95,
            "SPXL": 0.95,
        }
        requested_weight = self._decimal_parameter(
            f"{ticker.lower()}_weight", defaults[ticker], 0.05, 0.95
        )
        # Keep an extra 1% beyond the configured free-cash reserve for fees,
        # price movement between calculation and fill, and whole-share rounding.
        max_executable_weight = 1.0 - self.cash_buffer - 0.01
        return min(requested_weight, max_executable_weight)

    def _prices_ready(self):
        missing = [
            ticker
            for ticker in self.SIGNAL_TICKERS
            if not self.Securities[self.symbols[ticker]].HasData
            or self.Securities[self.symbols[ticker]].Price <= 0
        ]
        if missing:
            self.Log(f"SKIP {self.Time:%Y-%m-%d}: prices not ready: {', '.join(missing)}")
            return False
        return True

    def _signal_snapshot(self):
        bars = max(
            self.spy_sma_period,
            self.qqq_sma_period,
            self.tqqq_sma_period,
            self.rsi_period + 1,
        ) + 6
        symbols = list(self.symbols.values())
        adjusted_history = self.history(
            symbols,
            bars,
            Resolution.DAILY,
            data_normalization_mode=DataNormalizationMode.ADJUSTED,
        )
        adjusted_minute_history = self.history(
            symbols,
            5,
            Resolution.MINUTE,
            data_normalization_mode=DataNormalizationMode.ADJUSTED,
        )
        if adjusted_history.empty or adjusted_minute_history.empty:
            self.Log(
                f"SKIP {self.Time:%Y-%m-%d}: adjusted daily/minute history is empty"
            )
            return None

        closes = {}
        for ticker, symbol in self.symbols.items():
            try:
                adjusted = adjusted_history.loc[symbol]["close"].dropna().copy()
                current_minutes = (
                    adjusted_minute_history.loc[symbol]["close"].dropna()
                )
            except (KeyError, TypeError):
                self.Log(f"SKIP {self.Time:%Y-%m-%d}: no history for {ticker}")
                return None

            # History implementations can differ on whether an incomplete daily
            # bar is exposed intraday.  Explicitly remove today so it is included
            # exactly once using the known 15:45 minute price below.
            adjusted = adjusted[adjusted.index.date < self.Time.date()]
            if adjusted.empty or current_minutes.empty:
                self.Log(
                    f"SKIP {self.Time:%Y-%m-%d}: adjusted prices unavailable for {ticker}"
                )
                return None

            # History is bounded by LEAN's current time frontier. At each hourly
            # callback, use only the latest completed minute available then.
            latest_minute_time = current_minutes.index[-1]
            minute_age = self.Time - latest_minute_time.to_pydatetime()
            if minute_age.total_seconds() < 0:
                self.Error(
                    f"LOOK-AHEAD GUARD {ticker}: latest history timestamp "
                    f"{latest_minute_time} exceeds algorithm time {self.Time}"
                )
                return None
            if minute_age > timedelta(minutes=2):
                self.Log(
                    f"SKIP {self.Time:%Y-%m-%d %H:%M}: stale minute price for "
                    f"{ticker}; latest={latest_minute_time}"
                )
                return None
            adjusted_today = float(current_minutes.iloc[-1])
            if adjusted_today <= 0:
                self.Log(
                    f"SKIP {self.Time:%Y-%m-%d}: invalid adjusted price for "
                    f"{ticker}: {adjusted_today}"
                )
                return None

            # Append today's provisional observation at this check time on the
            # same corporate-action-adjusted scale as the daily history.
            adjusted.loc[self.Time] = adjusted_today

            required = max(
                self.rsi_period + 1,
                self.spy_sma_period if ticker == "SPY" else 0,
                self.qqq_sma_period if ticker == "QQQ" else 0,
                self.tqqq_sma_period if ticker == "TQQQ" else 0,
            )
            if len(adjusted) < required:
                self.Log(
                    f"SKIP {self.Time:%Y-%m-%d}: {ticker} has "
                    f"{len(adjusted)}/{required} daily observations"
                )
                return None
            closes[ticker] = adjusted

        signal = {
            f"{ticker}_RSI": self._wilder_rsi(series, self.rsi_period)
            for ticker, series in closes.items()
        }
        signal.update(
            {
                "SPY_close": float(closes["SPY"].iloc[-1]),
                "QQQ_close": float(closes["QQQ"].iloc[-1]),
                "TQQQ_close": float(closes["TQQQ"].iloc[-1]),
                "SPY_SMA": float(
                    closes["SPY"].iloc[-self.spy_sma_period :].mean()
                ),
                "QQQ_SMA": float(
                    closes["QQQ"].iloc[-self.qqq_sma_period :].mean()
                ),
                "TQQQ_SMA": float(
                    closes["TQQQ"].iloc[-self.tqqq_sma_period :].mean()
                ),
            }
        )
        return signal

    @staticmethod
    def _wilder_rsi(close, period):
        changes = close.diff().dropna()
        gains = changes.clip(lower=0.0)
        losses = -changes.clip(upper=0.0)
        average_gain = float(gains.iloc[:period].mean())
        average_loss = float(losses.iloc[:period].mean())
        for index in range(period, len(changes)):
            average_gain = (
                average_gain * (period - 1) + float(gains.iloc[index])
            ) / period
            average_loss = (
                average_loss * (period - 1) + float(losses.iloc[index])
            ) / period
        if average_loss == 0:
            return 100.0 if average_gain > 0 else 50.0
        relative_strength = average_gain / average_loss
        return 100.0 - 100.0 / (1.0 + relative_strength)

    def _held_strategy_symbols(self):
        non_traded = ("SPY", "QQQ", "SQQQ")
        return [
            symbol
            for ticker, symbol in self.symbols.items()
            if ticker not in non_traded and self.Portfolio[symbol].Invested
        ]

    def _log_signal(self, target_ticker, signal):
        fields = [
            f"SIGNAL {self.Time:%Y-%m-%d}",
            f"target={target_ticker}",
            f"SPY={signal['SPY_close']:.4f}",
            f"SPY_SMA={signal['SPY_SMA']:.4f}",
            f"QQQ={signal['QQQ_close']:.4f}",
            f"QQQ_SMA={signal['QQQ_SMA']:.4f}",
            f"TQQQ={signal['TQQQ_close']:.4f}",
            f"TQQQ_SMA={signal['TQQQ_SMA']:.4f}",
            f"QQQ_RSI={signal['QQQ_RSI']:.2f}",
            f"SPY_RSI={signal['SPY_RSI']:.2f}",
            f"TQQQ_RSI={signal['TQQQ_RSI']:.2f}",
            f"SQQQ_RSI={signal['SQQQ_RSI']:.2f}",
            f"UVXY_RSI={signal['UVXY_RSI']:.2f}",
            f"TECS_RSI={signal['TECS_RSI']:.2f}",
            f"BSV_RSI={signal['BSV_RSI']:.2f}",
        ]
        self.Log(" | ".join(fields))

    def _clear_rotation_state(self):
        self.desired_ticker = None
        self.pending_entry_symbol = None
        self.rebalance_in_progress = False

    def _integer_parameter(self, name, default, minimum):
        value = int(self.GetParameter(name) or default)
        if value < minimum:
            raise ValueError(f"{name} must be at least {minimum}")
        return value

    def _decimal_parameter(self, name, default, minimum, maximum):
        raw = self.GetParameter(name)
        value = float(raw) if raw not in (None, "") else float(default)
        if not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return value
