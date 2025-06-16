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
