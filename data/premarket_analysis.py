"""
Pre-market intelligence for NSE/Zerodha KiteConnect intraday bot.

Called from strategies/selector.py during select_strategy() at 09:15 IST.
All external network calls (NSE public API) use graceful try/except fallbacks -
a failure here must never crash the bot or prevent strategy selection.

Main entry point: get_premarket_context(kite, universe_df) -> dict
"""
import logging

import requests
import pandas as pd
from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)

# Sector index NSE trading symbols (accessible via kite.quote())
_SECTOR_SYMBOLS = {
    "bank":   "NIFTY BANK",
    "it":     "NIFTY IT",
    "pharma": "NIFTY PHARMA",
    "metal":  "NIFTY METAL",
    "fmcg":   "NIFTY FMCG",
    "auto":   "NIFTY AUTO",
}

_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
}

_NSE_HOME         = "https://www.nseindia.com"
_NSE_PREOPEN_URL  = "https://www.nseindia.com/api/market-data-pre-open?key=NIFTY"
_NSE_OPTION_CHAIN = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"


def _nse_session() -> requests.Session:
    """Build an NSE-cookied requests session (NSE APIs reject requests without site cookies)."""
    session = requests.Session()
    session.headers.update(_NSE_HEADERS)
    try:
        session.get(_NSE_HOME, timeout=8)
    except Exception:
        pass
    return session


def get_gift_nifty_bias() -> dict:
    """
    Fetch NSE pre-open Nifty data as an overnight sentiment indicator.

    Note: True GIFT Nifty (SGX Nifty successor) requires a paid data feed.
    NSE's pre-open session data (09:00–09:08 IST) is free and provides a
    directional proxy. Returns neutral if the API is unavailable.

    Returns: {"change_pct": float, "bias": "bullish"|"bearish"|"neutral", "source": str}
    """
    try:
        session = _nse_session()
        resp = session.get(_NSE_PREOPEN_URL, timeout=8)
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("data", []):
            meta = item.get("metadata", {})
            if meta.get("symbol") == "NIFTY 50":
                change_pct = float(meta.get("pChange", 0.0))
                if change_pct > 0.3:
                    bias = "bullish"
                elif change_pct < -0.3:
                    bias = "bearish"
                else:
                    bias = "neutral"
                return {"change_pct": round(change_pct, 2), "bias": bias, "source": "nse_preopen"}
    except Exception as e:
        logger.debug(f"NSE pre-open fetch failed (non-fatal): {e}")

    return {"change_pct": 0.0, "bias": "neutral", "source": "unavailable"}


def get_sector_indices(kite: KiteConnect) -> dict[str, dict]:
    """
    Fetch sector index LTP and % change vs previous close via kite.quote().
    kite.quote() OHLC close field = previous trading day's close.

    Returns: {"bank": {"ltp": float, "change_pct": float}, ...}
    """
    result = {}
    instruments = [f"NSE:{sym}" for sym in _SECTOR_SYMBOLS.values()]
    try:
        quotes = kite.quote(instruments)
        for key_name, nse_sym in _SECTOR_SYMBOLS.items():
            q = quotes.get(f"NSE:{nse_sym}", {})
            if not q:
                continue
            ltp = float(q.get("last_price") or 0)
            prev_close = float((q.get("ohlc") or {}).get("close") or 0)
            change_pct = (ltp - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            result[key_name] = {"ltp": round(ltp, 2), "change_pct": round(change_pct, 2)}
    except Exception as e:
        logger.warning(f"Sector indices fetch failed: {e}")
    return result


def get_nifty_fno_pcr() -> float:
    """
    Fetch Nifty Put-Call Ratio from NSE option chain.

    PCR interpretation:
      < 0.8  -> call-heavy -> bullish sentiment
      0.8–1.2 -> neutral
      > 1.2  -> put-heavy -> bearish / hedging activity

    Returns: float PCR value, or 1.0 (neutral) on failure.
    """
    try:
        session = _nse_session()
        resp = session.get(_NSE_OPTION_CHAIN, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        total_put_oi  = 0
        total_call_oi = 0
        for row in data.get("records", {}).get("data", []):
            total_call_oi += int((row.get("CE") or {}).get("openInterest") or 0)
            total_put_oi  += int((row.get("PE") or {}).get("openInterest") or 0)

        if total_call_oi > 0:
            pcr = total_put_oi / total_call_oi
            logger.info(
                f"Nifty F&O PCR: {pcr:.2f} "
                f"(Put OI={total_put_oi:,}, Call OI={total_call_oi:,})"
            )
            return round(pcr, 3)
    except Exception as e:
        logger.debug(f"F&O PCR fetch failed (non-fatal): {e}")
    return 1.0


def get_fii_dii_data() -> dict:
    """
    Fetch previous day's FII/DII net buy/sell data from NSE.

    FII (Foreign Institutional Investor) flows drive 60-70% of Indian market
    direction. Consistent FII selling = bearish. Consistent FII buying = bullish.

    Returns:
      {
        "fii_net": float,       # FII net buy/sell in crore (negative = selling)
        "dii_net": float,       # DII net buy/sell in crore
        "total_net": float,     # combined net
        "fii_trend": str,       # "buying" | "selling" | "neutral"
        "available": bool,
      }

    Returns neutral defaults on failure.
    """
    default = {
        "fii_net": 0.0, "dii_net": 0.0, "total_net": 0.0,
        "fii_trend": "neutral", "available": False,
    }

    try:
        session = _nse_session()
        # NSE FII/DII activity endpoint (publicly available)
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        fii_buy = 0.0
        fii_sell = 0.0
        dii_buy = 0.0
        dii_sell = 0.0

        for entry in data:
            category = entry.get("category", "").upper()
            buy_val = float(entry.get("buyValue", 0))
            sell_val = float(entry.get("sellValue", 0))

            if "FII" in category or "FPI" in category:
                fii_buy += buy_val
                fii_sell += sell_val
            elif "DII" in category:
                dii_buy += buy_val
                dii_sell += sell_val

        fii_net = round((fii_buy - fii_sell) / 100, 2)  # convert to crore approx
        dii_net = round((dii_buy - dii_sell) / 100, 2)
        total_net = round(fii_net + dii_net, 2)

        if fii_net > 100:
            fii_trend = "buying"
        elif fii_net < -100:
            fii_trend = "selling"
        else:
            fii_trend = "neutral"

        logger.info(
            f"FII/DII flows: FII={fii_net:+.0f}cr ({fii_trend}), "
            f"DII={dii_net:+.0f}cr, Net={total_net:+.0f}cr"
        )

        return {
            "fii_net": fii_net,
            "dii_net": dii_net,
            "total_net": total_net,
            "fii_trend": fii_trend,
            "available": True,
        }

    except Exception as e:
        logger.debug(f"FII/DII data fetch failed (non-fatal): {e}")
        return default


def get_top_movers_prev_day(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    top_n: int = 5,
) -> dict:
    """
    Rank universe stocks by current gap from previous day's close.
    Uses kite.quote() OHLC data - ohlc.close is previous day's close.

    Returns: {"gainers": [sym,...], "losers": [sym,...], "changes": {sym: pct}}
    """
    if universe_df.empty:
        return {"gainers": [], "losers": [], "changes": {}}

    symbols = list(universe_df["symbol"])
    changes: dict[str, float] = {}

    try:
        for i in range(0, len(symbols), 200):
            batch = [f"NSE:{s}" for s in symbols[i : i + 200]]
            quotes = kite.quote(batch)
            for key, q in quotes.items():
                sym = key.split(":", 1)[1]
                prev_close = float((q.get("ohlc") or {}).get("close") or 0)
                ltp = float(q.get("last_price") or 0)
                if prev_close > 0:
                    changes[sym] = round((ltp - prev_close) / prev_close * 100, 2)
    except Exception as e:
        logger.warning(f"Top movers fetch failed: {e}")
        return {"gainers": [], "losers": [], "changes": {}}

    sorted_syms = sorted(changes, key=changes.get, reverse=True)
    return {
        "gainers": sorted_syms[:top_n],
        "losers":  list(reversed(sorted_syms[-top_n:])),
        "changes": changes,
    }


def get_premarket_context(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
) -> dict:
    """
    Master pre-market intelligence aggregator.

    Assembles Gift Nifty bias, sector trends, F&O PCR, and top movers
    into a single dict. Computes a composite `market_bias` signal used
    by strategies/selector.py to adjust strategy scores.

    Returns dict keys:
      gift_nifty_bias   : "bullish" | "bearish" | "neutral"
      gift_nifty_change : float  (% change from NSE pre-open)
      sector_trends     : {"bank": {"ltp": float, "change_pct": float}, ...}
      fno_pcr           : float  (Put-Call Ratio; 1.0 = neutral)
      top_gainers       : list[str]  top-5 prev-day gainers in universe
      top_losers        : list[str]  top-5 prev-day losers in universe
      sym_changes       : dict[str, float]  gap % for every universe symbol
      market_bias       : "bullish" | "bearish" | "neutral"
      bias_score        : float  (signed score driving market_bias)
    """
    logger.info("Gathering pre-market intelligence...")

    gift    = get_gift_nifty_bias()
    sectors = get_sector_indices(kite)
    pcr     = get_nifty_fno_pcr()
    fii_dii = get_fii_dii_data()
    movers  = get_top_movers_prev_day(kite, universe_df)

    # --- Composite bias score (range roughly -1.0 to +1.0) ---
    bias_score = 0.0

    # Gift Nifty / NSE pre-open direction (weight 0.40)
    if gift["bias"] == "bullish":
        bias_score += 0.40
    elif gift["bias"] == "bearish":
        bias_score -= 0.40

    # F&O PCR (weight 0.30): low PCR = call-heavy = bullish sentiment
    if pcr < 0.8:
        bias_score += 0.30
    elif pcr > 1.2:
        bias_score -= 0.30

    # Sector breadth (weight 0.20): fraction of sectors moving up vs down
    if sectors:
        n_up   = sum(1 for s in sectors.values() if s["change_pct"] >  0.2)
        n_down = sum(1 for s in sectors.values() if s["change_pct"] < -0.2)
        bias_score += 0.20 * (n_up - n_down) / len(sectors)

    # FII/DII flows (weight 0.25): institutional money direction
    if fii_dii["available"]:
        if fii_dii["fii_trend"] == "buying":
            bias_score += 0.25
        elif fii_dii["fii_trend"] == "selling":
            bias_score -= 0.25

    if bias_score >= 0.20:
        market_bias = "bullish"
    elif bias_score <= -0.20:
        market_bias = "bearish"
    else:
        market_bias = "neutral"

    sector_summary = {k: v["change_pct"] for k, v in sectors.items()}
    logger.info(
        f"Pre-market: Gift Nifty={gift['bias']}({gift['change_pct']:+.2f}%), "
        f"PCR={pcr:.2f}, sectors={sector_summary}, "
        f"bias_score={bias_score:+.2f} -> {market_bias.upper()}"
    )

    return {
        "gift_nifty_bias":   gift["bias"],
        "gift_nifty_change": gift["change_pct"],
        "sector_trends":     sectors,
        "fno_pcr":           pcr,
        "fii_dii":           fii_dii,
        "top_gainers":       movers["gainers"],
        "top_losers":        movers["losers"],
        "sym_changes":       movers["changes"],
        "market_bias":       market_bias,
        "bias_score":        round(bias_score, 3),
    }
