import logging
import sqlite3
from datetime import datetime

from tabulate import tabulate
from kiteconnect import KiteConnect

from config.settings import (
    IST, MAX_DAILY_LOSS_PCT, PROFIT_LOCK_PCT, SL_ATR_MULTIPLIER,
    TIME_DECAY_SL_ENABLED, TIME_DECAY_BREAKEVEN_AFTER,
    TIME_DECAY_TIGHTEN_AFTER, TIME_DECAY_AGGRESSIVE_AFTER,
)
from data.market_data import fetch_ltp
from orders.order_manager import (
    get_open_positions, get_order_status, modify_sl_order, place_market_sell,
)
from ledger.tracker import record_trade_exit, update_daily_pnl

logger = logging.getLogger(__name__)


def check_kill_switch(
    realized_pnl: float,
    unrealized_pnl: float,
    daily_capital: float,
) -> bool:
    total_loss = realized_pnl + unrealized_pnl
    threshold = -(daily_capital * MAX_DAILY_LOSS_PCT)
    if total_loss <= threshold:
        logger.warning(
            f"KILL-SWITCH TRIGGERED: total P&L Rs{total_loss:.2f} <= threshold Rs{threshold:.2f}"
        )
        return True
    return False


def check_profit_lock(
    realized_pnl: float,
    unrealized_pnl: float,
    daily_capital: float,
) -> bool:
    total_gain = realized_pnl + unrealized_pnl
    threshold = daily_capital * PROFIT_LOCK_PCT
    if total_gain >= threshold:
        logger.info(
            f"PROFIT LOCK TRIGGERED: total P&L Rs{total_gain:.2f} >= threshold Rs{threshold:.2f}"
        )
        return True
    return False


def _time_decay_sl(
    trade: dict,
    current_price: float,
) -> float | None:
    """
    Time-based stop-loss tightening. Returns new SL price or None if no change.

    As the trading day progresses, edge decays. We progressively tighten SL:
      After 12:00 IST: move SL to breakeven if profitable
      After 13:30 IST: tighten SL to entry + 0.3×ATR if profitable
      After 14:30 IST: exit losing trades, lock profitable ones aggressively
    """
    if not TIME_DECAY_SL_ENABLED:
        return None

    now_time = datetime.now(IST).time()
    entry = trade["entry_price"]
    current_sl = trade["stop_loss"]
    atr = trade.get("atr14", entry * 0.01)
    profit = current_price - entry

    # After 14:30 - aggressive mode
    if now_time >= TIME_DECAY_AGGRESSIVE_AFTER:
        if profit > 0:
            # Lock at entry + 0.5×ATR
            aggressive_sl = round(entry + 0.5 * atr, 2)
            if aggressive_sl > current_sl:
                logger.info(
                    f"Time-decay (14:30+): {trade['symbol']} locking profit "
                    f"SL -> {aggressive_sl:.2f}"
                )
                return aggressive_sl
        elif profit < -0.5 * atr:
            # Losing more than 0.5 ATR after 14:30 - recommend exit
            # We signal this by returning a SL above current price (triggers immediate exit)
            logger.warning(
                f"Time-decay (14:30+): {trade['symbol']} losing Rs{profit:.2f} "
                f"with <45min left - flagging for exit"
            )
            return round(current_price + 0.01, 2)  # triggers SL immediately

    # After 13:30 - tighten mode
    elif now_time >= TIME_DECAY_TIGHTEN_AFTER:
        if profit > 0.3 * atr:
            tighten_sl = round(entry + 0.3 * atr, 2)
            if tighten_sl > current_sl:
                logger.info(
                    f"Time-decay (13:30+): {trade['symbol']} tightening "
                    f"SL -> {tighten_sl:.2f}"
                )
                return tighten_sl

    # After 12:00 - breakeven mode
    elif now_time >= TIME_DECAY_BREAKEVEN_AFTER:
        if profit > 0:
            breakeven_sl = entry
            if breakeven_sl > current_sl:
                logger.info(
                    f"Time-decay (12:00+): {trade['symbol']} moving to breakeven "
                    f"SL -> {breakeven_sl:.2f}"
                )
                return breakeven_sl

    return None


def trail_stop_loss(
    kite: KiteConnect,
    trade: dict,
    current_price: float,
    dry_run: bool = True,
) -> float:
    entry = trade["entry_price"]
    current_sl = trade["stop_loss"]
    sl_order_id = trade.get("sl_order_id", "")
    atr = trade.get("atr14", (entry * 0.01))
    qty = trade["quantity"]

    new_sl = current_sl
    profit = current_price - entry

    # Move SL to breakeven at +1 ATR profit
    breakeven_sl = entry
    if profit >= atr and breakeven_sl > current_sl:
        new_sl = breakeven_sl

    # Trail to entry + 0.5 ATR once at +1.5 ATR profit
    trail_sl = round(entry + 0.5 * atr, 2)
    if profit >= 1.5 * atr and trail_sl > new_sl:
        new_sl = trail_sl

    # Time-decay SL (can override ATR-based trailing if tighter)
    time_sl = _time_decay_sl(trade, current_price)
    if time_sl is not None and time_sl > new_sl:
        new_sl = time_sl

    if new_sl > current_sl:
        logger.info(f"Trailing SL for {trade['symbol']}: {current_sl:.2f} -> {new_sl:.2f}")
        modify_sl_order(kite, sl_order_id, new_sl, qty, dry_run)
        return new_sl

    return current_sl


def _check_partial_fill(
    kite: KiteConnect,
    trade: dict,
    trade_id: int,
    conn: sqlite3.Connection,
    dry_run: bool,
) -> dict:
    """
    Check if partial profit target order was filled.
    If filled: reduce tracked quantity, move SL to breakeven, log profit.

    Returns updated trade dict.
    """
    partial_oid = trade.get("partial_target_order_id")
    if not partial_oid or trade.get("partial_booked"):
        return trade

    status = get_order_status(kite, partial_oid)
    if status == "COMPLETE":
        partial_qty = trade.get("partial_qty", 0)
        partial_target = trade.get("partial_target_price", trade["entry_price"])
        partial_pnl = (partial_target - trade["entry_price"]) * partial_qty

        logger.info(
            f"🎯 PARTIAL PROFIT BOOKED: {trade['symbol']} - "
            f"{partial_qty} shares @ {partial_target:.2f}, "
            f"P&L Rs{partial_pnl:+.2f}"
        )

        # Mark partial as booked
        trade["partial_booked"] = True
        trade["quantity"] = trade.get("remaining_qty", trade["quantity"])

        # Move SL to breakeven for remaining quantity
        entry = trade["entry_price"]
        current_sl = trade["stop_loss"]
        if entry > current_sl:
            logger.info(
                f"Moving SL to breakeven for {trade['symbol']} remaining "
                f"{trade['quantity']} shares: {current_sl:.2f} -> {entry:.2f}"
            )
            modify_sl_order(
                kite, trade.get("sl_order_id", ""), entry,
                trade["quantity"], dry_run,
            )
            trade["stop_loss"] = entry

    return trade


def run_monitor_cycle(
    kite: KiteConnect,
    open_trades: dict,
    daily_capital: float,
    realized_pnl: float,
    conn: sqlite3.Connection,
    trade_date: str,
    dry_run: bool = True,
) -> tuple[dict, float, bool, bool]:
    """
    Returns (updated_open_trades, realized_pnl, kill_switch_hit, profit_lock_hit)
    """
    if not open_trades:
        return open_trades, realized_pnl, False, False

    symbols = [t["symbol"] for t in open_trades.values()]
    ltp = fetch_ltp(kite, symbols)

    unrealized_pnl = 0.0
    trades_to_close = []

    for trade_id, trade in list(open_trades.items()):
        symbol = trade["symbol"]
        current_price = ltp.get(symbol, trade["entry_price"])
        trade["current_price"] = current_price

        # Check if SL or target orders were filled (live mode)
        if not dry_run:
            sl_status = get_order_status(kite, trade.get("sl_order_id", ""))
            tgt_status = get_order_status(kite, trade.get("target_order_id", ""))
            if sl_status == "COMPLETE":
                pnl = record_trade_exit(conn, trade_id, trade["stop_loss"], "SL_HIT")
                realized_pnl += pnl
                trades_to_close.append(trade_id)
                logger.info(f"{symbol}: SL hit @ {trade['stop_loss']:.2f}, P&L Rs{pnl:.2f}")
                continue
            if tgt_status == "COMPLETE":
                pnl = record_trade_exit(conn, trade_id, trade["target_price"], "TARGET_HIT")
                realized_pnl += pnl
                trades_to_close.append(trade_id)
                logger.info(f"{symbol}: Target hit @ {trade['target_price']:.2f}, P&L Rs{pnl:.2f}")
                continue

        # Check partial profit fill
        trade = _check_partial_fill(kite, trade, trade_id, conn, dry_run)
        open_trades[trade_id] = trade

        unrealized = (current_price - trade["entry_price"]) * trade["quantity"]
        unrealized_pnl += unrealized
        trade["unrealized_pnl"] = unrealized

        # Trail stop-loss for profitable positions (includes time-decay)
        new_sl = trail_stop_loss(kite, trade, current_price, dry_run)
        trade["stop_loss"] = new_sl

    for tid in trades_to_close:
        del open_trades[tid]

    update_daily_pnl(conn, trade_date)

    kill = check_kill_switch(realized_pnl, unrealized_pnl, daily_capital)
    lock = check_profit_lock(realized_pnl, unrealized_pnl, daily_capital)

    return open_trades, realized_pnl, kill, lock


def print_live_dashboard(
    open_trades: dict,
    realized_pnl: float,
    daily_capital: float,
    strategy: str = "",
) -> None:
    now = datetime.now(IST).strftime("%H:%M:%S")
    total_unrealized = sum(
        t.get("unrealized_pnl", 0) for t in open_trades.values()
    )
    total_pnl = realized_pnl + total_unrealized
    pnl_pct = total_pnl / daily_capital * 100 if daily_capital else 0

    rows = []
    for tid, t in open_trades.items():
        entry = t.get("entry_price", 0)
        curr = t.get("current_price", entry)
        unreal = t.get("unrealized_pnl", 0)
        partial = "✓" if t.get("partial_booked") else ""
        rows.append([
            t["symbol"],
            t.get("strategy", ""),
            t["quantity"],
            f"{entry:.2f}",
            f"{curr:.2f}",
            f"{t['stop_loss']:.2f}",
            f"{t['target_price']:.2f}",
            f"{unreal:+.2f}",
            partial,
        ])

    print(f"\n[{now}] Capital: Rs{daily_capital:.2f} | "
          f"Realized: Rs{realized_pnl:+.2f} | "
          f"Unrealized: Rs{total_unrealized:+.2f} | "
          f"Total P&L: Rs{total_pnl:+.2f} ({pnl_pct:+.1f}%) | "
          f"Strategy: {strategy}")

    if rows:
        print(tabulate(rows, headers=[
            "Symbol", "Strategy", "Qty", "Entry", "LTP", "SL", "Target", "Unreal P&L", "Part"
        ], tablefmt="simple"))
    else:
        print("  (No open positions)")
