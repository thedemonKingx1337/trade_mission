import logging
import sqlite3
from datetime import datetime

from kiteconnect import KiteConnect

from config.settings import IST
from orders.order_manager import (
    get_open_positions, get_all_orders_today, cancel_order, place_market_sell,
)
from ledger.tracker import record_trade_exit, update_daily_pnl

logger = logging.getLogger(__name__)


def cancel_all_open_orders(kite: KiteConnect, dry_run: bool = True) -> list[str]:
    cancelled = []
    orders_df = get_all_orders_today(kite)
    if orders_df.empty:
        return cancelled
    open_statuses = {"OPEN", "TRIGGER PENDING", "AMO REQ RECEIVED"}
    open_orders = orders_df[orders_df["status"].isin(open_statuses)]
    for _, order in open_orders.iterrows():
        oid = str(order["order_id"])
        variety = order.get("variety", "regular")
        if cancel_order(kite, oid, variety, dry_run):
            cancelled.append(oid)
    logger.info(f"Cancelled {len(cancelled)} open orders.")
    return cancelled


def close_all_positions(
    kite: KiteConnect,
    dry_run: bool = True,
) -> dict:
    result = {"closed": [], "errors": []}
    positions = get_open_positions(kite)
    if positions.empty:
        logger.info("No open MIS positions to close.")
        return result
    for _, pos in positions.iterrows():
        symbol = pos["tradingsymbol"]
        qty = int(pos["quantity"])
        if qty == 0:
            continue
        oid = place_market_sell(kite, symbol, qty, dry_run)
        if oid:
            result["closed"].append({"symbol": symbol, "qty": qty, "order_id": oid})
            logger.info(f"EOD Close: SELL {qty} x {symbol}")
        else:
            result["errors"].append(symbol)
            logger.error(f"EOD Close FAILED for {symbol}")
    return result


def run_eod_close(
    kite: KiteConnect,
    conn: sqlite3.Connection,
    open_trades: dict,
    trade_date: str,
    dry_run: bool = True,
) -> dict:
    now = datetime.now(IST).strftime("%H:%M:%S")
    logger.warning(f"=== EOD CLOSE starting at {now} IST ===")

    # Step 1: Cancel all open/pending orders first (SL and target orders)
    cancelled = cancel_all_open_orders(kite, dry_run)

    # Step 2: Close all remaining MIS positions at market
    close_result = close_all_positions(kite, dry_run)

    # Step 3: Update DB for any trades still marked OPEN
    for trade_id, trade in open_trades.items():
        exit_price = trade.get("current_price", trade.get("entry_price", 0))
        record_trade_exit(conn, trade_id, exit_price, "EOD_CLOSE")

    update_daily_pnl(conn, trade_date)
    logger.warning(f"=== EOD CLOSE complete. Closed: {len(close_result['closed'])}, Errors: {len(close_result['errors'])} ===")

    return {
        "cancelled_orders": cancelled,
        "closed_positions": close_result["closed"],
        "errors": close_result["errors"],
    }
