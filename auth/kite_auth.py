import json
import logging
from datetime import datetime
from pathlib import Path

from kiteconnect import KiteConnect

from config.settings import (
    KITE_API_KEY, KITE_API_SECRET, TOKEN_PATH, IST
)

logger = logging.getLogger(__name__)


def _today_ist() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def load_session() -> KiteConnect | None:
    path = Path(TOKEN_PATH)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if data.get("date") != _today_ist():
            logger.info("Stored session is from a previous day — need fresh login.")
            return None
        kite = KiteConnect(api_key=KITE_API_KEY)
        kite.set_access_token(data["access_token"])
        logger.info("Loaded existing session token (no login required).")
        return kite
    except Exception as e:
        logger.warning(f"Could not load session: {e}")
        return None


def save_session(access_token: str) -> None:
    path = Path(TOKEN_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"access_token": access_token, "date": _today_ist()}))
    logger.info(f"Session token saved to {path}")


def interactive_login() -> KiteConnect:
    kite = KiteConnect(api_key=KITE_API_KEY)
    login_url = kite.login_url()
    print("\n" + "=" * 65)
    print("  ZERODHA LOGIN REQUIRED")
    print("=" * 65)
    print(f"\n  1. Open this URL in your browser:\n\n     {login_url}\n")
    print("  2. Log in with your Zerodha credentials + TOTP.")
    print("  3. After login you'll be redirected to a URL like:")
    print("     http://127.0.0.1/?request_token=XXXX&action=login&status=success")
    print("\n  4. Copy ONLY the request_token value (the XXXX part) and paste below.\n")
    request_token = input("  Paste request_token here: ").strip()

    session = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
    access_token = session["access_token"]
    kite.set_access_token(access_token)
    save_session(access_token)
    logger.info("Login successful.")
    return kite


def get_kite() -> KiteConnect:
    if not KITE_API_KEY or KITE_API_KEY == "your_api_key_here":
        raise RuntimeError(
            "KITE_API_KEY is not set in .env\n"
            "Subscribe to KiteConnect API at https://developers.kite.trade/ "
            "and add your api_key to the .env file."
        )
    kite = load_session()
    if kite is not None:
        return kite
    return interactive_login()
