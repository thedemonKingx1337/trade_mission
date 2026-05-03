"""Quick test for Kite API authentication."""
import sys
import logging
sys.stdout.reconfigure(encoding='utf-8')

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

from auth.kite_auth import get_kite

print("=== Testing Kite Connect Login ===")
try:
    kite = get_kite()
    print("\n✅ Successfully authenticated with Zerodha Kite!")
    
    # Test a simple API call
    profile = kite.profile()
    print(f"Logged in as: {profile.get('user_name')} ({profile.get('user_id')})")
    
except Exception as e:
    print(f"\n❌ Authentication failed: {e}")
