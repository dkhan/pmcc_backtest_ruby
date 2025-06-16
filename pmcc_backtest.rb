require 'csv'
require_relative './lib/option_math'

include OptionMath

# === CONFIG ===
DAYS_TO_LONG = 60
DAYS_TO_SHORT = 45
RISK_FREE_RATE = 0.01
IV = 0.20
START_DATE = Date.new(2010, 1, 1)

# Load SPY historical data
data = CSV.read("data/spy_daily_full.csv", headers: true, header_converters: :symbol)
prices = data.map { |row| [Date.parse(row[:date]), row[:adj_close].to_f] }.to_h
sorted_dates = prices.keys.sort

results = []

sorted_dates.each_with_index do |entry_date, i|
  next unless entry_date >= START_DATE
  exit_date = entry_date + DAYS_TO_SHORT
  next unless prices[entry_date] && prices[exit_date]

  s = prices[entry_date]
  t_long = DAYS_TO_LONG / 365.0
  t_short = DAYS_TO_SHORT / 365.0

  # Choose strike prices for delta ~ 0.60 long and ~ 0.30 short
  k_long = (s * 0.95).round
  k_short = (s * 1.03).round

  long_price = OptionMath.black_scholes_call(s, k_long, t_long, RISK_FREE_RATE, IV)
  short_price = OptionMath.black_scholes_call(s, k_short, t_short, RISK_FREE_RATE, IV)

  s_exit = prices[exit_date]
  t_long_left = (DAYS_TO_LONG - DAYS_TO_SHORT) / 365.0

  long_close = OptionMath.black_scholes_call(s_exit, k_long, t_long_left, RISK_FREE_RATE, IV)
  short_close = OptionMath.black_scholes_call(s_exit, k_short, 0, RISK_FREE_RATE, IV)

  total_pnl = (long_close - long_price) + (short_price - short_close)
  debit_paid = long_price - short_price
  roi = total_pnl / debit_paid

  results << {
    date: entry_date,
    spy_price: s.round(2),
    long_strike: k_long,
    short_strike: k_short,
    debit: debit_paid.round(2),
    pnl: total_pnl.round(2),
    roi: roi.round(2),
    win: total_pnl > 0
  }
end

# Save results to CSV
CSV.open("results/trades.csv", "w") do |csv|
  csv << %w[date spy_price long_strike short_strike debit pnl roi win]
  results.each do |row|
    csv << row.values
  end
end

puts "Backtest complete. Results saved to results/trades.csv."

# Step 3: Create `analyze_results.rb`
require 'csv'

file_path = 'results/trades.csv'
data = CSV.read(file_path, headers: true, header_converters: :symbol)

pnl = data.map { |r| r[:pnl].to_f }
roi = data.map { |r| r[:roi].to_f }
wins = data.map { |r| r[:win] == 'true' }

puts "\n=== PMCC Strategy Summary ==="
puts "Total Trades: #{pnl.size}"
puts "Win Rate: #{(wins.count(true) * 100.0 / pnl.size).round(1)}%"
puts "Avg PnL: $#{(pnl.sum / pnl.size).round(2)}"
puts "Avg ROI: #{(roi.sum / roi.size).round(2)}"
puts "Median ROI: #{roi.sort[roi.size / 2]}"
puts "==============================\n"

