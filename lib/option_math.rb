module OptionMath
  include Math

  def self.norm_cdf(x)
    0.5 * (1.0 + Math.erf(x / Math.sqrt(2)))
  end

  def self.black_scholes_call(s, k, t, r, sigma)
    return 0.0 if t <= 0
    d1 = (Math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * Math.sqrt(t))
    d2 = d1 - sigma * Math.sqrt(t)
    c = s * norm_cdf(d1) - k * Math.exp(-r * t) * norm_cdf(d2)
    c.round(2)
  end

  def self.delta_call(s, k, t, r, sigma)
    return 0.0 if t <= 0
    d1 = (Math.log(s / k) + (r + 0.5 * sigma**2) * t) / (sigma * Math.sqrt(t))
    norm_cdf(d1).round(2)
  end
end

