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

