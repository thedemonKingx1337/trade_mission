import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import (
    IST, KNOWLEDGE_DIR, PANIC_VIX_THRESHOLD,
    MOMENTUM_MIN_GAP_PCT, MEAN_REV_RSI_OVERSOLD,
    RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS,
    RECOVERY_RISK_MULTIPLIER, RECOVERY_MAX_POSITIONS, RECOVERY_MIN_SCORE,
    MIN_STRATEGY_SCORE,
)
from data.market_data import get_nifty_vix, get_nifty_ltp, get_daily_candles
from data.premarket_analysis import get_premarket_context
from ledger.tracker import is_recovery_mode

logger = logging.getLogger(__name__)


def load_knowledge_files() -> dict[str, str]:
    knowledge = {}
    for md_file in Path(KNOWLEDGE_DIR).glob("*.md"):
        knowledge[md_file.stem] = md_file.read_text()
    return knowledge


def get_market_context(kite: KiteConnect) -> dict:
    vix = get_nifty_vix(kite)
    nifty_ltp = get_nifty_ltp(kite)

    nifty_gap_pct = 0.0
    prev_day_nifty_pct = 0.0
    prev_day_was_inside_bar = False
    nifty_avg_atr = 0.0

    daily = get_daily_candles(kite, "NIFTY 50", days=22)
    if len(daily) >= 3:
        prev_close = float(daily.iloc[-1]["close"])
        two_days_close = float(daily.iloc[-2]["close"])
        prev_day_nifty_pct = (prev_close - two_days_close) / two_days_close * 100 if two_days_close else 0.0
        nifty_gap_pct = (nifty_ltp - prev_close) / prev_close * 100 if prev_close > 0 else 0.0

        # Inside bar: yesterday's range inside the day before's range
        if len(daily) >= 2:
            yd = daily.iloc[-1]
            dby = daily.iloc[-2]
            prev_day_was_inside_bar = (
                float(yd["high"]) <= float(dby["high"])
                and float(yd["low"]) >= float(dby["low"])
            )

        # Nifty 20-day ATR ratio
        highs = daily["high"].astype(float)
        lows  = daily["low"].astype(float)
        closes_prev = daily["close"].astype(float).shift(1)
        tr = pd.concat([highs - lows,
                         (highs - closes_prev).abs(),
                         (lows  - closes_prev).abs()], axis=1).max(axis=1)
        nifty_avg_atr = float(tr.tail(20).mean())

    return {
        "vix": vix,
        "nifty_ltp": nifty_ltp,
        "nifty_gap_pct": nifty_gap_pct,
        "prev_day_nifty_pct": prev_day_nifty_pct,
        "prev_day_was_inside_bar": prev_day_was_inside_bar,
        "nifty_avg_atr": nifty_avg_atr,
    }


def _compute_rsi_data(universe_df: pd.DataFrame, kite: KiteConnect) -> dict[str, float]:
    """Return {symbol: latest_rsi} for all universe stocks — used for mean-reversion scoring."""
    from data.market_data import get_today_candles
    from indicators.technicals import add_rsi, _safe_last
    rsi_data = {}
    for _, row in universe_df.iterrows():
        sym = row["symbol"]
        try:
            candles = get_today_candles(kite, sym, interval="15minute", only_complete=True)
            if candles.empty or len(candles) < 5:
                continue
            enriched = add_rsi(candles.copy())
            rsi_val = _safe_last(enriched.get("rsi_14", pd.Series(dtype=float)))
            if not pd.isna(rsi_val):
                rsi_data[sym] = rsi_val
        except Exception:
            pass
    return rsi_data


def score_momentum(
    universe_df: pd.DataFrame, context: dict, premarket: dict | None = None
) -> float:
    score = 0.0
    vix = context.get("vix", 15.0)
    nifty_gap = context.get("nifty_gap_pct", 0.0)

    gap_stocks = universe_df[universe_df["gap_pct"] >= MOMENTUM_MIN_GAP_PCT]
    n_gap = len(gap_stocks)
    score += min(0.40, n_gap * 0.20)  # +0.20 per gap stock, max +0.40

    if "vol_ratio" in universe_df.columns:
        avg_vr = universe_df["vol_ratio"].dropna().mean()
        if avg_vr >= 2.0:
            score += 0.10

    if nifty_gap > 0.5:
        score += 0.10
    elif nifty_gap < 0.2:
        score -= 0.20

    if 12 <= vix <= 18:
        score += 0.10
    elif vix > 20:
        score -= 0.15

    # Pre-market: bullish overnight bias boosts momentum score
    if premarket:
        if premarket.get("market_bias") == "bullish":
            score += 0.15
        elif premarket.get("market_bias") == "bearish":
            score -= 0.15
        # F&O PCR < 0.8 = call-heavy = momentum-friendly
        pcr = premarket.get("fno_pcr", 1.0)
        if pcr < 0.8:
            score += 0.05
        # Sector breadth: bank+auto gapping = strong momentum day
        sectors = premarket.get("sector_trends", {})
        if sectors.get("bank", {}).get("change_pct", 0) > 0.5:
            score += 0.05
        # Pre-qualified gainers from prev day that are also in gap_stocks
        top_gainers = set(premarket.get("top_gainers", []))
        overlap = top_gainers & set(gap_stocks["symbol"].tolist())
        if overlap:
            score += min(0.10, len(overlap) * 0.05)

    return round(max(0.0, min(1.0, score)), 3)


def score_mean_reversion(
    universe_df: pd.DataFrame,
    context: dict,
    rsi_data: dict,
    premarket: dict | None = None,
) -> float:
    score = 0.0
    vix = context.get("vix", 15.0)
    prev_day_pct = context.get("prev_day_nifty_pct", 0.0)

    if vix > 20:
        score -= 0.30
    elif 14 <= vix <= 20:
        score += 0.10

    if prev_day_pct < -1.0:
        score += 0.20
    elif prev_day_pct > 0.5:
        score -= 0.20

    n_oversold = sum(1 for v in rsi_data.values() if v < MEAN_REV_RSI_OVERSOLD)
    score += min(0.30, n_oversold * 0.06)

    # Pre-market: bearish overnight bias means more oversold opportunities
    if premarket:
        if premarket.get("market_bias") == "bearish":
            score += 0.15
        elif premarket.get("market_bias") == "bullish":
            score -= 0.10
        # High PCR = defensive hedging = mean-reversion-friendly
        pcr = premarket.get("fno_pcr", 1.0)
        if pcr > 1.2:
            score += 0.10
        # Top losers from prev day make better RSI-bounce candidates
        top_losers = set(premarket.get("top_losers", []))
        n_oversold_losers = sum(
            1 for s in top_losers if rsi_data.get(s, 100) < MEAN_REV_RSI_OVERSOLD
        )
        score += min(0.10, n_oversold_losers * 0.05)

    return round(max(0.0, min(1.0, score)), 3)


def score_range_trading(
    universe_df: pd.DataFrame,
    context: dict,
    premarket: dict | None = None,
) -> float:
    score = 0.0
    vix = context.get("vix", 15.0)
    nifty_gap = abs(context.get("nifty_gap_pct", 0.0))
    inside_bar = context.get("prev_day_was_inside_bar", False)

    if vix < 13:
        score += 0.30
    elif vix > 18:
        score -= 0.30

    if nifty_gap < 0.3:
        score += 0.20
    elif nifty_gap > 0.5:
        score -= 0.20

    if inside_bar:
        score += 0.10

    gap_stocks = universe_df[universe_df["gap_pct"].abs() >= MOMENTUM_MIN_GAP_PCT]
    if len(gap_stocks) > 5:
        score -= 0.20

    # Pre-market: neutral overnight bias = range-friendly environment
    if premarket:
        if premarket.get("market_bias") == "neutral":
            score += 0.10
        elif premarket.get("market_bias") != "neutral":
            score -= 0.05
        # Low PCR (~1.0) = balanced positioning = range day
        pcr = premarket.get("fno_pcr", 1.0)
        if 0.85 <= pcr <= 1.15:
            score += 0.05
        # Gift Nifty flat = no directional overnight move = range day
        gift_change = abs(premarket.get("gift_nifty_change", 0.0))
        if gift_change < 0.2:
            score += 0.05

    return round(max(0.0, min(1.0, score)), 3)


def select_strategy(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    conn: sqlite3.Connection = None,
) -> tuple[str, dict]:
    context = get_market_context(kite)
    vix = context["vix"]

    logger.info(f"Market context: {context}")
    load_knowledge_files()  # load for logging; rules are enforced in Python code

    if vix > PANIC_VIX_THRESHOLD:
        logger.warning(
            f"PANIC REGIME — VIX {vix:.1f} > {PANIC_VIX_THRESHOLD}. Skipping trading today."
        )
        return "skip", {"reason": f"VIX {vix:.1f} in panic territory", "vix": vix}

    # Pre-market intelligence: Gift Nifty bias, sector trends, F&O PCR, top movers
    premarket: dict | None = None
    try:
        premarket = get_premarket_context(kite, universe_df)
        logger.info(
            f"Pre-market bias: {premarket['market_bias'].upper()} "
            f"(score={premarket['bias_score']:+.2f}, PCR={premarket['fno_pcr']:.2f})"
        )
    except Exception as e:
        logger.warning(f"Pre-market analysis failed (non-fatal): {e}")

    # Compute RSI data for all universe stocks (needed for mean-reversion scoring)
    rsi_data = _compute_rsi_data(universe_df, kite)

    m_score  = score_momentum(universe_df, context, premarket)
    mr_score = score_mean_reversion(universe_df, context, rsi_data, premarket)
    r_score  = score_range_trading(universe_df, context, premarket)

    scores = {"momentum": m_score, "mean_reversion": mr_score, "range_trading": r_score}
    logger.info(
        f"Strategy scores — Momentum: {m_score:.2f}, "
        f"MeanRev: {mr_score:.2f}, Range: {r_score:.2f}"
    )

    best = max(scores, key=scores.get)
    best_score = scores[best]

    if best_score < MIN_STRATEGY_SCORE:
        logger.info(
            f"All strategy scores below {MIN_STRATEGY_SCORE} — skipping trading today."
        )
        return "skip", {"reason": "No strategy scored above threshold", "scores": scores}

    # Recovery mode — read from settings constants (not hardcoded)
    recovery = False
    max_positions = MAX_OPEN_POSITIONS
    risk_pct = RISK_PER_TRADE_PCT

    if conn and is_recovery_mode(conn):
        recovery = True
        risk_pct = round(RISK_PER_TRADE_PCT * RECOVERY_RISK_MULTIPLIER, 4)
        max_positions = RECOVERY_MAX_POSITIONS
        logger.warning(
            f"RECOVERY MODE ACTIVE — risk_pct={risk_pct:.0%}, "
            f"max_positions={max_positions}, min_score={RECOVERY_MIN_SCORE}"
        )
        if best_score < RECOVERY_MIN_SCORE:
            logger.warning(
                f"Recovery mode: best score {best_score:.2f} < {RECOVERY_MIN_SCORE}. "
                "Skipping today to preserve capital."
            )
            return "skip", {"reason": "Recovery mode requires higher score", "scores": scores}

    config = {
        "scores": scores,
        "selected_score": best_score,
        "market_context": context,
        "premarket": premarket,
        "risk_pct": risk_pct,
        "max_positions": max_positions,
        "recovery_mode": recovery,
        "rsi_data": rsi_data,
    }

    if best == "momentum":
        config.update({"min_gap_pct": 1.5, "min_vol_ratio": 1.8, "max_chase_pct": 0.5})
    elif best == "mean_reversion":
        config.update({"rsi_oversold_threshold": MEAN_REV_RSI_OVERSOLD, "max_vix": 20.0})
    elif best == "range_trading":
        config.update({"max_range_pct": 1.5, "max_vix": 13.0})

    if conn:
        now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
        trade_date = datetime.now(IST).strftime("%Y-%m-%d")
        full_context = {**context, "premarket": premarket}
        try:
            conn.execute(
                """INSERT INTO strategy_log
                   (trade_date,log_time,momentum_score,mean_rev_score,range_score,selected,market_context)
                   VALUES (?,?,?,?,?,?,?)""",
                (trade_date, now, m_score, mr_score, r_score, best, json.dumps(full_context)),
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"Could not write strategy_log: {e}")

    logger.info(f"Selected strategy: {best.upper()} (score={best_score:.2f})")
    return best, config
