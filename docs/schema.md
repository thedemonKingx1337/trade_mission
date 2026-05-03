# Schema, Signal Format & Environment Variables — Trade Mission

> Back to: [AGENTS.md](../AGENTS.md)

---

## SQLite Schema

Database file: `ledger/trades.db` (WAL mode, `row_factory=sqlite3.Row`).
Created by `ledger/db.py → initialize_db()`. All 5 tables use `CREATE TABLE IF NOT EXISTS` — safe to call repeatedly.

```sql
-- One row per trading day
CREATE TABLE daily_capital (
    trade_date      TEXT PRIMARY KEY,  -- 'YYYY-MM-DD'
    opening_capital REAL NOT NULL,
    realized_pnl    REAL DEFAULT 0.0,
    closing_capital REAL,              -- NULL until EOD
    strategy_used   TEXT,              -- 'momentum'|'mean_reversion'|'range_trading'|'skip'
    strategy_scores TEXT,              -- JSON blob
    num_trades      INTEGER DEFAULT 0,
    win_trades      INTEGER DEFAULT 0,
    loss_trades     INTEGER DEFAULT 0,
    recovery_mode   INTEGER DEFAULT 0, -- 1 if previous day was a loss
    notes           TEXT               -- 'KILL_SWITCH'|'PROFIT_LOCK' if triggered
);

-- One row per order placed
CREATE TABLE trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    order_id        TEXT,              -- KiteConnect ID or 'DRY_XXXXXXXX'
    sl_order_id     TEXT,
    target_order_id TEXT,
    direction       TEXT NOT NULL,     -- 'BUY' only (long-only bot)
    quantity        INTEGER NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    target_price    REAL,
    status          TEXT DEFAULT 'OPEN',  -- OPEN|CLOSED|SL_HIT|TARGET_HIT|EOD_CLOSE
    pnl             REAL,
    strategy        TEXT,
    rationale       TEXT,
    entry_time      TEXT,
    exit_time       TEXT
);

-- Running capital log — every money event
CREATE TABLE capital_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date    TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- SEED|EOD_COMPOUND|LOSS_DAY
    amount      REAL NOT NULL,   -- P&L amount (positive or negative)
    balance     REAL NOT NULL,   -- balance AFTER this event
    notes       TEXT
);

-- Strategy selection log — one row per selection event
CREATE TABLE strategy_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    log_time         TEXT NOT NULL,
    momentum_score   REAL,
    mean_rev_score   REAL,
    range_score      REAL,
    selected         TEXT NOT NULL,
    market_context   TEXT   -- JSON: {vix, nifty_ltp, nifty_gap_pct, premarket, ...}
);

-- Schema versioning (reserved for future migrations)
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);
```

### Useful ad-hoc queries

```bash
# Check today's capital and P&L
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT * FROM daily_capital ORDER BY trade_date DESC LIMIT 7').fetchall():
    print(dict(r))
"

# List all trades today
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT symbol, strategy, direction, quantity, entry_price, exit_price, pnl, status FROM trades ORDER BY id DESC LIMIT 20').fetchall():
    print(dict(r))
"

# Strategy selection history
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT trade_date, selected, momentum_score, mean_rev_score, range_score FROM strategy_log').fetchall():
    print(dict(r))
"

# Capital compounding log
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT * FROM capital_log ORDER BY id').fetchall():
    print(dict(r))
"
```

---

## Signal Dict Format

Every `get_signals()` returns a list of these dicts. `order_manager.py` and `main._execute_signal()` consume them directly.

```python
{
    # Required fields — present in all strategies
    "symbol":           "RELIANCE",     # NSE trading symbol
    "exchange":         "NSE",
    "instrument_token": 738561,         # from instruments cache
    "direction":        "BUY",          # always BUY (long-only bot)
    "entry_price":      2450.50,
    "stop_loss":        2425.00,
    "target_price":     2501.00,
    "atr14":            25.50,          # ATR(14) from daily candles
    "quantity":         4,              # set by calculate_position_size()
    "strategy":         "momentum",     # "momentum"|"mean_reversion"|"range_trading"
    "rationale":        "Gap-up 2.1%, vol_ratio 2.4x, broke OR high 2448.00",
    "score":            0.82,           # 0.0–1.0 signal confidence

    # Strategy-specific extras (only present for that strategy)
    "gap_pct":          2.1,            # momentum only
    "vol_ratio":        2.4,            # momentum only
    "rsi":              30.5,           # mean_reversion only
    "reversal_pattern": "hammer",       # mean_reversion only
    "range_high":       2480.00,        # range_trading only
    "range_low":        2440.00,        # range_trading only
}
```

After `_execute_signal()` in `main.py`, the dict stored in `_open_trades` also gains:
```python
{
    ...signal fields...,
    "order_id":                "DRY_1234",      # or real KiteConnect order ID
    "sl_order_id":             "DRY_2345",
    "target_order_id":         "DRY_3456",
    "partial_target_order_id": "DRY_4567",      # None if qty < 2
    "partial_qty":             2,
    "remaining_qty":           2,
    "partial_target_price":    2476.00,         # 1x ATR
    "partial_booked":          False,
    "current_price":           2450.50,         # updated each monitor cycle
    "unrealized_pnl":          0.0,             # updated each monitor cycle
}
```

---

## `.env` Variables Reference

File location: `trade_mission/.env` (copy from `.env.example`, never commit).

| Variable | Default | Description |
|---|---|---|
| `KITE_API_KEY` | — | From kite.trade developer console |
| `KITE_API_SECRET` | — | From kite.trade developer console |
| `KITE_USER_ID` | — | Zerodha user ID (e.g. ZJ1234) |
| `KITE_PASSWORD` | — | Zerodha login password |
| `KITE_TOTP_SECRET` | — | Base32 TOTP secret (leave blank for manual OTP) |
| `SEED_CAPITAL` | `1000.0` | One-time seed amount in Rs — used on first run only |
| `RISK_PER_TRADE_PCT` | `0.25` | Fraction of daily capital risked per trade (25%) |
| `MAX_OPEN_POSITIONS` | `3` | Max simultaneous open trades |
| `MAX_DAILY_LOSS_PCT` | `0.04` | Kill-switch threshold — 4% of opening capital |
| `PROFIT_LOCK_PCT` | `0.08` | Profit-lock threshold — 8% of opening capital |
| `MIS_LEVERAGE` | `3` | Intraday leverage cap for position sizing |
| `DB_PATH` | `ledger/trades.db` | SQLite DB file location |
| `TOKEN_PATH` | `auth/.session_token` | Daily KiteConnect session token location |
| `DRY_RUN` | `true` | **Must be `false` to place real orders** |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`) |
| `ANTHROPIC_API_KEY` | `""` | Anthropic API key from console.anthropic.com. Blank = Claude disabled. |
| `CLAUDE_TRADE_MODEL` | `claude-opus-4-7` | Claude model for morning trade decisions. |
| `CLAUDE_MONITOR_MODEL` | `claude-sonnet-4-6` | Claude model for mid-session position advice. |
| `NEWS_FETCH_ENABLED` | `true` | Enable Google News RSS fetching. |
| `NEWS_MAX_HEADLINES` | `15` | Max number of headlines to fetch. |
| `NEWS_MAX_AGE_HOURS` | `24` | Only fetch news from the last N hours. |
