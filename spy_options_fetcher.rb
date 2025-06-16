# spy_options_fetcher.rb
require 'httparty'
require 'json'
require 'csv'
require 'date'

API_KEY = '684f5b836d15e5.71599183'
BASE_URL = 'https://eodhistoricaldata.com/api/options'
SYMBOL = 'SPY.US'
FROM_DATE = '2023-06-01'
TO_DATE = '2023-06-30'
OUTPUT_DIR = 'data/options/'

Dir.mkdir(OUTPUT_DIR) unless Dir.exist?(OUTPUT_DIR)

def fetch_options_for_date(date)
  url = "#{BASE_URL}/#{SYMBOL}?api_token=#{API_KEY}&from=#{date}&to=#{date}&fmt=json"
  response = HTTParty.get(url)
  if response.success?
    data = JSON.parse(response.body)
    File.write("#{OUTPUT_DIR}spy_options_#{date}.json", JSON.pretty_generate(data))
    puts "Saved options chain for #{date}"
  else
    puts "Failed to fetch data for #{date}: #{response.code}"
  end
end

# Fetch data for each Friday in date range
(Date.parse(FROM_DATE)..Date.parse(TO_DATE)).each do |date|
  next unless date.friday?
  fetch_options_for_date(date)
  sleep 1  # avoid hitting API limits
end

