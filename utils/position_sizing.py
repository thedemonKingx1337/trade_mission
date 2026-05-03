import math
from config.settings import MIS_LEVERAGE


def calculate_position_size(
    capital: float,
    entry: float,
    stop_loss: float,
    risk_pct: float,
    leverage: int = MIS_LEVERAGE,
) -> int:
    """
    Kelly-lite sizing: risk_pct of capital divided by per-share risk.
    Hard cap: position value must not exceed capital * leverage.
    """
    risk_per_share = entry - stop_loss
    if risk_per_share <= 0:
        return 0
    qty = math.floor((capital * risk_pct) / risk_per_share)
    max_qty = math.floor((capital * leverage) / entry)
    return max(0, min(qty, max_qty))
