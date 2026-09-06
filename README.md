# 📈 PMCC Backtest (Ruby)

A backtesting engine for the **Poor Man’s Covered Call (PMCC)** strategy on SPY using Black-Scholes pricing with dynamic VIX-based implied volatility.

This project simulates 15+ years of weekly trades, incorporating bid/ask slippage, realistic commissions, and volatility adjustments using historical VIX data.

---

## 🧰 Features

- Weekly PMCC simulation: Buy 60 DTE call, sell 45 DTE call
- VIX-based IV estimation (adjusted for LEAPS)
- Slippage: ±$0.10 per option leg
- Commission: $2.00 per round-trip trade
- Outputs CSV trade logs and performance summaries

---

## 📁 Project Structure

pmcc_backtest_ruby/  
├── data/  
│   ├── spy_daily_full.csv         (Daily SPY prices)  
│   └── vix_daily.csv              (VIX implied volatility)  
├── lib/  
│   └── option_math.rb             (Black-Scholes model)  
├── results/  
│   └── trades.csv                 (Backtest output)  
├── pmcc_backtest.rb              (Main simulation script)  
└── analyze_results.rb            (CLI summary script)

---

## 🚀 How to Run

1. Clone the repo:

    git clone https://github.com/dkhan/pmcc_backtest_ruby.git  
    cd pmcc_backtest_ruby

2. Run the backtest:

    ruby pmcc_backtest.rb

3. Analyze results:

    ruby analyze_results.rb

---

## 📊 Example Output

=== PMCC Strategy Summary ===  
Total Trades: 2167  
Win Rate: 61.1%  
Avg PnL: $3.33  
Avg ROI: 0.20  
Median ROI: 0.21  
==============================

## Local QQQ 21/7-Delta Call-Spread Backtest

The repository also includes a fast local backtester that reads the downloaded
QQQ option-chain Parquet files directly. It uses historical bid/ask quotes and
vendor-provided deltas instead of estimating option prices.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-local-backtest.txt
.venv/bin/python local_qqq_backtest.py
```

Useful alternatives:

```bash
# Summary only
.venv/bin/python local_qqq_backtest.py --quiet

# Keep idle portfolio cash invested in QQQ
.venv/bin/python local_qqq_backtest.py --hold-qqq

# Enable the historical VIX entry/exit rules
.venv/bin/python local_qqq_backtest.py --use-vix

# Use the earlier percentage-plus-ratchet sizing (fixed 10 is the default)
.venv/bin/python local_qqq_backtest.py --contracts 0 --entry-fraction 0.01

# Override fixed contracts or test dates
.venv/bin/python local_qqq_backtest.py \
  --contracts 5 --start-date 2013-01-02 --end-date 2025-06-30
```

Results are written to `results/qqq_local/trades.csv` and
`results/qqq_local/daily_equity.csv`.

## Monthly Leveraged ETF Rotation Backtest

Download adjusted daily prices for QQQ, TQQQ, TECL, GLD, IEF, and SHY, then
run the independent rotation backtest:

```bash
.venv/bin/python download_rotation_etfs.py

# Deployable test: prior month-end signal, next-session open, 5 bps per side
.venv/bin/python local_rotation_backtest.py \
  --execution next_open --cost-bps 5 \
  --start-date 2016-06-01 --end-date 2026-06-12

# Same-close platform convention, included only for comparison
.venv/bin/python local_rotation_backtest.py \
  --execution same_close --cost-bps 0 \
  --start-date 2016-06-01 --end-date 2026-06-12

# FinLab short-term mean-reversion variant: buy the 3-day laggard, 8% stop
.venv/bin/python local_rotation_backtest.py \
  --execution same_close --risk-selection laggard --laggard-days 3 --stop-loss 0.08 \
  --start-date 2016-06-01 --end-date 2026-06-12 \
  --output-dir results/rotation_laggard_3d
```

The regime is risk-on when adjusted QQQ is above its 200-session average and
its 126-session return is positive. Risk-on holds the stronger of TQQQ and
TECL; risk-off holds the stronger of GLD, IEF, and SHY. Strength is the
63-session percentage return minus the 21-session percentage return. The
portfolio rebalances monthly and uses a 10% touched stop with gap-aware fills.
Pass `--risk-selection laggard --laggard-days 3 --stop-loss 0.08` to reproduce the signal and
stop configuration from FinLab's short-term mean-reversion paper. This selects
the lower trailing three-session return between TQQQ and TECL at each rebalance.

Results are written to `results/rotation/trades.csv` and
`results/rotation/daily_equity.csv`.

## TQQQ Seasonal-MACD Backtest

The Les Masonson/Stock Trader's Almanac seasonal timing idea is available as a
separate local backtest. It watches standard 12/26/9 MACD crossovers on QQQ,
buys TQQQ at the next open after the first bullish crossover on or after
October 1, and exits at the next open after the first bearish crossover on or
after July 1. This is the Nasdaq-specific eight-month version discussed in the
video; pass `--window broad-six` to test the April exit gate. Idle capital
remains in cash.

```bash
.venv/bin/python local_tqqq_seasonal_macd_backtest.py \
  --start-date 2010-02-11 --cost-bps 5
```

Results are written to `results/tqqq_seasonal_macd/trades.csv` and
`results/tqqq_seasonal_macd/daily_equity.csv`.

## VectorVest NETS Local Approximation

Download QLD alongside the existing ETF data and run the public-data NETS
approximation locally:

```bash
.venv/bin/python download_rotation_etfs.py
.venv/bin/python local_vectorvest_nets_backtest.py --strategy turbo
.venv/bin/python local_vectorvest_nets_backtest.py --strategy nitro
.venv/bin/python local_vectorvest_nets_backtest.py --strategy turbo --sweep
.venv/bin/python local_vectorvest_nets_backtest.py --strategy nitro --sweep-target one-year --sweep
```

The local model follows the published QQQ/QLD state transitions and executes
signals at the following session's open. Since VectorVest's Confirmed Calls and
Relative Timing series are proprietary, it substitutes a QQQ EMA regime and
RSI/50. Turbo uses QLD with +30%/-5% exits; Nitro uses TQQQ with +50%/-5%
exits. Results go to a strategy-specific `results/vectorvest_nets_*` folder;
the sweep reports the ten parameter combinations nearest the video's implied
CAGR.

For QuantConnect, create a Python project and paste the contents of
`quantconnect_finlab_rotation.py` into `main.py`. This is a LEAN port of the
published FinLab rules; it uses the prior completed bar and trades after the
next month opens, because the original FinLab SDK's same-close execution
engine is not available on QuantConnect.

The QuantConnect algorithm accepts a `risk-selection` project parameter:

- `strongest` (default): highest 63-day-minus-21-day momentum
- `weakest`: lowest 63-day-minus-21-day momentum
- `alternate`: TQQQ in odd-numbered months and TECL in even-numbered months
- `random`: reproducible random choice between TQQQ and TECL; set `random-seed`
- `laggard`: lowest return over the previous `laggard-days` completed sessions

To test the mean-reversion paper, add `risk-selection` = `laggard` and
`laggard-days` = `3` in the project's Parameters panel. The paper also uses an 8% stop, so set
`STOP_LOSS = 0.08` when reproducing its complete configuration; leave it at
`0.10` for a selection-only comparison against the original strategy.

---

## 📌 Notes

- Prices and deltas use Black-Scholes estimation.
- VIX is used for dynamic IV modeling.
- No margin, early assignment, or tax impact modeled.

---

## 🧠 Next Ideas

- Track equity curve and drawdown
- Add delta-based strike selection
- Compare PMCC with covered call strategy
- Support early roll mechanics

---

## 📜 License

MIT License
