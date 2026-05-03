import os
from datetime import time, date
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# --- KiteConnect credentials ---
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_USER_ID = os.getenv("KITE_USER_ID", "")
KITE_PASSWORD = os.getenv("KITE_PASSWORD", "")
KITE_TOTP_SECRET = os.getenv("KITE_TOTP_SECRET", "")

# --- Capital & risk ---
SEED_CAPITAL = float(os.getenv("SEED_CAPITAL", "1000.0"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "0.25"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.04"))
PROFIT_LOCK_PCT = float(os.getenv("PROFIT_LOCK_PCT", "0.08"))
MIS_LEVERAGE = int(os.getenv("MIS_LEVERAGE", "3"))

# Recovery mode: if yesterday ended in a loss, bump risk allocation
# Effective recovery risk = RISK_PER_TRADE_PCT * RECOVERY_RISK_MULTIPLIER
RECOVERY_RISK_MULTIPLIER = 1.2   # 1.2 × 0.25 = 0.30 recovery risk
RECOVERY_MAX_POSITIONS = 1       # single highest-confidence trade only
RECOVERY_MIN_SCORE = 0.70        # signal must score above this in recovery

# --- Paths ---
DB_PATH = BASE_DIR / os.getenv("DB_PATH", "ledger/trades.db")
TOKEN_PATH = BASE_DIR / os.getenv("TOKEN_PATH", "auth/.session_token")
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
LOG_DIR = BASE_DIR / "logs"

# --- Timezone ---
IST = ZoneInfo("Asia/Kolkata")

# --- Market schedule (IST) ---
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PREMARKET_START = time(9, 0)
STRATEGY_LOCK_TIME = time(9, 25)
LAST_ENTRY_TIME = time(10, 15)
EOD_SQUAREOFF_TIME = time(15, 15)
SHUTDOWN_TIME = time(15, 30)

# --- Strategy parameters ---
MOMENTUM_MIN_GAP_PCT = 1.5
MOMENTUM_MIN_VOL_RATIO = 1.8
MOMENTUM_MAX_CHASE_PCT = 0.5

MEAN_REV_RSI_OVERSOLD = 32
MEAN_REV_MAX_VIX = 20.0

RANGE_MAX_ATR_RATIO = 1.0
RANGE_MAX_VIX = 13.0

PANIC_VIX_THRESHOLD = 25.0
MIN_STRATEGY_SCORE = 0.30        # skip trading if best score below this

# --- SL / Target (used everywhere — do not hardcode elsewhere) ---
SL_ATR_MULTIPLIER = 1.0          # stop = entry - 1x ATR14
TARGET_ATR_MULTIPLIER = 2.0      # target = entry + 2x ATR14

# --- Claude AI brain ---
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_TRADE_MODEL = os.getenv("CLAUDE_TRADE_MODEL", "claude-opus-4-7")    # morning decisions
CLAUDE_MONITOR_MODEL = os.getenv("CLAUDE_MONITOR_MODEL", "claude-sonnet-4-6")  # position advice

# --- Market Intelligence ---
NEWS_FETCH_ENABLED = os.getenv("NEWS_FETCH_ENABLED", "true").lower() == "true"
NEWS_MAX_HEADLINES = int(os.getenv("NEWS_MAX_HEADLINES", "15"))
NEWS_MAX_AGE_HOURS = int(os.getenv("NEWS_MAX_AGE_HOURS", "24"))
EVENTS_CALENDAR_PATH = KNOWLEDGE_DIR / "market_events.json"

# --- Partial profit booking ---
# Sell PARTIAL_PROFIT_PCT of qty at intermediate target (entry + 1×ATR)
# Let remaining qty run to full target (entry + 2×ATR) with breakeven SL
PARTIAL_PROFIT_ENABLED = True
PARTIAL_PROFIT_RATIO = 0.5          # fraction to book early (50%)
PARTIAL_TARGET_ATR_MULT = 1.0       # intermediate target = entry + 1×ATR
FULL_TARGET_ATR_MULT = 2.0          # full target = entry + 2×ATR (same as TARGET_ATR_MULTIPLIER)

# --- Time-decay trailing SL (IST times) ---
TIME_DECAY_SL_ENABLED = True
TIME_DECAY_BREAKEVEN_AFTER = time(12, 0)    # move SL to breakeven if profitable after 12:00
TIME_DECAY_TIGHTEN_AFTER = time(13, 30)     # tighten SL to entry+0.3×ATR after 13:30
TIME_DECAY_AGGRESSIVE_AFTER = time(14, 30)  # exit losing trades, lock profitable after 14:30

# --- Win-rate adaptive risk ---
ADAPTIVE_RISK_ENABLED = True
ADAPTIVE_RISK_LOOKBACK = 10         # number of recent trades to evaluate
ADAPTIVE_RISK_HOT_THRESHOLD = 0.60  # win rate above this → boost risk
ADAPTIVE_RISK_HOT_MULT = 1.3       # risk multiplier on hot streak
ADAPTIVE_RISK_COLD_THRESHOLD = 0.40 # win rate below this → reduce risk
ADAPTIVE_RISK_COLD_MULT = 0.7      # risk multiplier on cold streak
ADAPTIVE_RISK_ICE_THRESHOLD = 0.25  # win rate below this → minimal risk
ADAPTIVE_RISK_ICE_MULT = 0.5       # risk multiplier on ice-cold streak

# --- Misc ---
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# --- NSE holidays 2025-2026 (update annually) ---
# Source: NSE India official holiday list
NSE_HOLIDAYS: set[date] = {
    # 2025
    date(2025, 1, 26),   # Republic Day
    date(2025, 3, 14),   # Holi
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti / Good Friday
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 24),  # Diwali Laxmi Puja
    date(2025, 11, 5),   # Diwali Balipratipada
    date(2025, 12, 25),  # Christmas
    # 2026
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 20),   # Holi (approximate — confirm from NSE circular)
    date(2026, 4, 3),    # Good Friday
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 12, 25),  # Christmas
}

NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK",
    "LT", "AXISBANK", "ASIANPAINT", "MARUTI", "BAJFINANCE",
    "HCLTECH", "WIPRO", "ULTRACEMCO", "TITAN", "POWERGRID",
    "NTPC", "ONGC", "COALINDIA", "ADANIPORTS", "JSWSTEEL",
    "TATASTEEL", "HINDALCO", "GRASIM", "BPCL", "TECHM",
    "SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB", "APOLLOHOSP",
    "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO", "M&M", "TATACONSUM",
    "BRITANNIA", "NESTLEIND", "INDUSINDBK", "BAJAJFINSV", "HDFCLIFE",
    "SBILIFE", "UPL", "SHREECEM", "TATAMOTORS", "ADANIENT",
]
