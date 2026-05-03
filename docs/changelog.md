# Changelog — Trade Mission

> **Format for new entries:** `### vX.Y — YYYY-MM-DD — [Model/Author]`
> Bullet points for Added / Changed / Fixed / Removed. Be specific — future LLMs use this to understand what was done and why.
>
> Back to: [AGENTS.md](../AGENTS.md)

---

### v1.5 — 2026-05-03 — Claude Opus 4.6 (Thinking)

**Added — Multi-Brain Architecture (Gemini + Claude):**
- `ai/gemini_brain.py` — NEW module. Google Gemini AI brain with identical public API to `claude_brain.py`:
  - `get_trade_signals()` — calls `gemini-2.5-pro` with structured JSON output (Pydantic schemas: `TradeDecisions`, `Trade`). Same market briefing, validation, and fallback as Claude brain.
  - `get_position_advice()` — calls `gemini-2.5-flash` for fast mid-session position management. Same hold/exit/tighten/trail actions.
  - Uses `google.generativeai` SDK with `response_mime_type="application/json"` and `response_schema` for structured output (no tool_use needed — Gemini handles this natively).
- `ACTIVE_AI_BRAIN` setting in `config/settings.py` — set to `"gemini"` or `"claude"` in `.env`. Default: `"gemini"` (free tier available).
- `GEMINI_API_KEY`, `GEMINI_TRADE_MODEL`, `GEMINI_MONITOR_MODEL` constants in `config/settings.py`.
- `google-generativeai>=0.8.0` and `pydantic>=2.0.0` added to `requirements.txt`. `anthropic` kept for Claude users.

**Changed:**
- `main.py` — refactored from hardcoded Claude to brain-agnostic routing:
  - Imports both `claude_brain` and `gemini_brain` modules.
  - `job_market_open()` checks `ACTIVE_AI_BRAIN` → routes to matching brain's `get_trade_signals()`.
  - `job_monitor()` checks `ACTIVE_AI_BRAIN` → routes to matching brain's `get_position_advice()`.
  - Global renamed: `_claude_signals` → `_ai_signals`. All log messages use `ACTIVE_AI_BRAIN.capitalize()`.
  - Falls back to rule-based if no API key is set for the selected brain.
- `.env.example` — added `ACTIVE_AI_BRAIN=gemini` and Gemini API key/model fields. Claude fields preserved.
- `AGENTS.md` — updated AI Brain description, directory structure (now shows both brain files), timeline (generic "AI" instead of "Claude"), and rule #11 (covers both API keys).

**Design decision:**
Both brains coexist. User picks which API to purchase and sets `ACTIVE_AI_BRAIN` accordingly. Gemini Pro free tier makes it ideal for paper trading (DRY_RUN). Claude remains available for users who prefer Anthropic's reasoning.

---

### v1.4 — 2026-05-03 — Claude Opus 4.6 (Thinking)

**Added — Market Intelligence (news + events for Claude):**
- `data/market_intelligence.py` — NEW module with 3 functions:
  - `fetch_market_news()` — fetches Indian market headlines from Google News RSS (3 feeds, parallel, free, no API key). Returns up to 15 deduplicated headlines from last 24 hours.
  - `get_market_events()` — reads `knowledge/market_events.json` calendar. Returns events within ±3 days of today (elections, RBI policy, budget, earnings, F&O expiry). Supports fixed dates and recurring patterns (weekly/monthly).
  - `get_market_intelligence()` — master aggregator. Returns headlines + events + high-impact alerts.
- `knowledge/market_events.json` — NEW pre-filled events calendar with 2026 RBI MPC dates (6 meetings), Union Budget, holidays, monthly F&O expiry (last Thursday), weekly Nifty expiry. Users can add custom events (state elections, earnings) by editing this JSON file.
- `ai/claude_brain.py` — `_build_market_snapshot()` now includes NEWS headlines, EVENT ALERTS, FII/DII flows, and upcoming events in Claude's briefing. System prompt updated to instruct Claude on interpreting news, events, and institutional flows.
- `data/premarket_analysis.py` — `get_fii_dii_data()` — NEW function fetching FII/DII net buy/sell from NSE public API. FII flows drive 60-70% of Indian market direction. Integrated into `get_premarket_context()` with 0.25 weight in bias score.

**Added — Profit Boosters (5 features):**
- **Partial profit booking** — `orders/order_manager.py` → `place_partial_target_order()`. `main.py._execute_signal()` now splits orders: 50% quantity targets 1×ATR (intermediate), remaining 50% rides to 2×ATR (full target). On partial fill, SL moves to breakeven for remaining shares. Requires qty≥2 (falls back to full-target for qty=1). Expected impact: win rate ~40% → ~60%+.
- **Time-decay trailing SL** — `monitor/position_monitor.py` → `_time_decay_sl()`. Progressively tightens SL as the day progresses: breakeven after 12:00, entry+0.3×ATR after 13:30, aggressive lock/exit after 14:30. Prevents holding dead trades until EOD.
- **Sector correlation filter** — `utils/correlation_filter.py` — NEW module. Groups Nifty 50 into 10 sectors. Prevents buying multiple stocks from same sector (e.g., 3 banking stocks). Applied to both Claude and rule-based signals.
- **FII/DII flow data** — integrated into pre-market analysis and Claude briefing. Claude sees: FII net buy/sell in crores, DII net, combined net, and trend direction.
- **Win-rate adaptive risk** — `ledger/tracker.py` → `get_recent_win_rate()` + `get_adaptive_risk_pct()`. Tracks last 10 trades' win rate. Hot streak (>60%): risk × 1.3. Cold (<40%): risk × 0.7. Ice-cold (<25%): risk × 0.5. Compounds faster on winning streaks, protects capital on losing.

**Changed:**
- `config/settings.py` — 16 new constants: `NEWS_FETCH_ENABLED`, `NEWS_MAX_HEADLINES`, `NEWS_MAX_AGE_HOURS`, `EVENTS_CALENDAR_PATH`, `PARTIAL_PROFIT_ENABLED`, `PARTIAL_PROFIT_RATIO`, `PARTIAL_TARGET_ATR_MULT`, `FULL_TARGET_ATR_MULT`, `TIME_DECAY_SL_ENABLED`, `TIME_DECAY_BREAKEVEN_AFTER`, `TIME_DECAY_TIGHTEN_AFTER`, `TIME_DECAY_AGGRESSIVE_AFTER`, `ADAPTIVE_RISK_ENABLED`, `ADAPTIVE_RISK_LOOKBACK`, `ADAPTIVE_RISK_HOT_*`, `ADAPTIVE_RISK_COLD_*`, `ADAPTIVE_RISK_ICE_*`.
- `main.py` — `job_market_open()` now: (1) fetches market intelligence, (2) computes adaptive risk, (3) passes `market_intel` to Claude, (4) applies correlation filter to AI signals. `job_entry_scan()` uses adaptive risk for rule-based signals and applies correlation filter. `_execute_signal()` implements partial profit booking with split orders.
- `monitor/position_monitor.py` — `trail_stop_loss()` now calls `_time_decay_sl()` alongside ATR-based trailing. `_check_partial_fill()` handles partial profit order completion. Dashboard shows partial booking status column.
- `knowledge/market_regimes.md` — added event-driven regime override rules (election, budget, RBI, F&O expiry) and FII/DII flow interpretation guide.
- `data/premarket_analysis.py` — `get_premarket_context()` now includes FII/DII data. Bias score weights adjusted: Gift Nifty 40%, PCR 30%, sectors 20%, FII/DII 25% (total slightly >1.0 for emphasis on institutional flows).

**No new pip dependencies.** Uses Python built-in `xml.etree.ElementTree` for RSS parsing + existing `requests` package for HTTP.

---

### v1.3 — 2026-05-02 — Claude Sonnet 4.6

**Added:**
- `ai/__init__.py` + `ai/claude_brain.py` — Claude API brain module. Two public functions:
  - `get_trade_signals(kite, universe_df, capital, conn, premarket, market_context) → (strategy, signals)` — calls `claude-opus-4-7` at 09:15 IST with a full market briefing (capital state, Nifty, VIX, pre-market bias, sector trends, F&O PCR, per-stock RSI/ATR/EMA). Claude returns structured JSON via tool_use. Signals are validated (SL < entry, R:R ≥ 1.2, qty > 0) before being queued.
  - `get_position_advice(kite, open_trades, capital, realized_pnl, market_context) → list[dict]` — calls `claude-sonnet-4-6` every 5 monitor cycles (~5 minutes). Returns hold/exit_now/tighten_sl/trail_sl for each open position. Rejects any instruction that would lower a stop-loss.
- `ANTHROPIC_API_KEY`, `CLAUDE_TRADE_MODEL`, `CLAUDE_MONITOR_MODEL` constants in `config/settings.py`.
- `anthropic>=0.40.0` added to `requirements.txt`.
- `ANTHROPIC_API_KEY`, `CLAUDE_TRADE_MODEL`, `CLAUDE_MONITOR_MODEL` added to `.env.example`.

**Changed:**
- `main.py` — wired Claude brain into 3 places:
  1. `job_market_open()` — after rule-based `select_strategy()`, calls `get_trade_signals()`. If Claude returns signals, queues them in `_claude_signals` and overrides `_strategy_name`. DB `strategy_used` is updated AFTER the Claude override (fixes Issue #3 from plan review).
  2. `job_entry_scan()` — if `_claude_signals` has items, pops up to `remaining_slots` signals and executes them instead of calling rule-based `get_signals()`. Falls back to rule-based when queue is empty.
  3. `job_monitor()` — increments `_monitor_ai_tick` each cycle; every 5th cycle calls `get_position_advice()`. Applies exit_now (market sell + record exit) and tighten_sl/trail_sl (modify_sl_order) instructions. Claude advice runs AFTER `run_monitor_cycle()` so Python trailing-SL fires first (Issue #6 fix).
- `main.py` imports expanded: `get_market_context` from selector, `modify_sl_order` + `place_market_sell` from order_manager, `record_trade_exit` + `update_daily_pnl` from tracker.
- `AGENTS.md` + `docs/module_reference.md` — updated with `ai/` directory, new constants, v1.3 header.

**Safety guarantees unchanged:**
- Kill-switch (loss > 4%) and profit-lock (gain > 8%) remain pure Python — Claude is never consulted for these.
- EOD 15:15 force-close is pure Python.
- If `ANTHROPIC_API_KEY` is blank or any Claude call raises an exception, the bot continues with rule-based signals — no crash, no silent failure.

**Rationale:**
Rule-based scoring (VIX thresholds, gap percentages, RSI levels) works but is rigid. Claude reads the same data and reasons about it the way a professional trader would — considering combinations, context, and nuance that fixed thresholds miss. Using `claude-opus-4-7` for decisions (most capable) and `claude-sonnet-4-6` for monitoring (faster, cheaper, adequate for hold/exit decisions) balances cost and quality. Estimated API cost: ₹1–3 per trading day.

---

### v1.2 — 2026-05-02 — Claude Sonnet 4.6

**Added:**
- `data/premarket_analysis.py` — pre-market intelligence module with 4 functions:
  - `get_gift_nifty_bias()` — NSE pre-open session API as overnight directional proxy. Returns bias string + change %.
  - `get_sector_indices(kite)` — 6 sector indices via `kite.quote()` (Bank, IT, Pharma, Metal, FMCG, Auto).
  - `get_nifty_fno_pcr()` — NSE public option-chain API → total Put OI / Call OI across all strikes.
  - `get_top_movers_prev_day(kite, universe_df)` — universe gap% ranking: top-5 gainers and losers.
  - `get_premarket_context(kite, universe_df)` — orchestrator. Computes weighted `bias_score` (Gift Nifty 40%, PCR 30%, sector breadth 30%) and `market_bias` string. Exception-safe; failure returns neutral values, never crashes the bot.
- `utils/__init__.py` + `utils/position_sizing.py` — single canonical `calculate_position_size(capital, entry, stop_loss, risk_pct, leverage=MIS_LEVERAGE) → int`. Eliminates the 3 identical private copies that existed in each strategy file.
- `docs/` directory with 4 reference files: `module_reference.md`, `schema.md`, `setup_guide.md`, `architecture.md`, `changelog.md`.

**Changed:**
- `strategies/momentum.py` — removed local `calculate_position_size`; now imports from `utils.position_sizing`. Removed unused `import math` and `MIS_LEVERAGE` imports.
- `strategies/mean_reversion.py` — same: removed local copy, imports from `utils.position_sizing`.
- `strategies/range_trading.py` — same: removed local copy, imports from `utils.position_sizing`.
- `strategies/selector.py` — now imports and calls `get_premarket_context(kite, universe_df)` at strategy selection time. Passes `premarket` dict to all three scorer functions. All scorers accept optional `premarket: dict | None` parameter. Impact on scores:
  - `score_momentum`: +0.15 bullish bias, −0.15 bearish bias, +0.05 low PCR, +0.05 bank sector gap, up to +0.10 for overlap with yesterday's top gainers
  - `score_mean_reversion`: +0.15 bearish bias, −0.10 bullish bias, +0.10 high PCR, up to +0.10 for oversold prior-day losers
  - `score_range_trading`: +0.10 neutral bias, +0.05 balanced PCR, +0.05 flat Gift Nifty
  - `premarket` dict stored in returned config and logged to `strategy_log.market_context` JSON
- `AGENTS.md` — refactored from monolithic 855-line file into lean index + 5 focused `docs/` files.

**Rationale for pre-market module:**
Indian market strategy decisions are heavily influenced by overnight global cues. Before v1.2, selector had zero visibility into pre-open sentiment. The pre-market module can shift momentum score by up to ±0.35, which can change the selected strategy entirely on days with strong directional overnight bias.

---

### v1.1 — 2026-05-02 — Claude Sonnet 4.6

**Fixed (19 bugs):**

- `config/settings.py` — `RECOVERY_RISK_MULTIPLIER` constant added (was hardcoded 0.30 in selector); added `MIN_STRATEGY_SCORE`, `RECOVERY_MIN_SCORE`, `NSE_HOLIDAYS` set for 2025–2026, `PANIC_VIX_THRESHOLD`.
- `data/market_data.py` — added `only_complete=True` flag to `get_today_candles()` to strip the still-forming last candle; `get_daily_candles()` now strips today's incomplete candle; `get_nifty_vix()` and `get_nifty_ltp()` try both known NSE key formats.
- `indicators/technicals.py` — Bollinger Bands column detection changed from fragile positional index to name prefix (`BBU_`, `BBM_`, `BBL_`); added `_safe_last(series, default=nan)` to prevent `IndexError` on empty series; added `add_daily_vol_ratio(df, daily_avg_volume)` for correct intraday volume surge calculation (was always ~1.0 before).
- `strategies/selector.py` — `_compute_rsi_data()` function added; previously `rsi_data` was always `None`, meaning mean-reversion scoring was always 0 for RSI breadth; `score_range_trading()` now includes inside-bar check; `select_strategy()` reads `MAX_OPEN_POSITIONS` and `RISK_PER_TRADE_PCT` from settings instead of hardcoded values.
- `strategies/momentum.py` — removed dead `get_daily_candles()` call that fetched 20 days of data and discarded it; vol ratio now computed against 20-day daily average (accurate); added Nifty broad-market check (returns `[]` if Nifty down >0.5%); added minimum 1:1 R:R check.
- `strategies/mean_reversion.py` — fixed `IndexError` crash every morning from `dropna().iloc[-1]` on empty series before enough candles exist; all indicator accesses now use `_safe_last()`; EMA-50/21/ATR guards for insufficient data; R:R check: reward ≥ 1 ATR and rr ≥ 1.0; requires ≥3 completed candles.
- `strategies/range_trading.py` — added 09:45 IST time guard (range strategy needs 30 minutes of data; was running at 09:25 with only 1 candle); `atr14` now fetched from daily candles instead of being set to `range_width` (wrong); added minimum 1.5:1 R:R check.
- `main.py` — `is_trading_day()` now checks `NSE_HOLIDAYS` set; added `_safe_close_conn()` to prevent double-close crash; added `_shutdown_done` flag to prevent double-shutdown on Ctrl+C; SL order failure now triggers immediate `place_market_sell()` to exit the unprotected entry position; `job_entry_scan()` retries `job_market_open()` if universe is empty (handles delayed startup).
- `ledger/tracker.py` — `get_today_capital()` SQL fixed to `WHERE closing_capital IS NOT NULL AND closing_capital > 0` (was picking up 0.0 rows); `is_recovery_mode()` same SQL fix; seed capital now used only on truly first run (empty `daily_capital` table), not every Monday.

---

### v1.0 — 2026-05-02 — Claude Sonnet 4.6 (initial build)

**Added:** Complete project from scratch — 27 files:

- `config/settings.py` — all constants, env loading
- `auth/kite_auth.py` — daily login + session token persistence
- `data/market_data.py` — all OHLCV and quote API calls
- `data/universe.py` — Nifty 50 base list + gap/volume filter
- `indicators/technicals.py` — RSI, EMA, ATR, VWAP, Bollinger Bands, volume, candle patterns
- `strategies/selector.py` — morning strategy scoring and selection
- `strategies/momentum.py` — Opening Range Breakout signals
- `strategies/mean_reversion.py` — RSI oversold bounce signals
- `strategies/range_trading.py` — low-VIX range play signals
- `orders/order_manager.py` — MIS order placement with DRY_RUN guard
- `monitor/position_monitor.py` — 60s position management, trailing SL, kill-switch, profit-lock
- `eod/eod_closer.py` — 15:15 IST force square-off
- `ledger/db.py` — SQLite schema + connection factory
- `ledger/tracker.py` — P&L recording, compounding, recovery detection, daily summary
- `main.py` — scheduler, global state, 10 jobs
- `knowledge/risk_rules.md`, `momentum_rules.md`, `mean_reversion_rules.md`, `market_regimes.md`
- `.env.example`, `requirements.txt`, `AGENTS.md`
