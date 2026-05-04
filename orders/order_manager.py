import logging
import uuid

import pandas as pd
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


def _dry_order_id() -> str:
    return "DRY_" + uuid.uuid4().hex[:8].upper()


def place_entry_order(
    kite: KiteConnect,
    signal: dict,
    dry_run: bool = True,
) -> str | None:
    symbol = signal["symbol"]
    qty = signal["quantity"]
    price = signal.get("entry_price", 0)

    if dry_run:
        oid = _dry_order_id()
        logger.info(f"[DRY-RUN] BUY {qty} x {symbol} @ MARKET (entry~{price:.2f}) -> {oid}")
        return oid

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_BUY,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_MARKET,
        )
        logger.info(f"ENTRY ORDER placed: BUY {qty} x {symbol} -> order_id={order_id}")
        return str(order_id)
    except Exception as e:
        logger.error(f"Failed to place entry order for {symbol}: {e}")
        return None


def place_sl_order(
    kite: KiteConnect,
    symbol: str,
    qty: int,
    trigger_price: float,
    dry_run: bool = True,
) -> str | None:
    if dry_run:
        oid = _dry_order_id()
        logger.info(f"[DRY-RUN] SL-Market SELL {qty} x {symbol} @ trigger {trigger_price:.2f} -> {oid}")
        return oid

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_SLM,
            trigger_price=trigger_price,
        )
        logger.info(f"SL ORDER placed: SELL {qty} x {symbol} @ trigger {trigger_price:.2f} -> {order_id}")
        return str(order_id)
    except Exception as e:
        logger.error(f"Failed to place SL order for {symbol}: {e}")
        return None


def place_target_order(
    kite: KiteConnect,
    symbol: str,
    qty: int,
    target_price: float,
    dry_run: bool = True,
) -> str | None:
    if dry_run:
        oid = _dry_order_id()
        logger.info(f"[DRY-RUN] TARGET LIMIT SELL {qty} x {symbol} @ {target_price:.2f} -> {oid}")
        return oid

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=target_price,
        )
        logger.info(f"TARGET ORDER placed: SELL {qty} x {symbol} @ {target_price:.2f} -> {order_id}")
        return str(order_id)
    except Exception as e:
        logger.error(f"Failed to place target order for {symbol}: {e}")
        return None


def place_partial_target_order(
    kite: KiteConnect,
    symbol: str,
    partial_qty: int,
    partial_target: float,
    dry_run: bool = True,
) -> str | None:
    """
    Place a LIMIT SELL for partial quantity at intermediate target.
    Used for partial profit booking - sell 50% at 1×ATR, let rest run.
    """
    if partial_qty <= 0:
        return None

    if dry_run:
        oid = _dry_order_id()
        logger.info(
            f"[DRY-RUN] PARTIAL TARGET SELL {partial_qty} x {symbol} "
            f"@ {partial_target:.2f} -> {oid}"
        )
        return oid

    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=partial_qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_LIMIT,
            price=partial_target,
        )
        logger.info(
            f"PARTIAL TARGET ORDER placed: SELL {partial_qty} x {symbol} "
            f"@ {partial_target:.2f} -> {order_id}"
        )
        return str(order_id)
    except Exception as e:
        logger.error(f"Failed to place partial target for {symbol}: {e}")
        return None


def cancel_order(
    kite: KiteConnect,
    order_id: str,
    variety: str = "regular",
    dry_run: bool = True,
) -> bool:
    if order_id.startswith("DRY_") or dry_run:
        logger.info(f"[DRY-RUN] Cancel order {order_id}")
        return True
    try:
        kite.cancel_order(variety=variety, order_id=order_id)
        logger.info(f"Cancelled order {order_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to cancel order {order_id}: {e}")
        return False


def modify_sl_order(
    kite: KiteConnect,
    order_id: str,
    new_trigger_price: float,
    qty: int,
    dry_run: bool = True,
) -> bool:
    if order_id.startswith("DRY_") or dry_run:
        logger.info(f"[DRY-RUN] Modify SL order {order_id} -> trigger {new_trigger_price:.2f}")
        return True
    try:
        kite.modify_order(
            variety=kite.VARIETY_REGULAR,
            order_id=order_id,
            quantity=qty,
            order_type=kite.ORDER_TYPE_SLM,
            trigger_price=new_trigger_price,
        )
        logger.info(f"Modified SL order {order_id} -> new trigger {new_trigger_price:.2f}")
        return True
    except Exception as e:
        logger.error(f"Failed to modify SL order {order_id}: {e}")
        return False


def get_order_status(kite: KiteConnect, order_id: str) -> str:
    if order_id.startswith("DRY_"):
        return "COMPLETE"
    try:
        history = kite.order_history(order_id)
        return history[-1]["status"] if history else "UNKNOWN"
    except Exception as e:
        logger.error(f"Could not get status for {order_id}: {e}")
        return "UNKNOWN"


def get_open_positions(kite: KiteConnect) -> pd.DataFrame:
    try:
        positions = kite.positions()
        net = pd.DataFrame(positions.get("net", []))
        if net.empty:
            return pd.DataFrame()
        mis = net[(net["product"] == "MIS") & (net["quantity"] != 0)]
        return mis.reset_index(drop=True)
    except Exception as e:
        logger.error(f"Could not fetch positions: {e}")
        return pd.DataFrame()


def get_all_orders_today(kite: KiteConnect) -> pd.DataFrame:
    try:
        return pd.DataFrame(kite.orders())
    except Exception as e:
        logger.error(f"Could not fetch orders: {e}")
        return pd.DataFrame()


def place_market_sell(
    kite: KiteConnect,
    symbol: str,
    qty: int,
    dry_run: bool = True,
) -> str | None:
    if dry_run:
        oid = _dry_order_id()
        logger.info(f"[DRY-RUN] MARKET SELL {qty} x {symbol} -> {oid}")
        return oid
    try:
        order_id = kite.place_order(
            variety=kite.VARIETY_REGULAR,
            exchange=kite.EXCHANGE_NSE,
            tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL,
            quantity=qty,
            product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_MARKET,
        )
        logger.info(f"MARKET SELL {qty} x {symbol} -> {order_id}")
        return str(order_id)
    except Exception as e:
        logger.error(f"Failed to market sell {symbol}: {e}")
        return None
