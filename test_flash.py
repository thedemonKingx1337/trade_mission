"""Quick config test - verifies .env is loaded and all keys are set."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from config.settings import GEMINI_API_KEY

print("=== Trade Mission Flash Model Test ===")

try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Test 2.5 Flash
    try:
        print("Testing gemini-2.5-flash...")
        model = genai.GenerativeModel("gemini-2.5-flash")
        response = model.generate_content("Say 'Flash works perfectly!' in one line.")
        print(f"  Response: {response.text.strip()}")
    except Exception as e:
        print(f"  FAILED: {e}")

except Exception as e:
    print(f"Global API Error: {e}")
