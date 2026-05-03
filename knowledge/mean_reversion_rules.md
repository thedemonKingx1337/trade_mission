# Mean Reversion / RSI Bounce Strategy Rules

## Core Concept
Strong stocks that have been temporarily pushed to oversold levels by short-term selling tend to bounce back toward their mean (EMA-50 or VWAP). We buy the oversold dip when a reversal candle confirms the bounce has started.

## Entry Conditions (ALL must be satisfied)
1. RSI(14) on 15-minute chart <= 32 (oversold territory)
2. Price is within 1% of the EMA-50 on the 15-minute chart (structural support)
3. Last completed 15-minute candle shows a reversal pattern: hammer, bullish engulfing, or morning doji star
4. VIX must be <= 20 (do not use mean reversion on high-volatility trend days)
5. Prior day Nifty close should be flat or mildly negative (oversold condition more reliable)
6. Stock must be in Nifty 50 (quality filter — weaker stocks may not bounce)

## Reversal Candle Definitions
- Hammer: lower shadow >= 2x body size, body in upper 1/3 of candle range, close >= open
- Bullish Engulfing: current candle's body fully engulfs previous candle's body, current close > current open
- Doji near support: open ≈ close (within 0.1%), long lower shadow — indecision that resolves upward

## Signal Scoring Weights (for strategy selector)
- Number of stocks with RSI < 32: more oversold stocks = better mean reversion day
- Prior day Nifty performance: bigger prior-day fall = stronger bounce potential (+0.2 if Nifty was down >1%)
- VIX above 14 but below 20: adds +0.1 (some fear, but not panic)
- VIX > 20: subtracts 0.3 (strong trends override mean reversion)
- Futures premium negative (spot > futures): adds +0.1 (bearish bias = oversold = bounce setup)

## Stop-Loss Placement
- Stop = low of the reversal candle - 0.2% buffer
- This is tight — if the reversal candle's low breaks, the bounce has failed

## Target Placement
- Target = EMA-21 on 15-minute chart (the mean we're reverting to)
- If EMA-21 is less than entry + 1x ATR, reject the signal (not enough reward)

## Time Rules
- Signals valid from 9:30 AM onwards (need at least 2 completed 15-min candles)
- Last entry: 10:15 AM
- Mean reversion works best in the first 90 minutes when intraday overselling peaks

## What Kills a Mean Reversion Trade
- Price breaks below the reversal candle low — exit immediately, the bounce has failed
- RSI continues falling below 25 — deeper oversold means a trend, not a bounce
- News-driven selling (check if there's a negative headline on the stock before entering)
