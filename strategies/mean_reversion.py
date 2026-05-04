import logging

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import (
    MEAN_REV_RSI_OVERSOLD, MEAN_REV_MAX_VIX,
    TARGET_ATR_MULTIPLIER,
)
from data.market_data import get_today_candles
from indicators.technicals import compute_all, detect_reversal_candle, _safe_last
from utils.position_sizing import calculate_position_size

logger = logging.getLogger(__name__)


def scan_candidates(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    config: dict,
    current_vix: float = 15.0,
) -> list[dict]:
    rsi_threshold = config.get("rsi_oversold_threshold", MEAN_REV_RSI_OVERSOLD)
    max_vix       = config.get("max_vix",                  MEAN_REV_MAX_VIX)

    if current_vix > max_vix:
        logger.info(f"VIX {current_vix:.1f} > {max_vix} - mean reversion suppressed")
        return []

    candidates = []
    for _, row in universe_df.iterrows():
        symbol = row["symbol"]
        try:
            # Only completed 15-minute candles
            candles_15m = get_today_candles(
                kite, symbol, interval="15minute", only_complete=True
            )
            if candles_15m.empty or len(candles_15m) < 3:
                logger.debug(f"{symbol}: insufficient candles for mean reversion")
                continue

            enriched = compute_all(candles_15m)

            # RSI check - guard against all-NaN series
            last_rsi = _safe_last(enriched.get("rsi_14", pd.Series(dtype=float)))
            if pd.isna(last_rsi) or last_rsi > rsi_threshold:
                continue

            last_price = float(candles_15m.iloc[-1]["close"])

            # EMA-50 support check - guard against insufficient history
            ema50 = _safe_last(enriched.get("ema_50", pd.Series(dtype=float)))
            if pd.isna(ema50):
                logger.debug(f"{symbol}: EMA-50 not available yet")
                continue
            if abs(last_price - ema50) / ema50 > 0.015:  # within 1.5% of EMA-50
                logger.debug(f"{symbol}: price not near EMA-50 ({last_price:.2f} vs {ema50:.2f})")
                continue

            # Reversal candle on last completed candle
            reversal = detect_reversal_candle(enriched)
            if reversal is None:
                logger.debug(f"{symbol}: no reversal candle")
                continue

            # ATR - guard against insufficient history
            atr14 = _safe_last(enriched.get("atr_14", pd.Series(dtype=float)))
            if pd.isna(atr14) or atr14 <= 0:
                atr14 = last_price * 0.01  # fallback: 1% of price

            # EMA-21 for target
            ema21 = _safe_last(enriched.get("ema_21", pd.Series(dtype=float)))
            if pd.isna(ema21):
                ema21 = last_price * 1.02  # fallback: 2% above entry

            entry_price = last_price
            reversal_candle_low = float(candles_15m.iloc[-1]["low"])
            stop_loss   = round(reversal_candle_low * 0.998, 2)  # 0.2% buffer
            target      = round(ema21, 2)

            # Reward must be at least 1x ATR (i.e. better than 1:1 R:R)
            if (target - entry_price) < atr14:
                logger.debug(
                    f"{symbol}: target {target:.2f} gives <1 ATR reward, skipping"
                )
                continue

            # R:R ratio
            sl_dist = entry_price - stop_loss
            rr = (target - entry_price) / sl_dist if sl_dist > 0 else 0
            if rr < 1.0:
                continue

            score = round(
                min(1.0, (rsi_threshold - last_rsi) / 20.0 * 0.7 + 0.3),
                3,
            )

            candidates.append({
                "symbol":            symbol,
                "exchange":          "NSE",
                "instrument_token":  row.get("instrument_token"),
                "direction":         "BUY",
                "entry_price":       round(entry_price, 2),
                "stop_loss":         stop_loss,
                "target_price":      target,
                "atr14":             round(atr14, 2),
                "rsi":               round(last_rsi, 1),
                "reversal_pattern":  reversal,
                "strategy":          "mean_reversion",
                "rationale": (
                    f"RSI {last_rsi:.1f} oversold, {reversal} candle near "
                    f"EMA-50 {ema50:.2f}, R:R {rr:.1f}:1"
                ),
                "score": score,
            })

        except Exception as e:
            logger.warning(f"Error scanning {symbol} for mean reversion: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_signals(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    capital: float,
    config: dict,
    current_vix: float = 15.0,
    risk_pct: float = 0.25,
    max_positions: int = 3,
) -> list[dict]:
    candidates = scan_candidates(kite, universe_df, config, current_vix)
    signals = []
    for c in candidates[:max_positions]:
        qty = calculate_position_size(capital, c["entry_price"], c["stop_loss"], risk_pct)
        if qty <= 0:
            logger.warning(
                f"{c['symbol']}: qty=0 - SL distance too wide for capital Rs{capital:.0f}"
            )
            continue
        c["quantity"] = qty
        signals.append(c)
    return signals
