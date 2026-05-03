"""Quick config test - verifies .env is loaded and all keys are set."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from config.settings import (
    IST, DRY_RUN, ACTIVE_AI_BRAIN, GEMINI_API_KEY,
    KITE_API_KEY, KITE_API_SECRET, KITE_USER_ID,
)

print("=== Trade Mission Config Test ===")
print(f"  DRY_RUN       = {DRY_RUN}")
print(f"  AI Brain      = {ACTIVE_AI_BRAIN}")
print(f"  GEMINI_KEY    = {'SET' if GEMINI_API_KEY else 'MISSING'}")
print(f"  KITE_KEY      = {'SET' if KITE_API_KEY else 'MISSING'}")
print(f"  KITE_SECRET   = {'SET' if KITE_API_SECRET else 'MISSING'}")
print(f"  KITE_USER_ID  = {'SET' if KITE_USER_ID else 'MISSING'}")
print()

# Test Gemini import
try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")
    response = model.generate_content("Say 'Trade Mission ready!' in one line.")
    print(f"  Gemini Test   = {response.text.strip()}")
except Exception as e:
    print(f"  Gemini Test   = FAILED: {e}")

print()
print("Config loaded OK!")
