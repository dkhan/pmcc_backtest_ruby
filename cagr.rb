require 'date'

# Function to calculate CAGR
def calculate_cagr(percent_return, start_date, end_date)
  # Convert percentage return to growth factor (e.g., 1856.61% -> 19.5661)
  growth_factor = 1 + (percent_return / 100.0)

  # Parse the start and end dates
  start_date = Date.parse(start_date)
  end_date = Date.parse(end_date)

  # Calculate the number of days
  days = (end_date - start_date).to_i

  # Convert days to years (using 365 days per year for simplicity)
  years = days / 365.0

  # Calculate CAGR
  cagr = (growth_factor ** (1.0 / years)) - 1

  # Convert to percentage and round to 2 decimal places
  (cagr * 100).round(2)
end

# Example usage
begin
  returns = [
    1856.61, # 1 contract(s), delta: 21/7, DTE: 28
    1894.27, # 1 contract(s), delta: 21/7, DTE: 21
    2034.03, # 1 contract(s), delta: 21/7, DTE: 42
    2117.74, # 1 contract(s), delta: 21/7, DTE: 35
    2185.43, # 3 contract(s), delta: 21/7, DTE: 42
    2204.77, # 7 contract(s), delta: 21/7, DTE: 35, capital: 10,031
    2209.12, # 10 contract(s), delta: 21/7, DTE: 35, capital: 14,330
    2290.59, # 10 contract(s), delta: 21/7, DTE: 35, capital: 12,640, Max VIX on Exit: 28, Max drawdown: -32.34%, -27,102$ on 1/29/2019
    2374.95, # 10 contract(s), delta: 21/7, DTE: 35, capital: 12,640, Max VIX on Enter & Exit: 28, Max drawdown: -31.57%, -27,102$ on 1/29/2019
    2383.05, # 10 contract(s), delta: 21/7, DTE: 35, capital: 12,330, Max VIX on Enter & Exit: 28, Stop loss: at 98% of premium, Max drawdown: -34.49%, -26,929$ on 1/29/2019
    2407.06, # 10 contract(s), delta: 21/7, DTE: 35, capital: 12,330, Max VIX on Enter & Exit: 28, Stop loss: at 84% of premium, Max drawdown: -37.4%, -24,779$ on 1/29/2019
  ]
  start_date = "2013-01-02"
  end_date = "2025-06-18"

  returns.each do |return_percent|
    cagr = calculate_cagr(return_percent, start_date, end_date)
    puts "CAGR for #{return_percent}% return from #{start_date} to #{end_date}: #{cagr}%"
  end
rescue ArgumentError => e
  puts "Error: Invalid date format. Please use YYYY-MM-DD."
rescue StandardError => e
  puts "Error: #{e.message}"
end

# QQQ
# CAGR for 1856.61% return from 2013-01-02 to 2025-06-18: 26.94%
# CAGR for 1894.27% return from 2013-01-02 to 2025-06-18: 27.14%
# CAGR for 2034.03% return from 2013-01-02 to 2025-06-18: 27.83%
# CAGR for 2117.74% return from 2013-01-02 to 2025-06-18: 28.22%
# CAGR for 2185.43% return from 2013-01-02 to 2025-06-18: 28.53%
# CAGR for 2204.77% return from 2013-01-02 to 2025-06-18: 28.62%
# CAGR for 2209.12% return from 2013-01-02 to 2025-06-18: 28.64%
# CAGR for 2290.59% return from 2013-01-02 to 2025-06-18: 29.0%
# CAGR for 2374.95% return from 2013-01-02 to 2025-06-18: 29.36%
# CAGR for 2383.05% return from 2013-01-02 to 2025-06-18: 29.39%
# CAGR for 2407.06% return from 2013-01-02 to 2025-06-18: 29.49%

