"""Quick config test - verifies .env is loaded and all keys are set."""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from config.settings import GEMINI_API_KEY

print("=== Trade Mission Model Test ===")
print(f"  GEMINI_KEY    = {'SET' if GEMINI_API_KEY else 'MISSING'}")
print()

try:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Test 2.5 Pro (The stable free tier model)
    try:
        print("Testing gemini-2.5-pro...")
        model = genai.GenerativeModel("gemini-2.5-pro")
        response = model.generate_content("Say '2.5 works!' in one line.")
        print(f"  Response: {response.text.strip()}")
    except Exception as e:
        print(f"  FAILED: {e}")
        
    print("-" * 30)
    
    # Test 3.1 Pro (The experimental/new model)
    try:
        model = genai.GenerativeModel("gemini-3.1-pro-preview")
        response = model.generate_content("Say '3.1 works!' in one line.")
        print(f"  Response: {response.text.strip()}")
    except Exception as e:
        print(f"  FAILED: {e}")

except Exception as e:
    print(f"Global API Error: {e}")
