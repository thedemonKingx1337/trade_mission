# Risk Management Rules

## Position Sizing
- Risk only 25% of daily capital per trade (aggressive but survivable)
- In recovery mode (yesterday was a loss): risk 30%, take only the top-1 highest-scoring signal
- Formula: risk_amount = daily_capital * risk_pct
- Quantity = floor(risk_amount / (entry_price - stop_loss_price))
- Cap: quantity * entry_price must not exceed daily_capital * MIS_LEVERAGE (3x)
- Minimum quantity = 1. If calculated quantity < 1, skip the trade.

## Stop-Loss Rules
- Always set stop-loss at entry_price - (1.0 * ATR14)
- Never move stop-loss downward (only trail upward)
- Use SL-Market orders (not SL-Limit) to guarantee fill even on gaps
- If stock gaps through SL at open, accept the fill — do not average down

## Target Rules
- Minimum target = entry_price + (2.0 * ATR14), giving 2:1 reward:risk ratio
- Use LIMIT orders for targets (better execution price)
- Once price reaches +1 ATR profit, trail stop to breakeven (entry price)
- Once price reaches +1.5 ATR profit, trail stop to entry + 0.5 ATR

## Kill-Switch (Daily Loss Limit)
- If (realized_pnl + unrealized_pnl) < -(daily_opening_capital * 0.04):
  - Immediately cancel all open orders
  - Close all open positions at market
  - Stop all new entries for the rest of the day
  - Log event as KILL_SWITCH in capital_log

## Profit Lock (Daily Gain Cap)
- If (realized_pnl + unrealized_pnl) > (daily_opening_capital * 0.08):
  - Close all open positions at market
  - Cancel all pending orders
  - Stop all new entries for the rest of the day
  - Log event as PROFIT_LOCK in capital_log
  - Rationale: lock in the gain, don't give it back to the market

## Recovery Mode Rules
- Triggered when yesterday's closing_capital < yesterday's opening_capital
- In recovery mode:
  - Take only 1 trade (highest-confidence signal only)
  - Increase risk_pct to 0.30 (from 0.25)
  - Require signal score > 0.75 (higher bar than normal 0.50)
  - Use the same SL/Target rules as normal
- Recovery mode deactivates once today ends with a profit

## Compounding Rules
- Every day at EOD: new_capital = old_capital + realized_pnl
- Monday of week 1: seed with Rs1000
- Monday of subsequent weeks: use compounded balance (do not reset to Rs1000)
- Never withdraw capital from the trading account mid-week
