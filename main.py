"""
Trade Mission — Zerodha KiteConnect Intraday Bot
Run: python main.py
"""
import logging
import sys
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

import schedule
import colorlog

from config.settings import (
    IST, DRY_RUN, LOG_LEVEL, LOG_DIR, NSE_HOLIDAYS,
    MAX_OPEN_POSITIONS, LAST_ENTRY_TIME, ACTIVE_AI_BRAIN,
    ANTHROPIC_API_KEY, GEMINI_API_KEY, RISK_PER_TRADE_PCT, NEWS_FETCH_ENABLED,
    PARTIAL_PROFIT_ENABLED, PARTIAL_PROFIT_RATIO,
    PARTIAL_TARGET_ATR_MULT,
)
from auth.kite_auth import get_kite
from data.universe import get_base_universe, filter_universe, get_premarket_snapshot
from data.market_intelligence import get_market_intelligence
from strategies.selector import select_strategy, get_market_context
from strategies import momentum, mean_reversion, range_trading
from orders.order_manager import (
    place_entry_order, place_sl_order, place_target_order,
    place_partial_target_order, modify_sl_order, place_market_sell,
)
from monitor.position_monitor import run_monitor_cycle, print_live_dashboard
from eod.eod_closer import run_eod_close
from ledger.db import initialize_db, get_connection
from ledger.tracker import (
    get_today_capital, record_trade_entry, record_trade_exit,
    update_daily_pnl, record_eod_compound,
    print_daily_summary, reconcile_previous_day,
    get_adaptive_risk_pct,
)
from utils.correlation_filter import filter_correlated
from ai import claude_brain, gemini_brain

# ── Globals ───────────────────────────────────────────────────────────────────
_kite           = None
_conn           = None
_universe_df    = None
_strategy_name  = None
_strategy_config: dict = {}
_open_trades:    dict  = {}   # {db_trade_id: trade_dict}
_daily_capital:  float = 0.0
_realized_pnl:   float = 0.0
_entries_stopped = False
_eod_done        = False
_shutdown_done   = False

# AI state
_ai_signals:       list  = []   # pre-fetched signals from AI at 09:15
_premarket_ctx:    dict  = {}   # pre-market context for the day
_market_ctx:       dict  = {}   # Nifty/VIX context for the day
_market_intel:     dict  = {}   # news headlines + events calendar
_adaptive_risk:    float = RISK_PER_TRADE_PCT  # adjusted risk per trade
_monitor_ai_tick:  int   = 0    # counts monitor cycles; AI advice every 5

# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(IST).strftime("%Y%m%d")
    log_file = LOG_DIR / f"trade_mission_{today}.log"

    console = colorlog.StreamHandler()
    console.setFormatter(colorlog.ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        log_colors={
            "DEBUG":    "white",
            "INFO":     "cyan",
            "WARNING":  "yellow",
            "ERROR":    "red",
            "CRITICAL": "bold_red",
        },
    ))
    fh = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    root.addHandler(console)
    root.addHandler(fh)
    return logging.getLogger("main")


logger = setup_logging()


def is_trading_day() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    if now.date() in NSE_HOLIDAYS:
        return False
    return True


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


def _safe_close_conn():
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None

# ── Scheduled jobs ────────────────────────────────────────────────────────────

def job_premarket():
    global _kite, _conn, _daily_capital, _realized_pnl
    if not is_trading_day():
        logger.info("Not a trading day — skipping.")
        return

    logger.info("=== PRE-MARKET: Initialising ===")
    if DRY_RUN:
        logger.warning("*** DRY-RUN MODE — no real orders will be placed ***")

    _conn = get_connection()
    initialize_db()

    try:
        _kite = get_kite()
    except RuntimeError as e:
        logger.critical(str(e))
        sys.exit(1)

    reconcile_previous_day(_conn, _kite)
    _daily_capital = get_today_capital(_conn)
    _realized_pnl  = 0.0
    logger.info(f"Today's capital: Rs{_daily_capital:.2f}")

    try:
        premarket = get_premarket_snapshot(_kite)
        logger.info(f"Pre-market snapshot: {len(premarket)} stocks loaded.")
    except Exception as e:
        logger.warning(f"Pre-market snapshot failed (non-fatal): {e}")


def job_market_open():
    global _universe_df, _strategy_name, _strategy_config
    global _ai_signals, _premarket_ctx, _market_ctx, _market_intel, _adaptive_risk

    if not is_trading_day() or _kite is None:
        return

    logger.info("=== MARKET OPEN: Filtering universe & selecting strategy ===")
    try:
        _universe_df = filter_universe(_kite)
    except Exception as e:
        logger.error(f"Universe filter failed: {e}")
        return

    if _universe_df is None or _universe_df.empty:
        logger.warning("Universe filter returned no stocks.")
        return

    # ── Market Intelligence (news + events) ────────────────────────────────
    if NEWS_FETCH_ENABLED:
        try:
            _market_intel = get_market_intelligence()
            logger.info(f"Market Intel: {_market_intel.get('summary', 'fetched')}")
            if _market_intel.get("has_high_impact_today"):
                alerts = [e["event"] for e in _market_intel.get("event_alerts", [])]
                logger.warning(f"⚠️ HIGH-IMPACT EVENT TODAY: {', '.join(alerts)}")
        except Exception as e:
            logger.warning(f"Market intelligence fetch failed (non-fatal): {e}")
            _market_intel = {}
    else:
        _market_intel = {}

    # ── Adaptive risk (win-rate based) ─────────────────────────────────────
    if _conn:
        try:
            _adaptive_risk = get_adaptive_risk_pct(_conn, RISK_PER_TRADE_PCT)
        except Exception as e:
            logger.debug(f"Adaptive risk calc failed: {e}")
            _adaptive_risk = RISK_PER_TRADE_PCT

    # Rule-based strategy selection (always runs as fallback)
    _strategy_name, _strategy_config = select_strategy(_kite, _universe_df, _conn)
    _premarket_ctx = _strategy_config.get("premarket") or {}
    _market_ctx    = _strategy_config.get("market_context") or {}

    if _strategy_name == "skip":
        logger.warning(f"Rule-based selector skipping today: {_strategy_config.get('reason')}")
    else:
        logger.info(f"Rule-based strategy locked: {_strategy_name.upper()}")

    # ── AI brain (with market intelligence) ─────────────────────────
    try:
        ai_strategy, ai_signals = "skip", []
        if ACTIVE_AI_BRAIN == "gemini" and GEMINI_API_KEY:
            logger.info("Calling Gemini AI for trade decisions (with news + events)...")
            ai_strategy, ai_signals = gemini_brain.get_trade_signals(
                _kite, _universe_df, _daily_capital, _conn,
                _premarket_ctx, _market_ctx, _market_intel,
            )
        elif ACTIVE_AI_BRAIN == "claude" and ANTHROPIC_API_KEY:
            logger.info("Calling Claude AI for trade decisions (with news + events)...")
            ai_strategy, ai_signals = claude_brain.get_trade_signals(
                _kite, _universe_df, _daily_capital, _conn,
                _premarket_ctx, _market_ctx, _market_intel,
            )
        else:
            logger.info(f"No API key set for {ACTIVE_AI_BRAIN} — using rule-based strategy only")

        if ai_signals:
            # Apply sector correlation filter
            ai_signals = filter_correlated(ai_signals, _open_trades)
            _ai_signals = ai_signals
            _strategy_name  = ai_strategy
            logger.info(
                f"{ACTIVE_AI_BRAIN.capitalize()} AI strategy: {ai_strategy.upper()} — "
                f"{len(ai_signals)} signal(s) queued (after correlation filter)"
            )
            for s in ai_signals:
                logger.info(
                    f"  ↳ {s['symbol']} entry={s['entry_price']} "
                    f"SL={s['stop_loss']} target={s['target_price']} — {s['rationale']}"
                )
        elif ai_strategy == "skip" and (GEMINI_API_KEY if ACTIVE_AI_BRAIN == "gemini" else ANTHROPIC_API_KEY):
            logger.warning(f"{ACTIVE_AI_BRAIN.capitalize()} AI recommends SKIP today — using rule-based fallback")
        elif (GEMINI_API_KEY if ACTIVE_AI_BRAIN == "gemini" else ANTHROPIC_API_KEY):
            logger.info(f"{ACTIVE_AI_BRAIN.capitalize()} AI found no signals — rule-based will scan at entry windows")
    except Exception as e:
        logger.error(f"AI call failed: {e} — using rule-based signals")

    # Write final strategy to DB (after potential AI override)
    if _conn:
        try:
            _conn.execute(
                "UPDATE daily_capital SET strategy_used=? WHERE trade_date=?",
                (_strategy_name, _today_str()),
            )
            _conn.commit()
        except Exception:
            pass


def job_entry_scan():
    global _open_trades, _entries_stopped, _universe_df, _strategy_name, _strategy_config
    global _ai_signals

    if not is_trading_day() or _kite is None:
        return
    if _entries_stopped or _eod_done:
        return
    if _strategy_name in (None, "skip"):
        if _universe_df is None:
            job_market_open()
        return

    if _now_ist().time() > LAST_ENTRY_TIME:
        return

    max_pos = _strategy_config.get("max_positions", MAX_OPEN_POSITIONS)
    if len(_open_trades) >= max_pos:
        return

    remaining_slots = max_pos - len(_open_trades)

    # ── AI signals (use first, if available) ────────────────────────
    if _ai_signals:
        logger.info(f"Executing {min(len(_ai_signals), remaining_slots)} AI signal(s)")
        signals_to_place = _ai_signals[:remaining_slots]
        _ai_signals  = _ai_signals[remaining_slots:]   # consume used slots
        for signal in signals_to_place:
            if len(_open_trades) >= max_pos:
                break
            _execute_signal(signal)
        return

    # ── Rule-based fallback ────────────────────────────────────────────────
    risk_pct = _adaptive_risk  # use adaptive risk instead of fixed
    vix      = _strategy_config.get("market_context", {}).get("vix", 15.0)

    try:
        if _strategy_name == "momentum":
            signals = momentum.get_signals(
                _kite, _universe_df, _daily_capital, _strategy_config,
                risk_pct=risk_pct, max_positions=remaining_slots,
            )
        elif _strategy_name == "mean_reversion":
            signals = mean_reversion.get_signals(
                _kite, _universe_df, _daily_capital, _strategy_config,
                current_vix=vix, risk_pct=risk_pct, max_positions=remaining_slots,
            )
        elif _strategy_name == "range_trading":
            signals = range_trading.get_signals(
                _kite, _universe_df, _daily_capital, _strategy_config,
                current_vix=vix, risk_pct=risk_pct, max_positions=remaining_slots,
            )
        else:
            return
    except Exception as e:
        logger.error(f"Rule-based signal generation error: {e}")
        return

    # Apply sector correlation filter to rule-based signals too
    signals = filter_correlated(signals, _open_trades)

    for signal in signals:
        if len(_open_trades) >= max_pos:
            break
        _execute_signal(signal)


def _execute_signal(signal: dict):
    global _open_trades
    import math

    symbol = signal["symbol"]
    total_qty = signal["quantity"]
    atr14 = signal.get("atr14", signal["entry_price"] * 0.01)
    logger.info(f"SIGNAL: {symbol} — {signal.get('rationale', '')}")

    entry_oid = place_entry_order(_kite, signal, DRY_RUN)
    if not entry_oid:
        logger.error(f"Entry order FAILED for {symbol} — aborting this trade")
        return

    sl_oid = place_sl_order(_kite, symbol, total_qty, signal["stop_loss"], DRY_RUN)
    if not sl_oid:
        logger.error(
            f"SL order FAILED for {symbol} — cancelling entry and aborting. "
            "This trade has no stop-loss protection."
        )
        place_market_sell(_kite, symbol, total_qty, DRY_RUN)
        return

    # ── Partial profit booking ─────────────────────────────────────────────
    partial_oid = None
    partial_qty = 0
    remaining_qty = total_qty
    partial_target_price = 0.0

    if PARTIAL_PROFIT_ENABLED and total_qty >= 2:
        partial_qty = math.floor(total_qty * PARTIAL_PROFIT_RATIO)
        remaining_qty = total_qty - partial_qty
        partial_target_price = round(
            signal["entry_price"] + PARTIAL_TARGET_ATR_MULT * atr14, 2
        )

        if partial_qty > 0:
            partial_oid = place_partial_target_order(
                _kite, symbol, partial_qty, partial_target_price, DRY_RUN
            )
            logger.info(
                f"Partial profit: {partial_qty}/{total_qty} shares target @ "
                f"{partial_target_price:.2f} (1×ATR), rest rides to full target"
            )

    # Full target for remaining quantity
    tgt_oid = place_target_order(
        _kite, symbol, remaining_qty, signal["target_price"], DRY_RUN
    )
    # Target order failure is non-fatal — monitor will handle exit manually

    trade_id = record_trade_entry(
        _conn,
        trade_date=_today_str(),
        symbol=symbol,
        order_id=entry_oid,
        sl_order_id=sl_oid or "",
        target_order_id=tgt_oid or "",
        direction=signal["direction"],
        quantity=total_qty,
        entry_price=signal["entry_price"],
        stop_loss=signal["stop_loss"],
        target_price=signal["target_price"],
        strategy=signal["strategy"],
        rationale=signal.get("rationale", ""),
    )

    _open_trades[trade_id] = {
        **signal,
        "quantity":                total_qty,
        "order_id":                entry_oid,
        "sl_order_id":             sl_oid,
        "target_order_id":         tgt_oid,
        "partial_target_order_id": partial_oid,
        "partial_qty":             partial_qty,
        "remaining_qty":           remaining_qty,
        "partial_target_price":    partial_target_price,
        "partial_booked":          False,
        "current_price":           signal["entry_price"],
        "unrealized_pnl":          0.0,
    }
    logger.info(
        f"Trade #{trade_id}: {symbol} {total_qty}@{signal['entry_price']:.2f} "
        f"SL={signal['stop_loss']:.2f} TGT={signal['target_price']:.2f}"
        + (f" | Partial: {partial_qty}@{partial_target_price:.2f}" if partial_qty else "")
    )


def job_monitor():
    global _open_trades, _realized_pnl, _entries_stopped, _eod_done, _monitor_ai_tick

    if not is_trading_day() or _kite is None or _eod_done:
        return

    # Step 1: Python trailing-SL + fill detection (always runs first)
    try:
        _open_trades, _realized_pnl, kill, lock = run_monitor_cycle(
            _kite, _open_trades, _daily_capital, _realized_pnl,
            _conn, _today_str(), DRY_RUN,
        )
    except Exception as e:
        logger.error(f"Monitor cycle error: {e}")
        return

    print_live_dashboard(_open_trades, _realized_pnl, _daily_capital, _strategy_name or "")

    # Step 2: Kill-switch / profit-lock (Python, not Claude)
    if kill:
        logger.critical("KILL-SWITCH triggered — closing all positions.")
        _entries_stopped = True
        _trigger_emergency_close("KILL_SWITCH")
        return

    if lock:
        logger.info("PROFIT LOCK triggered — banking the gains.")
        _entries_stopped = True
        _trigger_emergency_close("PROFIT_LOCK")
        return

    # Step 3: AI position advice (every 5 monitor cycles = ~5 min)
    _monitor_ai_tick += 1
    ai_key_exists = (GEMINI_API_KEY if ACTIVE_AI_BRAIN == "gemini" else ANTHROPIC_API_KEY)

    if ai_key_exists and _open_trades and _monitor_ai_tick % 5 == 0:
        try:
            current_market_ctx = dict(_market_ctx)
            try:
                current_market_ctx["nifty_ltp"] = get_market_context(_kite).get("nifty_ltp", 0)
            except Exception:
                pass

            if ACTIVE_AI_BRAIN == "gemini":
                advice = gemini_brain.get_position_advice(
                    _kite, _open_trades, _daily_capital, _realized_pnl, current_market_ctx,
                )
            else:
                advice = claude_brain.get_position_advice(
                    _kite, _open_trades, _daily_capital, _realized_pnl, current_market_ctx,
                )
                
            for action in advice:
                trade_id = action["trade_id"]
                act      = action["action"]
                new_sl   = action.get("new_sl")
                reason   = action.get("reason", "")
                trade    = _open_trades.get(trade_id)
                if not trade:
                    continue

                if act == "exit_now":
                    logger.warning(
                        f"{ACTIVE_AI_BRAIN.capitalize()} AI: exit {trade['symbol']} now — {reason}"
                    )
                    place_market_sell(_kite, trade["symbol"], trade["quantity"], DRY_RUN)
                    pnl = record_trade_exit(
                        _conn, trade_id,
                        trade.get("current_price", trade["entry_price"]),
                        "CLOSED",
                    )
                    _realized_pnl += pnl
                    del _open_trades[trade_id]
                    update_daily_pnl(_conn, _today_str())  # noqa: already imported at top

                elif act in ("tighten_sl", "trail_sl") and new_sl:
                    logger.info(
                        f"{ACTIVE_AI_BRAIN.capitalize()} AI: tighten SL {trade['symbol']} "
                        f"{trade['stop_loss']:.2f} → {new_sl:.2f} — {reason}"
                    )
                    modify_sl_order(
                        _kite, trade["sl_order_id"], new_sl, trade["quantity"], DRY_RUN
                    )
                    _open_trades[trade_id]["stop_loss"] = new_sl

        except Exception as e:
            logger.error(f"{ACTIVE_AI_BRAIN.capitalize()} AI position advice error: {e}")


def _trigger_emergency_close(reason: str):
    global _open_trades, _eod_done
    try:
        run_eod_close(_kite, _conn, _open_trades, _today_str(), DRY_RUN)
    except Exception as e:
        logger.error(f"Emergency close error: {e}")
    _open_trades = {}
    _eod_done = True
    if _conn:
        try:
            _conn.execute(
                "UPDATE daily_capital SET notes=? WHERE trade_date=?",
                (reason, _today_str()),
            )
            _conn.commit()
        except Exception:
            pass
    logger.warning(f"Emergency close ({reason}) complete.")


def job_eod_close():
    global _open_trades, _eod_done
    if not is_trading_day() or _kite is None:
        return
    if _eod_done:
        logger.info("EOD already done (kill-switch or profit-lock fired earlier).")
        return
    logger.warning("=== 15:15 EOD CLOSE ===")
    try:
        run_eod_close(_kite, _conn, _open_trades, _today_str(), DRY_RUN)
    except Exception as e:
        logger.error(f"EOD close error: {e}")
    _open_trades = {}
    _eod_done = True


def job_shutdown():
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True

    if not is_trading_day():
        return

    logger.info("=== 15:30 SHUTDOWN ===")
    if _conn:
        try:
            record_eod_compound(_conn)
            print_daily_summary(_conn, _today_str())
        except Exception as e:
            logger.error(f"Shutdown ledger error: {e}")
        _safe_close_conn()

    logger.info(f"Trading complete for {_today_str()}. Goodbye.")
    schedule.clear()
    sys.exit(0)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 65)
    print("  TRADE MISSION — Zerodha KiteConnect Intraday Bot")
    print(f"  Date: {_today_str()}    DRY_RUN: {DRY_RUN}")
    print("=" * 65 + "\n")

    if not is_trading_day():
        today = datetime.now(IST).date()
        if today in NSE_HOLIDAYS:
            logger.warning(f"Today ({today}) is an NSE holiday. Exiting.")
        else:
            logger.warning("Today is a weekend. Exiting.")
        sys.exit(0)

    schedule.every().day.at("09:00").do(job_premarket)
    schedule.every().day.at("09:15").do(job_market_open)
    schedule.every().day.at("09:25").do(job_entry_scan)
    schedule.every().day.at("09:30").do(job_entry_scan)
    schedule.every().day.at("09:45").do(job_entry_scan)
    schedule.every().day.at("10:00").do(job_entry_scan)
    schedule.every().day.at("10:15").do(job_entry_scan)
    schedule.every(60).seconds.do(job_monitor)
    schedule.every().day.at("15:15").do(job_eod_close)
    schedule.every().day.at("15:30").do(job_shutdown)

    logger.info("Scheduler ready. Waiting for 9:00 AM IST…")
    logger.info("Press Ctrl+C at any time — EOD close will run automatically.")

    # Catch-up logic for late starts
    now_time = _now_ist().time()
    from datetime import time as dt_time
    if dt_time(9, 0) <= now_time < dt_time(9, 15):
        logger.warning("Bot started between 9:00 and 9:15 AM. Running premarket catch-up instantly...")
        job_premarket()

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.warning("Ctrl+C — running EOD close before exit…")
        if _kite and not _eod_done:
            job_eod_close()
        job_shutdown()


if __name__ == "__main__":
    main()
