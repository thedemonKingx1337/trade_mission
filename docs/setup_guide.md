# Setup & Daily Operation Guide — Trade Mission

> Back to: [AGENTS.md](../AGENTS.md)

---

## Step 1 — Prerequisites (one-time)

**Python 3.11 or higher is required** (uses `zoneinfo`, type hints with `|`).

```bash
python --version
```

If below 3.11, download from https://www.python.org/downloads/ and install. On Windows, tick **"Add Python to PATH"** during installation.

---

## Step 2 — Subscribe to KiteConnect API (one-time, ₹2000/month)

1. Go to **https://kite.trade** (Zerodha developer portal)
2. Log in with your Zerodha credentials
3. Click **Create new app** → choose **Connect** type
4. Name it anything (e.g. "TradeMission")
5. Copy your **API Key** and **API Secret** — you need them in Step 4

> Without this subscription the bot cannot place orders or fetch live market data.
> This is separate from your Zerodha trading account charges.

---

## Step 3 — Install dependencies (one-time)

Open **Command Prompt** or **PowerShell** in the `trade_mission/` folder:

```bash
pip install -r requirements.txt
```

Installs: `kiteconnect`, `pandas`, `numpy`, `pandas_ta`, `schedule`, `python-dotenv`, `pyotp`, `colorlog`, `tabulate`, `requests`.

If `pip not found`: try `python -m pip install -r requirements.txt`.

---

## Step 4 — Configure credentials (one-time)

```bash
# Windows Command Prompt
copy .env.example .env

# Windows PowerShell / Git Bash
cp .env.example .env
```

Open `.env` in any text editor and fill in every line:

```
KITE_API_KEY=your_api_key_from_step_2
KITE_API_SECRET=your_secret_from_step_2
KITE_USER_ID=your_zerodha_user_id        # e.g. ZJ1234
KITE_PASSWORD=your_zerodha_password
KITE_TOTP_SECRET=                        # leave blank for now (manual OTP at login)

SEED_CAPITAL=1000.0
RISK_PER_TRADE_PCT=0.25
MAX_OPEN_POSITIONS=3
MAX_DAILY_LOSS_PCT=0.04
PROFIT_LOCK_PCT=0.08
MIS_LEVERAGE=3

DB_PATH=ledger/trades.db
TOKEN_PATH=auth/.session_token

DRY_RUN=true          # KEEP TRUE until paper trading is complete
LOG_LEVEL=INFO
```

> **Never commit `.env` to git.** It contains your login password.

---

## Step 5 — Paper trade first (at least 5 trading days)

**Every morning, before 08:55 IST:**

```bash
python main.py
```

The bot prints a banner and waits for the schedule. At **09:00 IST** it will print:

```
[INFO] Please open this URL in your browser to log in:
https://kite.zerodha.com/connect/login?api_key=YOUR_KEY&v=3

After logging in, paste the full redirect URL or just the request_token here:
```

**Login steps:**
1. Open the printed URL in your browser
2. Log in: Zerodha username → password → OTP (from Zerodha authenticator app)
3. You will be redirected to a URL that may show an error — that is normal
4. Copy the **full URL** from your browser address bar
5. Paste it back into the terminal and press Enter

The bot authenticates, saves the token to `auth/.session_token`, and continues automatically.

**What happens automatically from 09:00:**

| Time | What the bot logs |
|---|---|
| 09:00 | Loads capital, reconciles yesterday's open trades |
| 09:15 | Filters universe, selects strategy, logs which and why |
| 09:25–10:15 | Scans for signals every 15 min, logs `[DRY_RUN]` fake orders |
| Every 60s | Prints live dashboard: positions, P&L, SL, target |
| 15:15 | Cancels all orders, closes all positions (fake in DRY_RUN) |
| 15:30 | Prints EOD summary, exits |

Press **Ctrl+C** at any time for a clean exit (EOD close runs automatically before shutdown).

---

## Step 6 — Verify paper trading is working

After the first paper day, run these checks:

```bash
# Capital row created
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT * FROM daily_capital').fetchall(): print(dict(r))
"

# Trades recorded
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT symbol, strategy, pnl, status FROM trades ORDER BY id').fetchall(): print(dict(r))
"

# Strategy selected each day
python -c "
import sqlite3; conn = sqlite3.connect('ledger/trades.db'); conn.row_factory = sqlite3.Row
for r in conn.execute('SELECT trade_date, selected, momentum_score, mean_rev_score, range_score FROM strategy_log').fetchall(): print(dict(r))
"
```

---

## Step 7 — Test recovery mode manually

Simulate a loss day and verify the bot reacts correctly:

```bash
python -c "
import sqlite3
from datetime import date, timedelta
conn = sqlite3.connect('ledger/trades.db')
yesterday = str(date.today() - timedelta(days=1))
conn.execute('''
    INSERT OR REPLACE INTO daily_capital
    (trade_date, opening_capital, realized_pnl, closing_capital, recovery_mode)
    VALUES (?, 1000.0, -60.0, 940.0, 0)
''', (yesterday,))
conn.commit()
print('Done. Restart the bot — expect RECOVERY MODE ACTIVE at 09:15.')
"
```

Expected log at 09:15:
```
[WARNING] RECOVERY MODE ACTIVE — risk_pct=30%, max_positions=1, min_score=0.70
```

---

## Step 8 — Go live (after 5 clean paper days)

1. Open `.env` and set:
   ```
   DRY_RUN=false
   ```
2. Ensure your Zerodha account has at least ₹1000 in available funds
3. Confirm KiteConnect subscription is active at kite.trade
4. **Start on a Monday** — the bot seeds ₹1000 on its first ever run
5. Run:
   ```bash
   python main.py
   ```

Real MIS orders are now placed. Real SL and target orders go to the exchange. All positions are force-closed at 15:15 IST every day.

---

## Step 9 — Daily routine (once live)

| Time | What you do |
|---|---|
| 08:40–08:55 IST | Open terminal, run `python main.py` |
| 08:55–09:00 IST | Complete browser login, paste `request_token` into terminal |
| 09:00–15:30 IST | Bot runs fully automatically — minimize the terminal |
| 15:30 IST | Bot prints EOD summary and exits |
| Next morning | Repeat from 08:40 — Kite token expires at midnight IST every day |

You do not need to do anything between 09:00 and 15:30.

---

## Step 10 — Log files

Written to `logs/trade_mission_YYYYMMDD.log`. Rotates at 5 MB, keeps 5 backups.

```bash
# Windows PowerShell — tail the live log
Get-Content logs\trade_mission_$(Get-Date -Format yyyyMMdd).log -Wait

# Or open the file in VS Code / Notepad — it updates in real time
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'kiteconnect'` | Run `pip install -r requirements.txt` |
| `RuntimeError: KITE_API_KEY is not set` | Check `.env` exists and has correct values |
| `Token not found or expired` | Normal — complete the browser login step each morning |
| Bot exits immediately: "Not a trading day" | Weekend or NSE holiday. Check `NSE_HOLIDAYS` in `config/settings.py`. |
| `qty=0` warnings in logs | SL distance too large for capital. Bot skips that signal safely — normal early on. |
| Orders not placed | Confirm `DRY_RUN=false` in `.env` AND KiteConnect subscription is active |
| `PANIC REGIME — VIX > 25` | Extreme volatility. Bot skips the day intentionally to protect capital. |
| Kill-switch fires early | Day loss hit 4% of opening capital. All positions closed — this is by design. |
| `SL order FAILED` in logs | Bot immediately market-sold the entry. No unprotected position held. |
| DB locked error | Close any other process with `ledger/trades.db` open (e.g. DB Browser for SQLite). |
| `invalid request_token` | The token was already used or expired. Re-open the login URL and get a fresh one. |
| Bot stuck at login prompt after 09:00 | Login is blocking — complete it immediately. Strategy selection runs at 09:15. |

---

## Annual maintenance

| Task | When | What to do |
|---|---|---|
| Update NSE holidays | Every January | Update `NSE_HOLIDAYS` set in `config/settings.py` from NSE circular (nseindia.com, published each December) |
| Update Nifty 50 list | Every quarter | Check NSE announcements for constituent changes, update `NIFTY50_SYMBOLS` in `config/settings.py` |
| Renew KiteConnect | Monthly | Auto-renews at ₹2000/month if card on file; check kite.trade dashboard if bot fails at login |
