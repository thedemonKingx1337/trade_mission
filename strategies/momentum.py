import logging

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import (
    MOMENTUM_MIN_GAP_PCT, MOMENTUM_MIN_VOL_RATIO, MOMENTUM_MAX_CHASE_PCT,
    TARGET_ATR_MULTIPLIER,
)
from data.market_data import get_today_candles, get_daily_candles, get_nifty_ltp
from indicators.technicals import (
    compute_all, get_opening_range, add_daily_vol_ratio, _safe_last,
)
from utils.position_sizing import calculate_position_size

logger = logging.getLogger(__name__)


def scan_candidates(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    config: dict,
) -> list[dict]:
    min_gap   = config.get("min_gap_pct",    MOMENTUM_MIN_GAP_PCT)
    min_vol   = config.get("min_vol_ratio",  MOMENTUM_MIN_VOL_RATIO)
    max_chase = config.get("max_chase_pct",  MOMENTUM_MAX_CHASE_PCT)

    # Broad market filter: skip if Nifty is down >0.5% at time of scan
    nifty_ltp = get_nifty_ltp(kite)
    nifty_gap = universe_df.attrs.get("nifty_gap_pct", 0.0)
    if nifty_gap < -0.5:
        logger.info(f"Nifty down {nifty_gap:.2f}% — momentum suppressed")
        return []

    gap_stocks = universe_df[universe_df["gap_pct"] >= min_gap]
    if gap_stocks.empty:
        return []

    candidates = []
    for _, row in gap_stocks.iterrows():
        symbol = row["symbol"]
        try:
            # First 5-minute candle (Opening Range) — only completed candles
            candles_5m = get_today_candles(kite, symbol, interval="5minute", only_complete=True)
            if candles_5m.empty:
                continue

            or_data = get_opening_range(candles_5m.iloc[:1])  # first completed candle only
            if not or_data or or_data["or_range"] <= 0:
                continue

            or_high = or_data["or_high"]
            or_low  = or_data["or_low"]
            current_price = float(candles_5m.iloc[-1]["close"])

            # Price must have broken above OR High (breakout confirmed)
            if current_price <= or_high:
                logger.debug(f"{symbol}: price {current_price:.2f} not above OR high {or_high:.2f}")
                continue

            # Don't chase if price ran too far above OR high
            chase_pct = (current_price - or_high) / or_high * 100
            if chase_pct > max_chase:
                logger.debug(f"{symbol}: chasing {chase_pct:.2f}% above OR high — skipped")
                continue

            # Volume ratio: use daily avg volume for accurate surge detection
            daily_df = get_daily_candles(kite, symbol, days=22)
            avg_daily_vol = float(daily_df["volume"].tail(20).mean()) if not daily_df.empty else 0
            enriched = add_daily_vol_ratio(candles_5m.copy(), avg_daily_vol)
            first_candle_vol_ratio = float(enriched["vol_ratio"].iloc[0]) if not enriched.empty else 0

            if first_candle_vol_ratio < min_vol:
                logger.debug(f"{symbol}: vol_ratio {first_candle_vol_ratio:.2f} < {min_vol}")
                continue

            # ATR from daily candles for accurate SL/target sizing
            if not daily_df.empty and len(daily_df) >= 5:
                enriched_daily = compute_all(daily_df)
                atr14 = _safe_last(enriched_daily.get("atr_14", pd.Series(dtype=float)),
                                   default=or_data["or_range"] * 2)
            else:
                atr14 = or_data["or_range"] * 2

            entry_price = or_high * 1.001  # slight buffer above OR high
            stop_loss   = or_low
            sl_distance = entry_price - stop_loss
            if sl_distance <= 0:
                continue

            target = entry_price + (TARGET_ATR_MULTIPLIER * atr14)

            # Minimum R:R check
            if (target - entry_price) < (entry_price - stop_loss):
                logger.debug(f"{symbol}: R:R less than 1:1, skipping")
                continue

            score = round(
                min(1.0,
                    (row["gap_pct"] / 4.0) * 0.5 + (min(first_candle_vol_ratio, 4.0) / 4.0) * 0.5
                ), 3
            )

            candidates.append({
                "symbol":           symbol,
                "exchange":         "NSE",
                "instrument_token": row.get("instrument_token"),
                "direction":        "BUY",
                "entry_price":      round(entry_price, 2),
                "stop_loss":        round(stop_loss, 2),
                "target_price":     round(target, 2),
                "atr14":            round(atr14, 2),
                "gap_pct":          round(row["gap_pct"], 2),
                "vol_ratio":        round(first_candle_vol_ratio, 2),
                "strategy":         "momentum",
                "rationale": (
                    f"Gap-up {row['gap_pct']:.1f}%, vol_ratio {first_candle_vol_ratio:.1f}x, "
                    f"broke OR high {or_high:.2f}"
                ),
                "score": score,
            })

        except Exception as e:
            logger.warning(f"Error scanning {symbol} for momentum: {e}")

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


def get_signals(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    capital: float,
    config: dict,
    risk_pct: float = 0.25,
    max_positions: int = 3,
) -> list[dict]:
    candidates = scan_candidates(kite, universe_df, config)
    signals = []
    for c in candidates[:max_positions]:
        qty = calculate_position_size(capital, c["entry_price"], c["stop_loss"], risk_pct)
        if qty <= 0:
            logger.warning(
                f"{c['symbol']}: qty=0 (capital Rs{capital:.0f}, "
                f"SL distance={c['entry_price']-c['stop_loss']:.2f})"
            )
            continue
        c["quantity"] = qty
        signals.append(c)
    return signals
