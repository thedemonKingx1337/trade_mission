import json
import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo
from tabulate import tabulate

from config.settings import SEED_CAPITAL, IST
from ledger.db import get_connection

_IST = IST


def _today_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d")


def _now_str() -> str:
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")


# ── Capital ──────────────────────────────────────────────────────────────────

def get_today_capital(conn: sqlite3.Connection) -> float:
    """
    Returns opening capital for today.
    - If today's row already exists: return it (idempotent on multiple calls).
    - If previous day exists with a closing_capital: use that (daily compounding).
    - If no history at all: seed with SEED_CAPITAL (first day ever).
    Note: we do NOT reset to SEED_CAPITAL every Monday — the user seeds Rs1000
    only on the very first Monday. After that, profits compound continuously.
    """
    today = _today_str()
    row = conn.execute(
        "SELECT opening_capital FROM daily_capital WHERE trade_date = ?", (today,)
    ).fetchone()
    if row:
        return float(row["opening_capital"])

    # Look up most recent closing balance (skip rows where closing_capital is NULL)
    prev = conn.execute(
        """SELECT closing_capital FROM daily_capital
           WHERE closing_capital IS NOT NULL AND closing_capital > 0
           ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()

    if prev and prev["closing_capital"] is not None:
        capital = float(prev["closing_capital"])
    else:
        # First ever run — seed with SEED_CAPITAL
        capital = SEED_CAPITAL
        conn.execute(
            """INSERT OR IGNORE INTO capital_log
               (log_date, event_type, amount, balance, notes)
               VALUES (?, 'SEED', ?, ?, 'Initial seed capital Rs1000')""",
            (today, SEED_CAPITAL, SEED_CAPITAL),
        )

    recovery = 1 if is_recovery_mode(conn) else 0
    conn.execute(
        """INSERT OR IGNORE INTO daily_capital
           (trade_date, opening_capital, realized_pnl, recovery_mode)
           VALUES (?, ?, 0.0, ?)""",
        (today, capital, recovery),
    )
    conn.commit()
    return capital


def is_recovery_mode(conn: sqlite3.Connection) -> bool:
    """True if the most recent completed trading day ended with a loss."""
    row = conn.execute(
        """SELECT realized_pnl FROM daily_capital
           WHERE closing_capital IS NOT NULL
           ORDER BY trade_date DESC LIMIT 1"""
    ).fetchone()
    return bool(row and row["realized_pnl"] is not None and float(row["realized_pnl"]) < 0)


# ── Trade recording ──────────────────────────────────────────────────────────

def record_trade_entry(
    conn: sqlite3.Connection,
    trade_date: str,
    symbol: str,
    order_id: str,
    sl_order_id: str,
    target_order_id: str,
    direction: str,
    quantity: int,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    strategy: str,
    rationale: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO trades
           (trade_date,symbol,order_id,sl_order_id,target_order_id,
            direction,quantity,entry_price,stop_loss,target_price,
            strategy,rationale,status,entry_time)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'OPEN',?)""",
        (
            trade_date, symbol, order_id, sl_order_id, target_order_id,
            direction, quantity, entry_price, stop_loss, target_price,
            strategy, rationale, _now_str(),
        ),
    )
    conn.execute(
        "UPDATE daily_capital SET num_trades = num_trades + 1 WHERE trade_date = ?",
        (trade_date,),
    )
    conn.commit()
    return cur.lastrowid


def record_trade_exit(
    conn: sqlite3.Connection,
    trade_id: int,
    exit_price: float,
    status: str,
) -> float:
    row = conn.execute(
        "SELECT direction, quantity, entry_price FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    if not row:
        return 0.0
    pnl = (exit_price - row["entry_price"]) * row["quantity"]
    if row["direction"] == "SELL":
        pnl = -pnl
    conn.execute(
        "UPDATE trades SET exit_price=?,status=?,pnl=?,exit_time=? WHERE id=?",
        (exit_price, status, pnl, _now_str(), trade_id),
    )
    col = "win_trades" if pnl > 0 else "loss_trades"
    trade_date = conn.execute(
        "SELECT trade_date FROM trades WHERE id=?", (trade_id,)
    ).fetchone()["trade_date"]
    conn.execute(
        f"UPDATE daily_capital SET {col} = {col} + 1 WHERE trade_date = ?",
        (trade_date,),
    )
    conn.commit()
    return pnl


def update_daily_pnl(conn: sqlite3.Connection, trade_date: str) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(pnl),0) as total FROM trades WHERE trade_date=? AND status != 'OPEN'",
        (trade_date,),
    ).fetchone()
    realized = row["total"]
    cap_row = conn.execute(
        "SELECT opening_capital FROM daily_capital WHERE trade_date=?", (trade_date,)
    ).fetchone()
    closing = (cap_row["opening_capital"] if cap_row else 0.0) + realized
    conn.execute(
        "UPDATE daily_capital SET realized_pnl=?,closing_capital=? WHERE trade_date=?",
        (realized, closing, trade_date),
    )
    conn.commit()
    return realized


def record_eod_compound(conn: sqlite3.Connection) -> float:
    today = _today_str()
    row = conn.execute(
        "SELECT closing_capital, realized_pnl FROM daily_capital WHERE trade_date=?",
        (today,),
    ).fetchone()
    if not row:
        return SEED_CAPITAL
    closing = row["closing_capital"] or SEED_CAPITAL
    pnl = row["realized_pnl"] or 0.0
    event = "EOD_COMPOUND" if pnl >= 0 else "LOSS_DAY"
    conn.execute(
        "INSERT INTO capital_log (log_date,event_type,amount,balance,notes) VALUES (?,?,?,?,?)",
        (today, event, pnl, closing, f"EOD P&L: Rs{pnl:.2f}"),
    )
    conn.commit()
    return closing


def reconcile_previous_day(conn: sqlite3.Connection, kite) -> None:
    open_trades = conn.execute(
        "SELECT id, order_id, symbol FROM trades WHERE status='OPEN' AND trade_date < ?",
        (_today_str(),),
    ).fetchall()
    if not open_trades:
        return
    try:
        orders = {o["order_id"]: o for o in kite.orders() if o.get("order_id")}
    except Exception:
        return
    for trade in open_trades:
        order = orders.get(trade["order_id"])
        if order and order.get("status") == "COMPLETE":
            avg_price = float(order.get("average_price", 0))
            record_trade_exit(conn, trade["id"], avg_price, "CLOSED")
        else:
            record_trade_exit(conn, trade["id"], 0.0, "EOD_CLOSE")
    update_daily_pnl(conn, _today_str())


# ── Adaptive risk ─────────────────────────────────────────────────────────────

def get_recent_win_rate(
    conn: sqlite3.Connection,
    lookback: int = 10,
) -> tuple[float, int, int]:
    """
    Calculate win rate from the most recent N completed trades.

    Returns: (win_rate: float 0.0-1.0, wins: int, total: int)
    Returns (0.5, 0, 0) if no completed trades exist.
    """
    rows = conn.execute(
        """SELECT pnl FROM trades
           WHERE status != 'OPEN' AND pnl IS NOT NULL
           ORDER BY id DESC LIMIT ?""",
        (lookback,),
    ).fetchall()

    if not rows:
        return 0.5, 0, 0  # neutral assumption when no data

    total = len(rows)
    wins = sum(1 for r in rows if float(r["pnl"]) > 0)
    rate = wins / total
    return round(rate, 3), wins, total


def get_adaptive_risk_pct(
    conn: sqlite3.Connection,
    base_risk_pct: float,
) -> float:
    """
    Adjust risk percentage based on recent win rate.

    Hot streak (>60%): boost risk × 1.3
    Normal (40-60%): keep base risk
    Cold streak (<40%): reduce risk × 0.7
    Ice cold (<25%): minimal risk × 0.5

    Returns adjusted risk_pct (float).
    """
    from config.settings import (
        ADAPTIVE_RISK_ENABLED, ADAPTIVE_RISK_LOOKBACK,
        ADAPTIVE_RISK_HOT_THRESHOLD, ADAPTIVE_RISK_HOT_MULT,
        ADAPTIVE_RISK_COLD_THRESHOLD, ADAPTIVE_RISK_COLD_MULT,
        ADAPTIVE_RISK_ICE_THRESHOLD, ADAPTIVE_RISK_ICE_MULT,
    )

    if not ADAPTIVE_RISK_ENABLED:
        return base_risk_pct

    win_rate, wins, total = get_recent_win_rate(conn, ADAPTIVE_RISK_LOOKBACK)

    if total < 3:
        # Not enough data — use base risk
        return base_risk_pct

    import logging
    logger = logging.getLogger(__name__)

    if win_rate >= ADAPTIVE_RISK_HOT_THRESHOLD:
        adjusted = base_risk_pct * ADAPTIVE_RISK_HOT_MULT
        logger.info(
            f"Adaptive risk: HOT streak ({wins}/{total} = {win_rate:.0%}) "
            f"→ risk boosted {base_risk_pct:.2%} → {adjusted:.2%}"
        )
        return adjusted

    elif win_rate <= ADAPTIVE_RISK_ICE_THRESHOLD:
        adjusted = base_risk_pct * ADAPTIVE_RISK_ICE_MULT
        logger.warning(
            f"Adaptive risk: ICE-COLD streak ({wins}/{total} = {win_rate:.0%}) "
            f"→ risk reduced {base_risk_pct:.2%} → {adjusted:.2%}"
        )
        return adjusted

    elif win_rate <= ADAPTIVE_RISK_COLD_THRESHOLD:
        adjusted = base_risk_pct * ADAPTIVE_RISK_COLD_MULT
        logger.info(
            f"Adaptive risk: COLD streak ({wins}/{total} = {win_rate:.0%}) "
            f"→ risk reduced {base_risk_pct:.2%} → {adjusted:.2%}"
        )
        return adjusted

    else:
        logger.debug(f"Adaptive risk: NORMAL ({wins}/{total} = {win_rate:.0%}) → base risk")
        return base_risk_pct


# ── Reporting ─────────────────────────────────────────────────────────────────

def print_daily_summary(conn: sqlite3.Connection, trade_date: str) -> None:
    cap = conn.execute(
        "SELECT * FROM daily_capital WHERE trade_date=?", (trade_date,)
    ).fetchone()
    trades = conn.execute(
        "SELECT symbol,strategy,direction,quantity,entry_price,exit_price,stop_loss,target_price,status,pnl "
        "FROM trades WHERE trade_date=? ORDER BY id",
        (trade_date,),
    ).fetchall()

    opening = cap["opening_capital"] if cap else 0
    closing = cap["closing_capital"] if cap else 0
    pnl = cap["realized_pnl"] if cap else 0
    pct = (pnl / opening * 100) if opening else 0

    print("\n" + "=" * 60)
    print(f"  TRADE MISSION — Daily Summary  {trade_date}")
    print("=" * 60)
    print(f"  Opening Capital : Rs{opening:.2f}")
    print(f"  Closing Capital : Rs{closing:.2f}")
    pnl_str = f"+Rs{pnl:.2f}" if pnl >= 0 else f"-Rs{abs(pnl):.2f}"
    print(f"  Day P&L         : {pnl_str}  ({pct:+.2f}%)")
    print(f"  Strategy        : {cap['strategy_used'] if cap else 'N/A'}")
    print(f"  Trades          : {cap['num_trades'] if cap else 0}  "
          f"(W:{cap['win_trades'] if cap else 0} L:{cap['loss_trades'] if cap else 0})")
    if cap and cap["recovery_mode"]:
        print("  *** RECOVERY MODE was active today ***")
    print()

    if trades:
        rows = []
        for t in trades:
            pnl_t = t["pnl"] or 0
            rows.append([
                t["symbol"], t["strategy"], t["direction"], t["quantity"],
                f"{t['entry_price']:.2f}" if t["entry_price"] else "-",
                f"{t['exit_price']:.2f}" if t["exit_price"] else "-",
                f"{pnl_t:+.2f}",
                t["status"],
            ])
        print(tabulate(rows, headers=[
            "Symbol", "Strategy", "Dir", "Qty", "Entry", "Exit", "P&L", "Status"
        ], tablefmt="simple"))
    else:
        print("  No trades placed today.")
    print("=" * 60 + "\n")
