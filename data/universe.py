import logging
from datetime import datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import IST
from data.market_data import fetch_quotes, get_daily_candles, get_instrument_token, _load_instruments

logger = logging.getLogger(__name__)


def get_base_universe(kite: KiteConnect) -> list[str]:
    df = _load_instruments(kite)
    if df.empty:
        return []
    
    # Filter for active NSE equity instruments
    eq_df = df[
        (df["exchange"] == "NSE") & 
        (df["segment"] == "NSE") & 
        (df["instrument_type"] == "EQ")
    ]
    return eq_df["tradingsymbol"].tolist()


def filter_universe(kite: KiteConnect, symbols: list[str] = None) -> pd.DataFrame:
    if symbols is None:
        logger.info("Scanning 2,000+ NSE instruments...")
        symbols = get_base_universe(kite)

    quotes = fetch_quotes(kite, symbols)
    rows = []
    for sym in symbols:
        q = quotes.get(sym)
        if not q:
            continue
        ohlc = q.get("ohlc", {})
        prev_close = ohlc.get("close", 0)
        current = q.get("last_price", 0)
        volume = q.get("volume", 0)
        if prev_close <= 50 or current <= 0:  # Anti penny-stock trap
            continue
        if volume < 100_000:  # Strict liquidity trap filter
            continue
        gap_pct = (current - prev_close) / prev_close * 100
        token = get_instrument_token(kite, sym)
        rows.append({
            "symbol": sym,
            "instrument_token": token,
            "prev_close": prev_close,
            "current_price": current,
            "gap_pct": gap_pct,
            "volume": volume,
            "abs_gap": abs(gap_pct),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = df.sort_values("abs_gap", ascending=False).head(20).reset_index(drop=True)
    logger.info(f"Universe filtered to {len(df)} stocks (top by gap size).")
    return df


def get_premarket_snapshot(kite: KiteConnect, symbols: list[str] = None) -> pd.DataFrame:
    if symbols is None:
        symbols = get_base_universe(kite)

    rows = []
    for sym in symbols:
        daily = get_daily_candles(kite, sym, days=25)
        if daily.empty or len(daily) < 5:
            continue
        last = daily.iloc[-1]
        atr = _atr14(daily)
        avg_vol = daily["volume"].tail(20).mean()
        rows.append({
            "symbol": sym,
            "prev_close": last["close"],
            "prev_high": last["high"],
            "prev_low": last["low"],
            "atr14": atr,
            "avg_daily_volume": avg_vol,
            "52w_high": daily["high"].max(),
            "52w_low": daily["low"].min(),
        })

    return pd.DataFrame(rows)


def _atr14(df: pd.DataFrame) -> float:
    if len(df) < 2:
        return 0.0
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs(),
    ], axis=1).max(axis=1)
    return float(tr.tail(14).mean())
