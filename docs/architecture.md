# Architecture Decisions & Rationale — Trade Mission

> Back to: [AGENTS.md](../AGENTS.md)

---

## Why intraday-only (MIS)?

With ₹1000 capital, positional trades produce tiny position sizes — often less than 1 share of most Nifty 50 stocks. MIS (Margin Intraday Square-off) leverage of 3–5x allows the bot to trade meaningfully sized lots. All risk is bounded within a single session — no overnight gap risk, no margin call from price moves while the laptop is closed.

---

## Why three strategies instead of one?

Indian market character changes day to day. A single strategy applied every day loses heavily on days it doesn't suit:

| Market type | Best strategy | Why |
|---|---|---|
| Trending (events, global cues, earnings) | Momentum / ORB | Direction is established early, follow-through is strong |
| Flat, oversold (post-sell-off bounce) | Mean reversion | Stocks that fell hard yesterday tend to recover intraday |
| Dull, low-VIX, directionless | Range trading | Known range gives clear entry/exit with high R:R |
| Panic (VIX > 25) | Skip entirely | No edge — random intraday moves dominate all patterns |

The `selector.py` scores all three each morning using live VIX, Nifty gap, RSI breadth, and pre-market context, then locks the day's strategy at 09:15.

---

## Why Opening Range Breakout (ORB) for momentum?

ORB is the most battle-tested intraday pattern in Indian markets. The reasoning:

- The first 5 minutes concentrate overnight order flow — mutual fund buys, FII activity, retail stops. The resulting candle defines the day's reference range.
- A gap-up stock that also breaks above its OR High with volume has two confluent signals: overnight buying pressure and intraday continuation.
- The entry rule is mechanical: `OR High + 0.1% buffer`. No discretion. No pattern interpretation.
- The stop is also mechanical: `OR Low`. The range defines the trade.

Momentum is suppressed when Nifty itself is down >0.5% at signal time — individual gap-ups rarely sustain when the broad market is selling.

---

## Why RSI(14) on 15-minute candles for mean reversion?

- 15-minute candles filter out tick noise but still provide 3–4 completed candles before the first entry scan at 09:25.
- RSI < 32 on the 15m chart with price near EMA-50 is a structural support level, not just a random oversold reading.
- The reversal candle requirement (hammer or bullish engulfing on the last completed candle) means the market has already started showing intent to bounce — the bot doesn't need to predict the low, just confirm it.
- Mean reversion is suppressed above VIX 20 — at high volatility, oversold can get much more oversold.

---

## Why `schedule` instead of `asyncio` or threading?

Single-threaded scheduling is sufficient here:

- All 10 scheduled jobs are short (< 2–5 seconds each, except the initial `kite.instruments()` call)
- The 60-second monitor loop is the most frequent job — still well within single-threaded capacity
- Threading would require locks on all 11 global variables in `main.py`, adding complexity and race-condition risk for zero throughput benefit
- `asyncio` would require the KiteConnect SDK to be async-compatible (it is not)

If the project grows to multi-account or multi-strategy concurrent execution, switch to `multiprocessing` with one process per account — not threading within a single process.

---

## Why SQLite instead of PostgreSQL or a cloud DB?

- Zero server dependency — the DB file travels with the code to any machine
- WAL mode gives sufficient concurrent-read performance for one bot
- The entire trade history for years fits in a single file under 10 MB
- `sqlite3.Row` gives dict-style column access without an ORM
- When moving from the organisation laptop to the personal PC, it's a single file copy

If this ever becomes multi-user or requires real-time dashboards from another machine, migrate to PostgreSQL. The schema is simple enough for a one-hour migration.

---

## Why the pre-market analysis module?

Before v1.2, strategy selection used only live Nifty gap and VIX at 09:15. That misses:

- **Overnight global cues** — if US markets fell 1% overnight, Indian momentum stocks rarely gap up sustainably even if they appear to gap up pre-open
- **Sector rotation** — a day where banking stocks are up 0.8% but IT is flat is a momentum-in-banking day, not a broad momentum day
- **F&O positioning** — put-heavy positioning (PCR > 1.2) indicates institutional hedging; call-heavy (PCR < 0.8) indicates bullish bets. Both inform whether a trend or a reversal is more likely

The pre-market module adds ±0.35 impact on momentum score and ±0.25 on mean-reversion score. All calls are exception-safe — if the NSE API is unavailable, the bot falls back to the VIX/gap-only scoring.

---

## Why no ML model?

With ₹1000 starting capital and a daily trading horizon, there are three problems:

1. **No training data yet** — the bot must trade before it can accumulate the 6–12 months of labelled trade outcomes needed to train a reliable classifier
2. **Overfitting risk** — with 50 Nifty 50 stocks × 3 strategies × daily decisions, a simple neural network will overfit to recent market regimes and fail on regime change
3. **Explainability** — if the bot loses money, rule-based strategies tell you exactly why (RSI was X, VIX was Y, OR was Z). A black-box model doesn't.

The right time to add ML is after accumulating 3–6 months of trade data in the SQLite DB. At that point, a gradient-boosted classifier (XGBoost) trained on `{vix, nifty_gap, sector_trends, rsi_breadth, prev_day_pct} → best_strategy` is a natural upgrade to the scoring functions in `selector.py`.

---

## Capital compounding model

```
Week 1, Day 1 (Monday): opening_capital = SEED_CAPITAL = ₹1000
Every subsequent day:   opening_capital = previous_day.closing_capital
                        closing_capital = opening_capital + realized_pnl
```

- Seed is used **exactly once** — on the very first run, when `daily_capital` table is empty
- After that, the bot always carries forward the most recent `closing_capital` with `IS NOT NULL AND > 0`
- Recovery mode fires when `realized_pnl < 0` on the most recent completed day — it increases risk allocation by 1.2× and limits to 1 high-confidence trade

This design means a single bad day does not reset to ₹1000 — the bot fights to recover the actual loss, not start fresh.

---

## DRY_RUN design

Every order function in `order_manager.py` checks `dry_run: bool` as its first action:

```python
if dry_run:
    fake_id = f"DRY_{random.randint(10000000, 99999999)}"
    logger.info(f"[DRY_RUN] Would place order — returning {fake_id}")
    return fake_id
```

`DRY_` prefixed IDs are recognised throughout the codebase:
- `cancel_order` returns `True` immediately for `DRY_` IDs
- `modify_sl_order` short-circuits to `True`
- `get_order_status` returns `"COMPLETE"` for `DRY_` IDs

This means the full paper-trading session is functionally identical to live — the ledger records trades, P&L compounds, recovery mode fires — just no real orders reach the exchange.
