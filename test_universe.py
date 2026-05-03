import logging
from auth.kite_auth import get_kite
from data.universe import get_base_universe, filter_universe
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def test_dynamic_universe():
    print("=== Testing Dynamic Full-Market Scanner ===")
    kite = get_kite()
    if not kite: return

    symbols = get_base_universe(kite)
    print(f"\n[Scanner] Found {len(symbols)} active NSE equity symbols.")
    
    print("[Scanner] Fetching live quotes and applying anti-trap filters...")
    df = filter_universe(kite, symbols)
    
    if df.empty:
        print("\nNo stocks passed. (On weekends, volume is 0, so the liquidity filter blocks everything).")
    else:
        print("\nSuccess! Here are the Top 20 Stocks selected by the dynamic scanner:")
        print(df.to_string())

if __name__ == "__main__":
    test_dynamic_universe()
