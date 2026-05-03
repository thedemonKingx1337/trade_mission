# Momentum / Opening Range Breakout (ORB) Strategy Rules

## Core Concept
Stocks that gap up significantly at open with high volume tend to continue in the direction of the gap for the first 1-2 hours. We enter only after confirmation (price breaks the Opening Range High) to avoid fading a strong trend.

## Entry Conditions (ALL must be satisfied)
1. Gap-up >= 1.5% from yesterday's close (price at 9:15 AM vs prev_close)
2. First 5-minute candle volume >= 1.8x the 20-day average 5-minute volume
3. Current price has broken ABOVE the first 5-minute candle High (the Opening Range High)
4. Broad market (Nifty 50) is NOT down more than 0.5% at time of signal
5. Do NOT enter if current price is more than 0.5% above the OR High (chasing — too late)
6. Stock must be in Nifty 50 universe (liquidity guarantee)

## Signal Scoring Weights (for strategy selector)
- Number of qualifying gap-up stocks: higher = more suited for momentum day
- Average volume ratio across gap-up stocks: higher = stronger momentum
- Nifty own gap direction: positive gap adds +0.2 to momentum score
- VIX between 12-18: optimal for trending — adds +0.1 to score
- VIX > 20: momentum less reliable — subtract 0.15 from score

## Stop-Loss Placement
- Stop = Opening Range Low (the low of the first 5-minute candle)
- This is the invalidation level — if price returns below OR Low, the breakout has failed

## Target Placement
- Primary target: entry + 2x ATR14
- Secondary target (trail to): previous day's high if it's above entry + 2x ATR

## Time Rules
- Only enter between 9:25 AM and 10:15 AM
- After 10:15 AM: manage existing positions only, no new momentum entries
- Best signals are in the first 30 minutes; quality degrades after 10:00 AM

## What Kills a Momentum Trade
- Nifty reverses sharply after entry — exit if Nifty falls >1% from open
- Stock gives back entire OR High break within 1 candle (false breakout)
- Volume dries up (vol_ratio drops below 1.0x) — consider partial exit
