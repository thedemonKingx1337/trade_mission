import logging

import numpy as np
import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)


def _require_columns(df: pd.DataFrame, cols: list[str]) -> bool:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        logger.debug(f"DataFrame missing columns: {missing}")
        return False
    return True


def _safe_last(series: pd.Series, default=float("nan")):
    """Return last non-NaN value, or default if series is empty after dropna."""
    s = series.dropna()
    return float(s.iloc[-1]) if not s.empty else default


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    if not _require_columns(df, ["close"]):
        return df
    df[f"rsi_{period}"] = ta.rsi(df["close"], length=period)
    return df


def add_ema(df: pd.DataFrame, periods: list[int] = None) -> pd.DataFrame:
    if periods is None:
        periods = [9, 21, 50]
    if not _require_columns(df, ["close"]):
        return df
    for p in periods:
        df[f"ema_{p}"] = ta.ema(df["close"], length=p)
    return df


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    if not _require_columns(df, ["high", "low", "close"]):
        return df
    df[f"atr_{period}"] = ta.atr(df["high"], df["low"], df["close"], length=period)
    return df


def add_vwap(df: pd.DataFrame) -> pd.DataFrame:
    if not _require_columns(df, ["high", "low", "close", "volume"]):
        return df
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    df["vwap"] = cum_tp_vol / cum_vol.replace(0, np.nan)
    return df


def add_bollinger_bands(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    if not _require_columns(df, ["close"]):
        return df
    bbands = ta.bbands(df["close"], length=period, std=std)
    if bbands is not None and not bbands.empty:
        # Access by column name suffix, not position — robust to pandas_ta version changes
        cols = bbands.columns.tolist()
        upper_col = next((c for c in cols if c.startswith("BBU")), None)
        mid_col   = next((c for c in cols if c.startswith("BBM")), None)
        lower_col = next((c for c in cols if c.startswith("BBL")), None)
        if upper_col:
            df["bb_upper"]  = bbands[upper_col]
            df["bb_middle"] = bbands[mid_col]
            df["bb_lower"]  = bbands[lower_col]
    return df


def add_volume_metrics(df: pd.DataFrame, avg_period: int = 20) -> pd.DataFrame:
    """
    vol_sma: rolling N-period average of volume within the provided candles.
    vol_ratio: current candle volume / rolling average (meaningful once avg_period rows exist).
    Note: for accurate ratios on intraday candles, pass a multi-day DataFrame that
    includes historical intraday candles, not just today's candles.
    """
    if not _require_columns(df, ["volume"]):
        return df
    df["vol_sma"] = df["volume"].rolling(avg_period, min_periods=avg_period).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma"].replace(0, np.nan)
    return df


def add_daily_vol_ratio(df: pd.DataFrame, daily_avg_volume: float) -> pd.DataFrame:
    """
    Compute vol_ratio relative to a known daily average volume (from daily candles).
    This is the correct volume surge check for intraday data.
    daily_avg_volume: 20-day average of full-day volume for this stock.
    vol_ratio = today's first-candle volume / (daily_avg_volume / 75)
    (75 = approximate number of 5-min candles in a trading session)
    """
    if not _require_columns(df, ["volume"]):
        return df
    candles_per_day = 75  # 6h15m session / 5min
    avg_per_candle = daily_avg_volume / candles_per_day if daily_avg_volume > 0 else 1
    df["vol_ratio"] = df["volume"] / avg_per_candle
    return df


def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df = add_rsi(df)
    df = add_ema(df)
    df = add_atr(df)
    df = add_vwap(df)
    df = add_bollinger_bands(df)
    df = add_volume_metrics(df)
    return df


def is_gap_up(current_price: float, prev_close: float, min_gap_pct: float = 1.5) -> bool:
    if prev_close <= 0:
        return False
    return (current_price - prev_close) / prev_close * 100 >= min_gap_pct


def is_gap_down(current_price: float, prev_close: float, min_gap_pct: float = 1.5) -> bool:
    if prev_close <= 0:
        return False
    return (prev_close - current_price) / prev_close * 100 >= min_gap_pct


def detect_reversal_candle(df: pd.DataFrame) -> str | None:
    """
    Detect reversal candle on the LAST COMPLETED candle (df.iloc[-1]).
    Caller must ensure the last row is a completed candle (use only_complete=True
    in get_today_candles).
    """
    if len(df) < 2:
        return None
    c = df.iloc[-1]
    p = df.iloc[-2]
    o, h, lo, cl = float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])
    candle_range = h - lo
    if candle_range <= 0:
        return None
    body = abs(cl - o)
    lower_shadow = min(o, cl) - lo
    upper_shadow = h - max(o, cl)

    # Hammer: small body in upper third, long lower shadow, bullish close
    if (
        cl >= o
        and body > 0
        and lower_shadow >= 2 * body
        and cl >= (lo + candle_range * 0.60)
        and upper_shadow <= body * 1.5
    ):
        return "hammer"

    # Bullish engulfing: current body fully engulfs previous candle body, bullish
    p_body_lo = min(float(p["open"]), float(p["close"]))
    p_body_hi = max(float(p["open"]), float(p["close"]))
    if cl > o and o <= p_body_lo and cl >= p_body_hi and (p_body_hi - p_body_lo) > 0:
        return "engulfing"

    # Doji near support: body <= 10% of range, long lower shadow >= 50% of range
    if body <= candle_range * 0.10 and lower_shadow >= candle_range * 0.50:
        return "doji"

    return None


def get_opening_range(candles_df: pd.DataFrame) -> dict:
    """Returns OR stats from the provided candles (caller decides which candles = OR period)."""
    if candles_df.empty:
        return {}
    high = float(candles_df["high"].max())
    low  = float(candles_df["low"].min())
    return {
        "or_high": high,
        "or_low":  low,
        "or_open": float(candles_df.iloc[0]["open"]),
        "or_range": high - low,
    }
