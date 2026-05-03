# AGENTS.md — Trade Mission: LLM Instruction Index

> **RULES FOR EVERY LLM THAT EDITS THIS PROJECT:**
> 1. **Read this entire file first, then read the relevant `docs/` file before touching code.**
> 2. **After any change**, add a versioned entry to [docs/changelog.md](docs/changelog.md).
> 3. **Keep docs/ accurate.** Rename a function → update [docs/module_reference.md](docs/module_reference.md). Add a module → update the directory structure below.
> 4. **Never hardcode** values that belong in `config/settings.py`.
> 5. **Never call `kite.instruments()`** outside `data/market_data._load_instruments()`.
> 6. **DRY_RUN guard** is mandatory on every new order function in `order_manager.py`.

---

## Reference Docs (read before working on that area)

| Topic | File |
|---|---|
| All function signatures, parameters, return types | [docs/module_reference.md](docs/module_reference.md) |
| SQLite schema, signal dict format, `.env` variables | [docs/schema.md](docs/schema.md) |
| Full setup, daily operation, troubleshooting guide | [docs/setup_guide.md](docs/setup_guide.md) |
| Why the code is designed the way it is | [docs/architecture.md](docs/architecture.md) |
| Full version history of every change | [docs/changelog.md](docs/changelog.md) |

---

## 1. Project Overview

**Trade Mission** is a fully automated intraday trading bot for Zerodha Kite (NSE, India).

| Property | Value |
|---|---|
| Owner | Kerala-based user |
| Currency | Indian Rupees (Rs / INR) |
| Platform | Windows 11 (organisation laptop), Python 3.11+ |
| Broker | Zerodha — KiteConnect API (₹2000/month add-on required) |
| Capital model | ₹1000 one-time seed on first Monday; profits compound daily Mon–Fri |
| Trading style | Intraday only — MIS product, long-only, all positions closed by 15:15 IST |
| Safety default | `DRY_RUN=true` in `.env` — no real orders until set to `false` |
| AI Brain | Multi-model support: Google Gemini (Pro/Flash) or Anthropic Claude (Opus/Sonnet). Set `ACTIVE_AI_BRAIN` in `.env`. Falls back to rule-based if key missing. |

**Capital compounding model:**
- First Monday ever: seed ₹1000 (only time seed is used)
- Every subsequent day: `opening_capital = previous_day.closing_capital`
- `closing_capital = opening_capital + realized_pnl`
- Loss day → `closing_capital < opening_capital` → **recovery mode** next day

---

## 2. Directory Structure

```
trade_mission/
├── main.py                     Entry point. All scheduled jobs. Global state lives here.
├── .env                        Real credentials — NEVER commit, NEVER log.
├── .env.example                Template for all required env vars.
├── requirements.txt            All pip dependencies.
├── AGENTS.md                   THIS FILE — index and rules for LLMs.
│
├── docs/                       Detailed reference docs (read these before editing)
│   ├── module_reference.md     Every function signature in every module.
│   ├── schema.md               SQLite schema, signal dict format, .env reference.
│   ├── setup_guide.md          Step-by-step setup, daily operation, troubleshooting.
│   ├── architecture.md         Why the code is designed the way it is.
│   └── changelog.md            Full version history.
│
├── config/
│   └── settings.py             Single source of truth for ALL constants and env vars.
│                               Every other module imports from here. Never use os.getenv() elsewhere.
│
├── knowledge/                  Human-readable financial rules and structured data.
│   ├── risk_rules.md           Core sizing and drawdown rules.
│   ├── momentum_rules.md       Rules for ORB and gap-up continuation.
│   ├── mean_reversion_rules.md Rules for RSI oversold and EMA-50 bounces.
│   ├── market_regimes.md       Includes event-driven regime overrides + FII/DII rules.
│   └── market_events.json      Events calendar: elections, RBI policy, budget, F&O expiry (used by market_intelligence).
│
├── auth/
│   └── kite_auth.py            Daily login + token persistence. Token expires at midnight IST.
│
├── ai/
│   ├── claude_brain.py         Claude API brain. Morning trade decisions + mid-session position advice.
│   └── gemini_brain.py         Gemini API brain. Same responsibilities, selected via ACTIVE_AI_BRAIN.
│                               Falls back silently to rule-based if API key missing or call fails.
│
├── data/
│   ├── market_data.py          All KiteConnect API calls. Instruments master cached once per session.
│   ├── universe.py             Nifty 50 base list + live gap/volume filter → top 20 stocks.
│   ├── premarket_analysis.py   Pre-market context: sector indices, F&O PCR, Gift Nifty bias, FII/DII flows.
│   └── market_intelligence.py  News headlines (Google News RSS) + events calendar → Claude briefing.
│
├── indicators/
│   └── technicals.py           RSI, EMA, ATR, VWAP, Bollinger Bands, volume, reversal candles.
│
├── utils/
│   ├── position_sizing.py      Single canonical position-size calculator. All strategies use this.
│   └── correlation_filter.py   Sector correlation filter — max 1 stock per sector.
│
├── strategies/
│   ├── selector.py             Morning strategy picker. Uses premarket context to score strategies.
│   ├── momentum.py             Opening Range Breakout — gap-up + volume surge + OR-high break.
│   ├── mean_reversion.py       RSI oversold bounce — RSI<32 + EMA-50 + reversal candle.
│   └── range_trading.py        Low-VIX range play — buy near range low, target range high.
│
├── orders/
│   └── order_manager.py        All MIS order types. DRY_RUN guard on every function.
│
├── monitor/
│   └── position_monitor.py     60s loop: trail SL, time-decay SL, partial profit, kill-switch, dashboard.
│
├── eod/
│   └── eod_closer.py           15:15 IST: cancel all orders first, then market-close all positions.
│
├── ledger/
│   ├── db.py                   SQLite schema + WAL-mode connection factory.
│   └── tracker.py              P&L recording, compounding, recovery detection, daily summary.
│
└── logs/                       Auto-created. Rotating daily files: trade_mission_YYYYMMDD.log.
```

---

## 3. Daily Execution Timeline (IST)

| Time | Job | Action |
|---|---|---|
| 08:45 | (manual) | Run `python main.py`, complete browser login, paste `request_token` |
| 09:00 | `job_premarket` | Authenticate Kite, init DB, load capital, reconcile yesterday |
| 09:15 | `job_market_open` | Filter universe, fetch market intelligence (news+events), compute adaptive risk, run `select_strategy()`, get AI trade signals |
| 09:25 | `job_entry_scan` | First entry — executes AI signals or falls back to rule-based, places orders, applies correlation filter |
| 09:30 | `job_entry_scan` | Repeat — fill remaining slots |
| 09:45 | `job_entry_scan` | Repeat (range strategy eligible from here — needs 30 min data) |
| 10:00 | `job_entry_scan` | Repeat |
| 10:15 | `job_entry_scan` | **Final entry.** No new entries after this time. |
| Every 60s | `job_monitor` | Trail SL, time-decay SL, check partial profit fills, kill-switch, profit-lock. Every 5 mins: AI position advice |
| 15:15 | `job_eod_close` | Cancel all orders → MARKET SELL all open MIS positions |
| 15:30 | `job_shutdown` | EOD compound, print daily summary, close DB, exit |

**Emergency exits (intraday):**
- Loss > 4% of opening capital → `KILL_SWITCH` → all positions closed, entries halted
- Gain > 8% of opening capital → `PROFIT_LOCK` → all positions closed, entries halted
- Ctrl+C → `job_eod_close()` then `job_shutdown()` (safe exit)

---

## 4. Critical Rules (Do Not Break These)

1. **Instruments master cached once.** `_load_instruments()` in `market_data.py` populates `_instruments_cache`. Never call `kite.instruments()` anywhere else — it is slow and will hit rate limits.

2. **`only_complete=True` on all intraday candle fetches.** The last candle is always forming. Passing it to indicators produces false signals. Always use `only_complete=True` in `get_today_candles()`.

3. **Cancel orders BEFORE closing positions at EOD.** `cancel_all_open_orders` must run before `close_all_positions`. Pending SL and target orders create double-sells if positions are closed first.

4. **SL orders must be SL-Market (`ORDER_TYPE_SLM`), not SL-Limit.** SL-Limit can miss fills on fast moves or gap-opens.

5. **SL failure = immediate market exit.** If `place_sl_order` returns `None`, `_execute_signal` must immediately call `place_market_sell`. Never hold a position without a stop-loss.

6. **All times in IST.** Import `IST` from `config.settings`. Always `datetime.now(IST)`. Never use naive `datetime.now()`.

7. **Never hardcode strategy thresholds.** Gap %, VIX levels, RSI thresholds, risk % — all live in `settings.py`. Add new thresholds there.

8. **`calculate_position_size` lives only in `utils/position_sizing.py`.** Import from there. Do not duplicate.

9. **EOD close must run by 15:15 IST.** Zerodha auto-squares MIS positions 15:20–15:30 with a penalty. Never move `EOD_SQUAREOFF_TIME` past 15:15.

10. **Recovery mode is automatic.** Detected by `is_recovery_mode(conn)` in `tracker.py`. Applied in `select_strategy`. Parameters in `settings.py`. Do not hardcode recovery behaviour elsewhere.

11. **AI API Fallback.** If the selected API key (`GEMINI_API_KEY` or `ANTHROPIC_API_KEY`) is missing or the API call fails, the bot MUST silently fall back to rule-based execution. No crashes allowed.

12. **Profit Boosters.** The system enforces 5 profit boosters dynamically:
    - **Partial Profit Booking:** 50% booked at 1x ATR, remaining rides to 2x ATR with SL at breakeven (`order_manager`).
    - **Time-Decay SL:** SL progressively tightens after 12:00, 13:30, and 14:30 to prevent holding dead trades (`position_monitor`).
    - **Sector Correlation Filter:** Max 1 open trade per sector (`utils/correlation_filter`).
    - **Win-Rate Adaptive Risk:** Position sizing risk scales up or down based on the last 10 trades' win rate (`ledger/tracker`).
    - **Market Intelligence:** Google News RSS + `market_events.json` + FII/DII data give macro context to Claude (`market_intelligence`).
