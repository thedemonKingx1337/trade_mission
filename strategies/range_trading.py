import logging
from datetime import datetime

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import RANGE_MAX_VIX, IST
from data.market_data import get_today_candles, get_daily_candles
from indicators.technicals import compute_all, _safe_last
from utils.position_sizing import calculate_position_size

logger = logging.getLogger(__name__)

# Range trading needs at least 2 completed 15-minute candles (= 30 min into session)
_MIN_RANGE_CANDLES = 2


def establish_range(kite: KiteConnect, symbol: str, config: dict) -> dict | None:
    candles = get_today_candles(kite, symbol, interval="15minute", only_complete=True)
    if candles.empty or len(candles) < _MIN_RANGE_CANDLES:
        return None

    range_candles = candles.iloc[:_MIN_RANGE_CANDLES]
    r_high = float(range_candles["high"].max())
    r_low  = float(range_candles["low"].min())
    r_width = r_high - r_low
    mid = (r_high + r_low) / 2
    if mid <= 0:
        return None

    max_range_pct = config.get("max_range_pct", 1.5)
    if r_width / mid * 100 > max_range_pct:
        return None  # range too wide - not a range day for this stock

    last_price = float(candles.iloc[-1]["close"])
    return {
        "range_high":    round(r_high, 2),
        "range_low":     round(r_low, 2),
        "range_mid":     round(mid, 2),
        "range_width":   round(r_width, 2),
        "current_price": round(last_price, 2),
    }


def scan_candidates(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    config: dict,
    current_vix: float = 12.0,
) -> list[dict]:
    max_vix = config.get("max_vix", RANGE_MAX_VIX)
    if current_vix > max_vix:
        logger.info(f"VIX {current_vix:.1f} > {max_vix} - range trading suppressed")
        return []

    # Only run range after the first 30 minutes have completed
    now = datetime.now(IST)
    if now.time().hour == 9 and now.time().minute < 45:
        logger.debug("Range trading: waiting for first 30 min of session to complete")
        return []

    candidates = []
    for _, row in universe_df.iterrows():
        symbol = row["symbol"]
        try:
            r = establish_range(kite, symbol, config)
            if r is None:
                continue

            current_price = r["current_price"]
            range_low     = r["range_low"]
            range_high    = r["range_high"]
            range_width   = r["range_width"]

            # Only enter in the bottom 20% of the range
            entry_zone_top = range_low + (range_width * 0.20)
            if current_price > entry_zone_top:
                continue

            entry_price = current_price
            stop_loss   = round(range_low * 0.995, 2)   # 0.5% below range low
            target      = round(range_high * 0.990, 2)  # 1% below range high

            if target <= entry_price:
                continue

            # Real ATR from daily data (not range_width)
            daily_df = get_daily_candles(kite, symbol, days=22)
            atr14 = range_width  # fallback
            if not daily_df.empty and len(daily_df) >= 5:
                enriched_daily = compute_all(daily_df)
                atr_val = _safe_last(enriched_daily.get("atr_14", pd.Series(dtype=float)))
                if not pd.isna(atr_val) and atr_val > 0:
                    atr14 = atr_val

            # Minimum R:R of 1.5:1
            sl_dist = entry_price - stop_loss
            rr = (target - entry_price) / sl_dist if sl_dist > 0 else 0
            if rr < 1.5:
                logger.debug(f"{symbol}: range R:R {rr:.2f} < 1.5, skipping")
                continue

            score = round(min(1.0, 1.0 - (current_vix / max_vix) * 0.4), 3)

            candidates.append({
                "symbol":           symbol,
                "exchange":         "NSE",
                "instrument_token": row.get("instrument_token"),
                "direction":        "BUY",
                "entry_price":      round(entry_price, 2),
                "stop_loss":        stop_loss,
                "target_price":     target,
                "atr14":            round(atr14, 2),
                "range_high":       range_high,
                "range_low":        range_low,
                "strategy":         "range_trading",
                "rationale": (
                    f"Range {range_low:.2f}–{range_high:.2f}, "
                    f"VIX {current_vix:.1f}, R:R {rr:.1f}:1"
                ),
                "score": score,
            })

        except Exception as e:
            logger.warning(f"Error scanning {symbol} for range: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_signals(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    capital: float,
    config: dict,
    current_vix: float = 12.0,
    risk_pct: float = 0.25,
    max_positions: int = 3,
) -> list[dict]:
    candidates = scan_candidates(kite, universe_df, config, current_vix)
    signals = []
    for c in candidates[:max_positions]:
        qty = calculate_position_size(capital, c["entry_price"], c["stop_loss"], risk_pct)
        if qty <= 0:
            logger.warning(f"{c['symbol']}: qty=0 for range trade")
            continue
        c["quantity"] = qty
        signals.append(c)
    return signals
