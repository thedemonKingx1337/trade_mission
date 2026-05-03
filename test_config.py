"""Quick config test - verifies .env is loaded and all keys are set."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from config.settings import (
    IST, DRY_RUN, ACTIVE_AI_BRAIN, GEMINI_API_KEY, GEMINI_TRADE_MODEL,
    KITE_API_KEY, KITE_API_SECRET, KITE_USER_ID,
)

print("=== Trade Mission Config Test ===")
print(f"  DRY_RUN       = {DRY_RUN}")
print(f"  AI Brain      = {ACTIVE_AI_BRAIN}")
print(f"  Trade Model   = {GEMINI_TRADE_MODEL}")
print(f"  GEMINI_KEY    = {'SET' if GEMINI_API_KEY else 'MISSING'}")
print(f"  KITE_KEY      = {'SET' if KITE_API_KEY else 'MISSING'}")
print()

# Test Gemini import
try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    
    print(f"Testing access to {GEMINI_TRADE_MODEL}...")
    model = genai.GenerativeModel(GEMINI_TRADE_MODEL)
    response = model.generate_content("Say 'Gemini 3.1 Pro is online!' in one line.")
    print(f"  API Response  = {response.text.strip()}")
except Exception as e:
    print(f"  API Test      = FAILED: {e}")

print()
print("Config loaded OK!")
