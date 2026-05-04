"""
Sector Correlation Filter - Trade Mission

Prevents the bot from buying multiple stocks in the same sector on the same day.
If HDFCBANK is already open, this filter blocks ICICIBANK and SBIN signals.

Diversification across sectors reduces correlated blow-up risk.
"""
import logging

logger = logging.getLogger(__name__)

# Nifty 50 stocks grouped by sector
SECTOR_MAP: dict[str, list[str]] = {
    "banking": [
        "HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "INDUSINDBK",
    ],
    "it": [
        "TCS", "INFY", "HCLTECH", "WIPRO", "TECHM",
    ],
    "auto": [
        "MARUTI", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "M&M", "TATAMOTORS",
    ],
    "pharma": [
        "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
    ],
    "metal": [
        "JSWSTEEL", "TATASTEEL", "HINDALCO",
    ],
    "oil_gas": [
        "RELIANCE", "ONGC", "BPCL",
    ],
    "fmcg": [
        "HINDUNILVR", "ITC", "BRITANNIA", "NESTLEIND", "TATACONSUM",
    ],
    "infra_power": [
        "LT", "POWERGRID", "NTPC", "COALINDIA", "ADANIPORTS", "ADANIENT",
    ],
    "financial_services": [
        "BAJFINANCE", "BAJAJFINSV", "HDFCLIFE", "SBILIFE",
    ],
    "cement": [
        "ULTRACEMCO", "GRASIM", "SHREECEM",
    ],
    "consumer": [
        "TITAN", "ASIANPAINT",
    ],
    "misc": [
        "UPL",
    ],
}

# Reverse lookup: symbol -> sector
_SYMBOL_TO_SECTOR: dict[str, str] = {}
for _sector, _symbols in SECTOR_MAP.items():
    for _sym in _symbols:
        _SYMBOL_TO_SECTOR[_sym] = _sector


def get_sector(symbol: str) -> str:
    """Return sector name for a symbol. Returns 'unknown' if not mapped."""
    return _SYMBOL_TO_SECTOR.get(symbol.upper(), "unknown")


def filter_correlated(
    signals: list[dict],
    open_trades: dict,
    max_per_sector: int = 1,
) -> list[dict]:
    """
    Remove signals from sectors that already have open positions.

    Args:
        signals: list of signal dicts (each has a 'symbol' key)
        open_trades: dict of currently open trades {trade_id: trade_dict}
        max_per_sector: maximum positions allowed per sector (default 1)

    Returns:
        Filtered list of signals with correlated duplicates removed.
    """
    # Count open positions per sector
    sector_count: dict[str, int] = {}
    for trade in open_trades.values():
        sym = trade.get("symbol", "")
        sector = get_sector(sym)
        if sector != "unknown":
            sector_count[sector] = sector_count.get(sector, 0) + 1

    filtered = []
    for signal in signals:
        sym = signal.get("symbol", "")
        sector = get_sector(sym)

        current_count = sector_count.get(sector, 0)
        if sector != "unknown" and current_count >= max_per_sector:
            logger.info(
                f"Correlation filter: {sym} blocked - sector '{sector}' "
                f"already has {current_count} open position(s)"
            )
            continue

        filtered.append(signal)
        # Track this signal as "will be opened" for subsequent signals
        if sector != "unknown":
            sector_count[sector] = current_count + 1

    if len(filtered) < len(signals):
        logger.info(
            f"Correlation filter: {len(signals)} signals -> {len(filtered)} "
            f"after sector dedup"
        )

    return filtered
