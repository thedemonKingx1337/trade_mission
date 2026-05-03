import sqlite3
from pathlib import Path
from config.settings import DB_PATH

_DDL = """
CREATE TABLE IF NOT EXISTS daily_capital (
    trade_date      TEXT PRIMARY KEY,
    opening_capital REAL NOT NULL,
    realized_pnl    REAL DEFAULT 0.0,
    closing_capital REAL,
    strategy_used   TEXT,
    strategy_scores TEXT,
    num_trades      INTEGER DEFAULT 0,
    win_trades      INTEGER DEFAULT 0,
    loss_trades     INTEGER DEFAULT 0,
    recovery_mode   INTEGER DEFAULT 0,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    order_id        TEXT,
    sl_order_id     TEXT,
    target_order_id TEXT,
    direction       TEXT NOT NULL,
    quantity        INTEGER NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    stop_loss       REAL,
    target_price    REAL,
    status          TEXT DEFAULT 'OPEN',
    pnl             REAL,
    strategy        TEXT,
    rationale       TEXT,
    entry_time      TEXT,
    exit_time       TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS capital_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date    TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    amount      REAL NOT NULL,
    balance     REAL NOT NULL,
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS strategy_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date       TEXT NOT NULL,
    log_time         TEXT NOT NULL,
    momentum_score   REAL,
    mean_rev_score   REAL,
    range_score      REAL,
    selected         TEXT NOT NULL,
    market_context   TEXT,
    created_at       TEXT DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""


def get_connection(db_path: Path = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_db(db_path: Path = None) -> None:
    conn = get_connection(db_path)
    conn.executescript(_DDL)
    conn.commit()
    conn.close()
