import logging
from datetime import datetime, timedelta

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import IST

logger = logging.getLogger(__name__)

_instruments_cache: pd.DataFrame | None = None


def _load_instruments(kite: KiteConnect) -> pd.DataFrame:
    global _instruments_cache
    if _instruments_cache is not None:
        return _instruments_cache
    logger.info("Downloading NSE instruments master (once per session)...")
    try:
        instruments = kite.instruments("NSE")
        _instruments_cache = pd.DataFrame(instruments)
        logger.info(f"Loaded {len(_instruments_cache)} NSE instruments.")
    except Exception as e:
        logger.error(f"Failed to load instruments: {e}")
        _instruments_cache = pd.DataFrame()
    return _instruments_cache


def get_instrument_token(kite: KiteConnect, symbol: str, exchange: str = "NSE") -> int | None:
    df = _load_instruments(kite)
    if df.empty:
        return None
    match = df[(df["tradingsymbol"] == symbol) & (df["exchange"] == exchange)]
    if match.empty:
        logger.warning(f"Instrument not found: {symbol} on {exchange}")
        return None
    return int(match.iloc[0]["instrument_token"])


def fetch_ohlcv(
    kite: KiteConnect,
    instrument_token: int,
    interval: str,
    from_dt: datetime,
    to_dt: datetime,
) -> pd.DataFrame:
    try:
        records = kite.historical_data(instrument_token, from_dt, to_dt, interval)
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"Failed to fetch OHLCV for token {instrument_token}: {e}")
        return pd.DataFrame()


def fetch_ltp(kite: KiteConnect, symbols: list[str], exchange: str = "NSE") -> dict[str, float]:
    instruments = [f"{exchange}:{s}" for s in symbols]
    result = {}
    for i in range(0, len(instruments), 400):
        batch = instruments[i : i + 400]
        try:
            data = kite.ltp(batch)
            for key, val in data.items():
                sym = key.split(":", 1)[1]
                result[sym] = val["last_price"]
        except Exception as e:
            logger.error(f"LTP fetch failed for batch starting {i}: {e}")
    return result


def fetch_quotes(kite: KiteConnect, symbols: list[str], exchange: str = "NSE") -> dict:
    instruments = [f"{exchange}:{s}" for s in symbols]
    result = {}
    for i in range(0, len(instruments), 400):
        batch = instruments[i : i + 400]
        try:
            data = kite.quote(batch)
            for key, val in data.items():
                sym = key.split(":", 1)[1]
                result[sym] = val
        except Exception as e:
            logger.error(f"Quote fetch failed for batch starting {i}: {e}")
    return result


def get_today_candles(
    kite: KiteConnect,
    symbol: str,
    interval: str = "5minute",
    only_complete: bool = True,
) -> pd.DataFrame:
    """
    Fetch intraday candles for today from 09:00 IST until now.
    If only_complete=True, drops the last (currently forming) candle.
    """
    token = get_instrument_token(kite, symbol)
    if token is None:
        return pd.DataFrame()
    now = datetime.now(IST)
    from_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
    df = fetch_ohlcv(kite, token, interval, from_dt, now)
    if only_complete and len(df) > 1:
        df = df.iloc[:-1]  # drop the last incomplete candle
    return df


def get_daily_candles(kite: KiteConnect, symbol: str, days: int = 60) -> pd.DataFrame:
    token = get_instrument_token(kite, symbol)
    if token is None:
        return pd.DataFrame()
    now = datetime.now(IST)
    from_dt = now - timedelta(days=days + 10)  # buffer for weekends/holidays
    df = fetch_ohlcv(kite, token, "day", from_dt, now)
    # Drop today's incomplete candle from daily data
    if not df.empty:
        today_str = now.strftime("%Y-%m-%d")
        df = df[df["date"].dt.strftime("%Y-%m-%d") < today_str]
    return df.tail(days).reset_index(drop=True)


def get_nifty_vix(kite: KiteConnect) -> float:
    try:
        # India VIX token varies by API version; try both common formats
        for key in ["NSE:INDIA VIX", "NSE:INDIAVIX"]:
            data = kite.ltp([key])
            if data:
                val = list(data.values())[0].get("last_price", 0)
                if val > 0:
                    return float(val)
    except Exception:
        pass
    logger.warning("Could not fetch VIX — defaulting to 15.0")
    return 15.0


def get_nifty_ltp(kite: KiteConnect) -> float:
    try:
        for key in ["NSE:NIFTY 50", "NSE:NIFTY50"]:
            data = kite.ltp([key])
            if data:
                val = list(data.values())[0].get("last_price", 0)
                if val > 0:
                    return float(val)
    except Exception:
        pass
    logger.warning("Could not fetch Nifty LTP")
    return 0.0
