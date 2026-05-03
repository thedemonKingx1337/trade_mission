# Market Regime Classification Rules

## How to Classify the Day (at 9:15–9:25 AM)

### Trending Day → Use Momentum Strategy
Conditions (need 3 of 4):
- Nifty 50 gapped up or down by > 0.5% from yesterday's close
- VIX is between 12 and 18 (moderate volatility, trending conditions)
- More than 5 Nifty 50 stocks have gapped up by > 1.5% with high volume
- Nifty Futures premium is positive (institutional buying overnight)

Momentum score boost: +0.3 if all 4 conditions met

### Mean Reversion Day → Use Mean Reversion Strategy
Conditions (need 3 of 4):
- Yesterday Nifty closed down > 1% (prior-day selling creates today's oversold bounce)
- Overnight futures are flat to slightly positive (panic sold, now stabilising)
- VIX is elevated (14–20 range) — fear has priced in the down move
- More than 5 Nifty 50 stocks have RSI(14) below 35 on the 15-min chart

Mean reversion score boost: +0.3 if all 4 conditions met

### Range / Sideways Day → Use Range Trading Strategy
Conditions (need 3 of 4):
- VIX is below 13 (low volatility, no strong directional bias)
- Yesterday Nifty was an inside bar (today's high < yesterday's high, low > yesterday's low)
- Nifty gapped less than 0.3% in either direction
- Average ATR of Nifty 50 stocks is below their 20-day average ATR

Range score boost: +0.3 if all 4 conditions met

## Avoid Trading Entirely (VIX Panic Regime)
- If VIX > 25: do not place any new trades for the day
- Log "PANIC_REGIME — trading skipped" to daily_capital notes
- Rationale: in panic regimes (Budget day, election results, global shock), all strategies fail
  because correlations break down and spreads widen massively

## Scoring Algorithm
Each strategy starts at a base score of 0.0.
Apply the following adjustments:

Momentum adjustments:
  +0.20 per gap-up stock > 1.5% (capped at +0.40 for 2+ stocks)
  +0.10 if average volume ratio > 2.0x
  +0.10 if Nifty gap > 0.5%
  +0.10 if VIX between 12–18
  -0.15 if VIX > 20
  -0.20 if Nifty gap < 0.2% (no directional bias)

Mean reversion adjustments:
  +0.20 if more than 5 stocks have RSI < 35
  +0.20 if prior day Nifty down > 1%
  +0.10 if VIX between 14–20
  -0.30 if VIX > 20 (trending, not reverting)
  -0.20 if prior day Nifty was up (no oversold setup)

Range trading adjustments:
  +0.30 if VIX < 13
  +0.20 if Nifty gap < 0.3%
  +0.10 if prior day was inside bar
  -0.30 if VIX > 18 (too volatile for range play)
  -0.20 if more than 5 gap-up stocks (trending day, not range)

Winner = strategy with highest final score (minimum score 0.3 required to trade at all)
If all scores < 0.3: skip trading for the day, log reason.

## Event-Driven Regime Override (from market_events.json)

When high-impact events are detected, apply these overrides regardless of scores:

- **Election result day**: Market will gap violently in either direction. Skip the first
  30 minutes completely. If market gaps down > 2%, consider mean reversion after 10:00 AM
  — the panic dip often recovers. If market gaps up big, ride momentum but with 0.75x ATR SL.

- **Union Budget day**: Speech starts ~11 AM. Skip all trading before 12:00 PM IST.
  Markets are completely unpredictable during the speech. After budget, if market settles
  by 12:30 PM, range trading may work. If a clear direction emerges, momentum.

- **RBI MPC Policy day**: Decision announced at 10:00 AM. Avoid ALL banking and NBFC
  stocks (HDFCBANK, ICICIBANK, SBIN, KOTAKBANK, AXISBANK, BAJFINANCE) before 10:15 AM.
  After announcement: if rate cut → bullish momentum in banks. If rate hold → neutral.
  If rate hike → sharp sell-off, skip trading entirely.

- **Monthly F&O Expiry (last Thursday)**: Very high gamma risk after 2 PM. Range trading
  is DANGEROUS — gamma can break any range. Momentum may work if a clear trend forms.
  Use 0.75x ATR for SL (tighter than normal). Consider stopping entries after 1 PM.

- **Quarterly earnings season**: If a specific Nifty 50 stock has results today, avoid
  trading that stock — the post-result move is unpredictable. But the next day, strong
  results = momentum candidate, weak results = potential mean reversion.

## FII/DII Flow Interpretation

FII (Foreign Institutional Investor) flows drive 60-70% of Indian market direction:

- **FII net buying > 500 crore**: Strongly bullish — momentum strategy favoured
- **FII net buying 0-500 crore**: Mildly bullish — normal strategy selection
- **FII net selling 0-500 crore**: Mildly bearish — prefer defensive mean reversion
- **FII net selling > 500 crore**: Strongly bearish — consider skipping or defensive only
- **FII selling 3+ consecutive days**: Bear trend — skip momentum, only mean reversion
- **DII buying while FII selling**: Market support likely — mean reversion works well

