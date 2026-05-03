# Module Reference — Trade Mission v1.5

> Function signatures for every module. Keep this file accurate when renaming or adding functions.
> Back to: [AGENTS.md](../AGENTS.md)

---

## `config/settings.py`

Pure constants module — no functions. **Every configurable value lives here.**

| Constant | Default | What it controls |
|---|---|---|
| `SEED_CAPITAL` | `1000.0` | First-ever seed (used exactly once) |
| `RISK_PER_TRADE_PCT` | `0.25` | Fraction of daily capital risked per trade |
| `MAX_OPEN_POSITIONS` | `3` | Max simultaneous open trades |
| `MAX_DAILY_LOSS_PCT` | `0.04` | Kill-switch threshold (4%) |
| `PROFIT_LOCK_PCT` | `0.08` | Profit-lock threshold (8%) |
| `MIS_LEVERAGE` | `3` | Conservative intraday leverage for sizing cap |
| `RECOVERY_RISK_MULTIPLIER` | `1.2` | Multiplied by `RISK_PER_TRADE_PCT` in recovery mode |
| `RECOVERY_MAX_POSITIONS` | `1` | Only 1 trade allowed on recovery days |
| `RECOVERY_MIN_SCORE` | `0.70` | Signal must score above this in recovery mode |
| `MIN_STRATEGY_SCORE` | `0.30` | Below this, skip trading entirely |
| `PANIC_VIX_THRESHOLD` | `25.0` | Above this VIX, skip trading entirely |
| `SL_ATR_MULTIPLIER` | `1.0` | Stop = entry − (1 × ATR14) |
| `TARGET_ATR_MULTIPLIER` | `2.0` | Target = entry + (2 × ATR14) |
| `LAST_ENTRY_TIME` | `time(10,15)` | No new entries after this |
| `EOD_SQUAREOFF_TIME` | `time(15,15)` | EOD close trigger — never move later than 15:15 |
| `NSE_HOLIDAYS` | `set[date]` | NSE holiday dates 2025–2026. Update annually. |
| `NIFTY50_SYMBOLS` | list of 50 | Hardcoded. Update quarterly with NSE circulars. |
| `ANTHROPIC_API_KEY` | `""` | Anthropic API key. Blank = Claude disabled, rule-based only. |
| `CLAUDE_TRADE_MODEL` | `"claude-opus-4-7"` | Model for morning trade decisions (most capable). |
| `CLAUDE_MONITOR_MODEL` | `"claude-sonnet-4-6"` | Model for mid-session position advice (faster/cheaper). |
| `ACTIVE_AI_BRAIN` | `"gemini"` | Which AI brain to use: `"gemini"` or `"claude"`. |
| `GEMINI_API_KEY` | `""` | Google Gemini API key. Blank = Gemini disabled. |
| `GEMINI_TRADE_MODEL` | `"gemini-2.5-pro"` | Gemini model for morning trade decisions (best reasoning). |
| `GEMINI_MONITOR_MODEL` | `"gemini-2.5-flash"` | Gemini model for mid-session position advice (fast + smart). |
| `NEWS_FETCH_ENABLED` | `True` | Fetch Google News RSS for Claude |
| `NEWS_MAX_HEADLINES` | `15` | Max headlines to send to Claude |
| `NEWS_MAX_AGE_HOURS` | `24` | Cutoff age for news headlines |
| `EVENTS_CALENDAR_PATH` | `Path` | Path to `market_events.json` |
| `PARTIAL_PROFIT_ENABLED` | `True` | Book 50% at 1x ATR, rest at 2x ATR |
| `PARTIAL_PROFIT_RATIO` | `0.5` | Fraction of qty to sell at partial target |
| `PARTIAL_TARGET_ATR_MULT`| `1.0` | Target for partial profit (1x ATR) |
| `FULL_TARGET_ATR_MULT` | `2.0` | Target for remaining qty (2x ATR) |
| `TIME_DECAY_SL_ENABLED` | `True` | Progressively tighten SL over time |
| `TIME_DECAY_BREAKEVEN_AFTER` | `12:00` | Move SL to breakeven after this time |
| `TIME_DECAY_TIGHTEN_AFTER` | `13:30` | Tighten SL to entry+0.3ATR after this time |
| `TIME_DECAY_AGGRESSIVE_AFTER` | `14:30` | Lock profit / cut losses after this time |
| `ADAPTIVE_RISK_ENABLED` | `True` | Dynamically adjust risk based on win rate |

---

## `ai/claude_brain.py`

Claude AI brain — replaces rule-based strategy scoring with real intelligence.
Both functions are **fully exception-safe** — any failure returns empty list and the bot falls back to rule-based. Never crashes the bot.
Requires `ANTHROPIC_API_KEY` in `.env`. If blank, functions return immediately without calling the API.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_trade_signals` | `(kite, universe_df, capital, conn, premarket, market_context) → tuple[str, list[dict]]` | `(strategy_name, signals)` | Calls `claude-opus-4-7` with full market briefing. Returns validated signal dicts compatible with `main._execute_signal()`. Validates: SL < entry, target > entry, R:R ≥ 1.2, qty > 0. |
| `get_position_advice` | `(kite, open_trades, capital, realized_pnl, market_context) → list[dict]` | list of action dicts | Calls `claude-sonnet-4-6`. Returns one action per open trade. Rejects any SL that would be lower than current SL. |

**`get_trade_signals` returns signal dicts** — same format as rule-based strategies, compatible with `_execute_signal()`:
```python
{"symbol", "exchange", "instrument_token", "direction", "entry_price",
 "stop_loss", "target_price", "atr14", "quantity", "strategy", "rationale", "score"}
```

**`get_position_advice` returns action dicts:**
```python
{
    "trade_id": int,    # key in _open_trades
    "action":   str,    # "hold" | "exit_now" | "tighten_sl" | "trail_sl"
    "new_sl":   float,  # required for tighten_sl / trail_sl
    "reason":   str,    # Claude's explanation
}
```

**Internal helpers (not called externally):**
- `_get_client()` — lazy-loads `anthropic.Anthropic` client; returns `None` if key missing or package not installed
- `_build_market_snapshot(...)` — builds the full morning briefing text sent to Claude (capital, Nifty, VIX, pre-market, per-stock RSI/ATR/EMA)
- `_build_position_snapshot(...)` — builds mid-session position review text
- `_TRADE_TOOL` / `_MONITOR_TOOL` — Claude tool schemas for structured JSON output

---

## `ai/gemini_brain.py`

Gemini AI brain — same role as `claude_brain.py`, selected via `ACTIVE_AI_BRAIN="gemini"` in `.env`.
Uses `google.generativeai` SDK with Pydantic schemas for structured JSON output.
Requires `GEMINI_API_KEY` in `.env`. If blank, functions return immediately without calling the API.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_trade_signals` | `(kite, universe_df, capital, conn, premarket, market_context, market_intel=None) → tuple[str, list[dict]]` | `(strategy_name, signals)` | Calls `gemini-2.5-pro` with full market briefing. Returns validated signal dicts compatible with `main._execute_signal()`. Same validations as Claude brain. |
| `get_position_advice` | `(kite, open_trades, capital, realized_pnl, market_context) → list[dict]` | list of action dicts | Calls `gemini-2.5-flash`. Same action format as Claude brain. |

**Pydantic schemas (for Gemini structured output):**
- `Trade` — single trade: symbol, direction, entry_price, stop_loss, target_price, atr14, confidence, rationale
- `TradeDecisions` — strategy_today, strategy_rationale, list of `Trade`
- `PositionAction` — trade_id, action, new_sl, reason
- `PositionAdvice` — list of `PositionAction`

---

## `auth/kite_auth.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_kite` | `() → KiteConnect` | `KiteConnect` | Main entry point. Tries `load_session` first; falls back to `interactive_login`. Raises `RuntimeError` if `KITE_API_KEY` is blank. |
| `load_session` | `() → KiteConnect \| None` | `KiteConnect` or `None` | Reads `TOKEN_PATH` JSON, validates today's IST date. Returns `None` if file missing, stale, or unreadable. |
| `save_session` | `(access_token: str) → None` | `None` | Writes `{"access_token": ..., "date": "YYYY-MM-DD"}` to `TOKEN_PATH`. Creates parent dir if needed. |
| `interactive_login` | `() → KiteConnect` | `KiteConnect` | Prints login URL. Blocks on `input()` for user to paste `request_token`. Calls `generate_session`, saves token. |

**Gotcha:** The `input()` in `interactive_login` blocks the process. Login must complete before 09:15 or strategy selection will be delayed.

---

## `data/market_data.py`

Module-level: `_instruments_cache: pd.DataFrame | None = None` — populated once per process by `_load_instruments`. **Never call `kite.instruments()` anywhere else.**

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_instrument_token` | `(kite, symbol, exchange="NSE") → int \| None` | `int` or `None` | Calls `_load_instruments` (cached). Returns `None` if symbol not found. |
| `fetch_ohlcv` | `(kite, instrument_token, interval, from_dt, to_dt) → pd.DataFrame` | DataFrame | Returns empty DataFrame on error or no data. Sorts ascending by `date`. |
| `fetch_ltp` | `(kite, symbols, exchange="NSE") → dict[str, float]` | `{symbol: price}` | Batches 400 per call. Returns partial results on error. |
| `fetch_quotes` | `(kite, symbols, exchange="NSE") → dict` | `{symbol: quote_dict}` | Batches 400 per call. Full OHLC+volume+depth. |
| `get_today_candles` | `(kite, symbol, interval="5minute", only_complete=True) → pd.DataFrame` | DataFrame | Fetches from 09:00 IST to now. If `only_complete=True`, strips last (forming) candle. |
| `get_daily_candles` | `(kite, symbol, days=60) → pd.DataFrame` | DataFrame | Excludes today's incomplete candle. Adds 10-day buffer for holidays. Returns last `days` rows. |
| `get_nifty_vix` | `(kite) → float` | `float` | Tries `"NSE:INDIA VIX"` and `"NSE:INDIAVIX"`. Returns `15.0` on failure. |
| `get_nifty_ltp` | `(kite) → float` | `float` | Tries `"NSE:NIFTY 50"` and `"NSE:NIFTY50"`. Returns `0.0` on failure. |

---

## `data/universe.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_base_universe` | `() → list[str]` | `list[str]` | Returns copy of `NIFTY50_SYMBOLS`. No I/O. |
| `filter_universe` | `(kite, symbols=None) → pd.DataFrame` | DataFrame | Fetches live quotes, computes `gap_pct`, returns top 20 by `abs_gap`. Columns: `symbol, instrument_token, prev_close, current_price, gap_pct, volume, abs_gap`. |
| `get_premarket_snapshot` | `(kite, symbols=None) → pd.DataFrame` | DataFrame | 25 days daily candles per symbol. Columns: `symbol, prev_close, prev_high, prev_low, atr14, avg_daily_volume, 52w_high, 52w_low`. Skips symbols with <5 candles. |

---

## `data/premarket_analysis.py`

Gathers broad market intelligence before the session opens to improve strategy selection.
All external network calls are fully exception-safe — failure here never crashes the bot.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_premarket_context` | `(kite, universe_df) → dict` | `dict` | Orchestrator. Calls all helpers, computes composite `bias_score` and `market_bias`. |
| `get_gift_nifty_bias` | `() → dict` | `dict` | NSE pre-open session API as directional proxy. Returns `{"change_pct": float, "bias": "bullish"/"bearish"/"neutral", "source": str}`. Neutral on failure. |
| `get_sector_indices` | `(kite) → dict[str, dict]` | `dict` | `kite.quote()` for NIFTY BANK, IT, PHARMA, METAL, FMCG, AUTO. Returns `{"bank": {"ltp": float, "change_pct": float}, ...}`. |
| `get_nifty_fno_pcr` | `() → float` | `float` | NSE public option-chain API. Put OI / Call OI. Returns 1.0 (neutral) on failure. PCR < 0.8 = bullish, > 1.2 = bearish. |
| `get_top_movers_prev_day` | `(kite, universe_df, top_n=5) → dict` | `dict` | Gap% per universe symbol. Returns `{"gainers": [...], "losers": [...], "changes": {sym: pct}}`. |

**Output dict from `get_premarket_context`:**
```python
{
    "gift_nifty_bias":   str,    # "bullish" | "bearish" | "neutral"
    "gift_nifty_change": float,  # % change from NSE pre-open
    "sector_trends":     dict,   # {"bank": {"ltp": float, "change_pct": float}, ...}
    "fno_pcr":           float,  # Nifty Put-Call Ratio
    "top_gainers":       list,   # top-5 prev-day gainers in universe
    "top_losers":        list,   # top-5 prev-day losers in universe
    "sym_changes":       dict,   # {symbol: gap_pct}
    "market_bias":       str,    # "bullish" | "bearish" | "neutral" (composite)
    "bias_score":        float,  # signed score (~-1.0 to +1.0)
}
```

---

## `data/market_intelligence.py`

Fetches news from free Google News RSS feeds and reads local events calendar.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `fetch_market_news` | `() → list[dict]` | list of headline dicts | Parallel fetches 3 RSS feeds. Returns up to 15 recent headlines. |
| `get_market_events` | `(lookahead_days=3) → list[dict]` | list of event dicts | Reads `market_events.json`. Returns fixed and recurring events in window. |
| `get_market_intelligence` | `() → dict` | dict | Master aggregator. Returns `headlines`, `upcoming_events`, `event_alerts`, `has_high_impact_today`, `summary`. |

---

## `indicators/technicals.py`

All functions take a DataFrame with OHLCV columns and return it enriched. Operate on a copy inside `compute_all`.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `add_rsi` | `(df, period=14) → pd.DataFrame` | DataFrame | Adds `rsi_14`. Requires `close`. |
| `add_ema` | `(df, periods=None) → pd.DataFrame` | DataFrame | Adds `ema_9`, `ema_21`, `ema_50`. Requires `close`. |
| `add_atr` | `(df, period=14) → pd.DataFrame` | DataFrame | Adds `atr_14`. Requires `high, low, close`. |
| `add_vwap` | `(df) → pd.DataFrame` | DataFrame | Manual cumulative VWAP. Adds `vwap`. |
| `add_bollinger_bands` | `(df, period=20, std=2.0) → pd.DataFrame` | DataFrame | Adds `bb_upper, bb_middle, bb_lower`. Detects pandas_ta columns by prefix (`BBU_`, `BBM_`, `BBL_`). |
| `add_volume_metrics` | `(df, avg_period=20) → pd.DataFrame` | DataFrame | Adds `vol_sma` (rolling) and `vol_ratio`. Only meaningful with historical candles, not just today's. |
| `add_daily_vol_ratio` | `(df, daily_avg_volume: float) → pd.DataFrame` | DataFrame | **Use for intraday.** `vol_ratio = candle_volume / (daily_avg_volume / 75)`. 75 = candles per session. |
| `compute_all` | `(df) → pd.DataFrame` | DataFrame | Runs all of the above on a copy. |
| `is_gap_up` | `(current_price, prev_close, min_gap_pct=1.5) → bool` | `bool` | |
| `is_gap_down` | `(current_price, prev_close, min_gap_pct=1.5) → bool` | `bool` | |
| `detect_reversal_candle` | `(df) → str \| None` | `str` or `None` | Last completed candle only. Requires `only_complete=True` upstream. Returns `"hammer"`, `"engulfing"`, `"doji"`, or `None`. |
| `get_opening_range` | `(candles_df) → dict` | `dict` | Returns `{or_high, or_low, or_open, or_range}`. |
| `_safe_last` | `(series, default=nan) → float` | `float` | Returns last non-NaN, or `default`. Prevents `IndexError` on empty series. Imported by all strategy files. |

---

## `utils/position_sizing.py`

Single canonical implementation — all strategy files import from here.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `calculate_position_size` | `(capital, entry, stop_loss, risk_pct, leverage=MIS_LEVERAGE) → int` | `int` | `qty = floor((capital × risk_pct) / (entry − stop_loss))`, capped at `floor((capital × leverage) / entry)`. Returns `0` if `stop_loss >= entry`. |

---

## `utils/correlation_filter.py`

Prevents buying multiple stocks in the same sector on the same day.

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_sector` | `(symbol: str) → str` | `str` | Returns sector name (e.g. `"banking"`) from hardcoded `SECTOR_MAP`. |
| `filter_correlated` | `(signals: list, open_trades: dict, max_per_sector=1) → list` | list of signals | Removes signals from sectors that already have `max_per_sector` open positions. |

---

## `strategies/selector.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `load_knowledge_files` | `() → dict[str, str]` | `{stem: content}` | Reads all `*.md` from `KNOWLEDGE_DIR`. For logging only — rules enforced in Python, not parsed from markdown. |
| `get_market_context` | `(kite) → dict` | `dict` | Returns `{vix, nifty_ltp, nifty_gap_pct, prev_day_nifty_pct, prev_day_was_inside_bar, nifty_avg_atr}`. Uses 22 daily Nifty candles. |
| `score_momentum` | `(universe_df, context, premarket=None) → float` | `float 0–1` | Rewards gap-up stocks, vol ratio, bullish overnight bias, low PCR, sector breadth. Penalises bearish bias or high VIX. |
| `score_mean_reversion` | `(universe_df, context, rsi_data, premarket=None) → float` | `float 0–1` | Rewards moderate VIX, prior-day sell-off, oversold RSI count, bearish overnight bias, high PCR. |
| `score_range_trading` | `(universe_df, context, premarket=None) → float` | `float 0–1` | Rewards low VIX, flat Nifty gap, inside-bar day, neutral overnight bias, balanced PCR. |
| `select_strategy` | `(kite, universe_df, conn=None) → tuple[str, dict]` | `(name, config)` | Calls `get_premarket_context()` → scores all strategies → returns winner. Config includes `scores, selected_score, market_context, premarket, risk_pct, max_positions, recovery_mode, rsi_data`. |

---

## `strategies/momentum.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `scan_candidates` | `(kite, universe_df, config) → list[dict]` | list of signal dicts | Returns `[]` if Nifty is down >0.5%. Checks OR-high break, vol ratio vs daily average, 1:1 R:R minimum. Sorted by `score` desc. |
| `get_signals` | `(kite, universe_df, capital, config, risk_pct=0.25, max_positions=3) → list[dict]` | list of signals | Calls `scan_candidates` + `calculate_position_size`. Omits `qty == 0` entries. |

---

## `strategies/mean_reversion.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `scan_candidates` | `(kite, universe_df, config, current_vix=15.0) → list[dict]` | list of signal dicts | Returns `[]` if VIX > `max_vix`. Needs ≥3 completed 15m candles. RSI<32, within 1.5% of EMA-50, reversal candle, reward ≥1 ATR, R:R ≥1.0. SL = candle_low × 0.998, target = EMA-21. |
| `get_signals` | `(kite, universe_df, capital, config, current_vix=15.0, risk_pct=0.25, max_positions=3) → list[dict]` | list of signals | Calls `scan_candidates` + `calculate_position_size`. |

---

## `strategies/range_trading.py`

Module-level: `_MIN_RANGE_CANDLES = 2`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `establish_range` | `(kite, symbol, config) → dict \| None` | `dict` or `None` | Uses first `_MIN_RANGE_CANDLES` completed 15m candles. Returns `None` if width > `max_range_pct`. Returns `{range_high, range_low, range_mid, range_width, current_price}`. |
| `scan_candidates` | `(kite, universe_df, config, current_vix=12.0) → list[dict]` | list of signal dicts | Returns `[]` if VIX > `max_vix` or time < 09:45. Entry zone = bottom 20% of range. SL = range_low × 0.995, target = range_high × 0.99. R:R ≥ 1.5. |
| `get_signals` | `(kite, universe_df, capital, config, current_vix=12.0, risk_pct=0.25, max_positions=3) → list[dict]` | list of signals | Calls `scan_candidates` + `calculate_position_size`. |

---

## `orders/order_manager.py`

**All functions accept `dry_run: bool`. When `True`, returns a fake `DRY_XXXXXXXX` ID without calling the API. Any new order function MUST follow this pattern.**

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `place_entry_order` | `(kite, signal: dict, dry_run=True) → str \| None` | order_id or `None` | MIS MARKET BUY. `None` = API failure. |
| `place_sl_order` | `(kite, symbol, qty, trigger_price, dry_run=True) → str \| None` | order_id or `None` | MIS SL-Market SELL. SL-Market not SL-Limit — guarantees fill on gaps. |
| `place_target_order` | `(kite, symbol, qty, target_price, dry_run=True) → str \| None` | order_id or `None` | MIS LIMIT SELL. |
| `place_partial_target_order` | `(kite, symbol, partial_qty, partial_target, dry_run=True) → str \| None` | order_id or `None` | MIS LIMIT SELL for partial profit booking. |
| `cancel_order` | `(kite, order_id, variety="regular", dry_run=True) → bool` | `bool` | Returns `True` for `DRY_` IDs regardless of `dry_run`. |
| `modify_sl_order` | `(kite, order_id, new_trigger_price, qty, dry_run=True) → bool` | `bool` | Short-circuits to `True` for dry-run or `DRY_` IDs. |
| `get_order_status` | `(kite, order_id) → str` | `str` | Returns `"COMPLETE"` for `DRY_` IDs. `"UNKNOWN"` on failure. |
| `get_open_positions` | `(kite) → pd.DataFrame` | DataFrame | Net MIS positions, non-zero quantity only. |
| `get_all_orders_today` | `(kite) → pd.DataFrame` | DataFrame | All today's orders. |
| `place_market_sell` | `(kite, symbol, qty, dry_run=True) → str \| None` | order_id or `None` | Unconditional MIS MARKET SELL. Used for EOD close and SL-failure abort. |

---

## `monitor/position_monitor.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `check_kill_switch` | `(realized_pnl, unrealized_pnl, daily_capital) → bool` | `bool` | `True` if `(realized + unrealized) <= -(daily_capital × MAX_DAILY_LOSS_PCT)` |
| `check_profit_lock` | `(realized_pnl, unrealized_pnl, daily_capital) → bool` | `bool` | `True` if `(realized + unrealized) >= (daily_capital × PROFIT_LOCK_PCT)` |
| `_time_decay_sl` | `(trade: dict, current_price: float) → float \| None` | `float` | Returns new SL based on time: breakeven>12:00, tighten>13:30, aggressive>14:30. |
| `trail_stop_loss` | `(kite, trade: dict, current_price, dry_run=True) → float` | `float` | Stage 1: SL → breakeven at +1 ATR. Stage 2: SL → entry+0.5 ATR at +1.5 ATR profit. Also includes time-decay. |
| `run_monitor_cycle` | `(kite, open_trades, daily_capital, realized_pnl, conn, trade_date, dry_run=True) → tuple[dict, float, bool, bool]` | `(open_trades, realized_pnl, kill, lock)` | Fetches LTP, checks fills, trails SL, checks kill-switch/profit-lock. |
| `print_live_dashboard` | `(open_trades, realized_pnl, daily_capital, strategy="") → None` | `None` | Tabulated console display: entry, LTP, SL, target, unrealized P&L, day totals. |

---

## `eod/eod_closer.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `cancel_all_open_orders` | `(kite, dry_run=True) → list[str]` | list of cancelled IDs | Cancels `OPEN`, `TRIGGER PENDING`, `AMO REQ RECEIVED` orders. **Must run before `close_all_positions`.** |
| `close_all_positions` | `(kite, dry_run=True) → dict` | `{"closed": [...], "errors": [...]}` | MARKET SELL for every non-zero MIS position. |
| `run_eod_close` | `(kite, conn, open_trades, trade_date, dry_run=True) → dict` | summary dict | Full sequence: cancel orders → close positions → `record_trade_exit` → `update_daily_pnl`. |

---

## `ledger/db.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_connection` | `(db_path: Path = None) → sqlite3.Connection` | `Connection` | WAL mode, `row_factory=sqlite3.Row`. Creates parent dirs. |
| `initialize_db` | `(db_path: Path = None) → None` | `None` | `CREATE TABLE IF NOT EXISTS` for all 5 tables. Idempotent. |

---

## `ledger/tracker.py`

| Function | Signature | Returns | Notes |
|---|---|---|---|
| `get_today_capital` | `(conn) → float` | `float` | Idempotent. Carries forward most recent `closing_capital` (non-null, >0). Falls back to `SEED_CAPITAL` only on first-ever run. |
| `is_recovery_mode` | `(conn) → bool` | `bool` | `True` if most recent completed day had negative `realized_pnl`. |
| `record_trade_entry` | `(conn, trade_date, symbol, order_id, sl_order_id, target_order_id, direction, quantity, entry_price, stop_loss, target_price, strategy, rationale) → int` | `int` (row id) | Inserts OPEN trade. Increments `num_trades`. |
| `record_trade_exit` | `(conn, trade_id: int, exit_price: float, status: str) → float` | `float` (P&L) | Updates trade row. Increments `win_trades` or `loss_trades`. |
| `update_daily_pnl` | `(conn, trade_date: str) → float` | `float` | Sums all non-OPEN P&Ls. Updates `daily_capital` closing fields. |
| `record_eod_compound` | `(conn) → float` | `float` | Logs `EOD_COMPOUND` or `LOSS_DAY` to `capital_log`. Returns `closing_capital`. |
| `reconcile_previous_day` | `(conn, kite) → None` | `None` | Fixes stale `OPEN` trades from prior days using Kite order history. |
| `get_recent_win_rate` | `(conn, lookback=10) → tuple` | `(rate, wins, total)` | Returns win rate for the last `lookback` completed trades. |
| `get_adaptive_risk_pct` | `(conn, base_risk_pct) → float` | `float` | Dynamically adjusts risk % based on recent win rate (hot/cold streaks). |
| `print_daily_summary` | `(conn, trade_date: str) → None` | `None` | Full tabulated console summary: capital, P&L %, strategy, W/L, per-trade breakdown. |

---

## `main.py`

**Global state** (shared across all jobs — single-threaded, no locks needed):

| Variable | Type | Purpose |
|---|---|---|
| `_kite` | `KiteConnect\|None` | Authenticated KiteConnect instance |
| `_conn` | `Connection\|None` | SQLite connection |
| `_universe_df` | `DataFrame\|None` | Filtered universe from `filter_universe()` |
| `_strategy_name` | `str\|None` | `"momentum"`, `"mean_reversion"`, `"range_trading"`, `"skip"` |
| `_strategy_config` | `dict` | Full config dict from `select_strategy()` |
| `_open_trades` | `dict` | `{db_trade_id: trade_dict}` for all live trades |
| `_daily_capital` | `float` | Opening capital for today |
| `_realized_pnl` | `float` | Cumulative realized P&L today |
| `_ai_signals` | `list` | Pre-fetched AI signals from morning call |
| `_premarket_ctx` | `dict` | Pre-market intelligence dict |
| `_adaptive_risk` | `float` | Base risk adjusted by recent win rate |
| `_entries_stopped` | `bool` | Set True by kill-switch/profit-lock |
| `_eod_done` | `bool` | Set True after EOD close runs |
| `_shutdown_done` | `bool` | Prevents double-close on Ctrl+C |

**Scheduled jobs:**

| Function | Trigger | Description |
|---|---|---|
| `job_premarket` | 09:00 daily | Auth, DB init, reconcile, load capital |
| `job_market_open` | 09:15 daily | Universe filter, strategy selection |
| `job_entry_scan` | 09:25/30/45, 10:00/15 | Signal generation + order placement |
| `job_monitor` | Every 60s | Position management loop |
| `job_eod_close` | 15:15 daily | Force square-off |
| `job_shutdown` | 15:30 daily | EOD ledger + exit |

**Helpers:** `setup_logging`, `is_trading_day`, `main`, `_now_ist`, `_today_str`, `_safe_close_conn`, `_execute_signal`, `_trigger_emergency_close`
