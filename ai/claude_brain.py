"""
Claude AI Brain - Trade Mission

Replaces rule-based strategy scoring with real Claude intelligence.
Claude reads live market data, indicators, context and returns structured
trade decisions. All execution (order placement, kill-switch, EOD close)
remains in Python - Claude only advises, Python executes.

Entry points:
  get_trade_signals(kite, universe_df, capital, conn, premarket, market_context)
      -> list[dict]  (signal dicts compatible with _execute_signal in main.py)

  get_position_advice(kite, open_trades, capital, realized_pnl, market_context)
      -> list[dict]  (position management instructions)

Both functions return [] on any failure - bot falls back to rule-based.
"""
import json
import logging
import sqlite3
from datetime import datetime

import pandas as pd
from kiteconnect import KiteConnect

from config.settings import (
    IST, CLAUDE_TRADE_MODEL, CLAUDE_MONITOR_MODEL, ANTHROPIC_API_KEY,
    MIS_LEVERAGE, MAX_OPEN_POSITIONS, RISK_PER_TRADE_PCT,
    MAX_DAILY_LOSS_PCT, PROFIT_LOCK_PCT, LAST_ENTRY_TIME,
)
from data.market_data import get_today_candles, get_daily_candles, get_nifty_vix, get_nifty_ltp
from indicators.technicals import compute_all, _safe_last
from utils.position_sizing import calculate_position_size

logger = logging.getLogger(__name__)


def _get_client():
    """Lazy-load Anthropic client - only imported when API key is present."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic>=0.40.0")
        return None


def _build_market_snapshot(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    capital: float,
    premarket: dict | None,
    market_context: dict,
    market_intel: dict | None = None,
) -> str:
    """
    Build a comprehensive market briefing string for Claude.
    Includes: capital state, Nifty, VIX, pre-market bias, sector trends,
    F&O PCR, top movers, and per-stock technical indicators.
    """
    vix = market_context.get("vix", 15.0)
    nifty_ltp = market_context.get("nifty_ltp", 0.0)
    nifty_gap = market_context.get("nifty_gap_pct", 0.0)
    prev_day_pct = market_context.get("prev_day_nifty_pct", 0.0)
    inside_bar = market_context.get("prev_day_was_inside_bar", False)

    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

    lines = [
        f"=== TRADE MISSION - Market Briefing {now_ist} ===",
        f"",
        f"ACCOUNT",
        f"  Today's capital : Rs{capital:.2f}",
        f"  Max risk/trade  : {RISK_PER_TRADE_PCT*100:.0f}% = Rs{capital*RISK_PER_TRADE_PCT:.2f}",
        f"  Max positions   : {MAX_OPEN_POSITIONS}",
        f"  Leverage cap    : {MIS_LEVERAGE}x",
        f"  Kill-switch     : loss > {MAX_DAILY_LOSS_PCT*100:.0f}% = Rs{capital*MAX_DAILY_LOSS_PCT:.2f}",
        f"  Profit lock     : gain > {PROFIT_LOCK_PCT*100:.0f}% = Rs{capital*PROFIT_LOCK_PCT:.2f}",
        f"",
        f"NIFTY 50",
        f"  Current         : {nifty_ltp:.2f}",
        f"  Today gap       : {nifty_gap:+.2f}%",
        f"  Prev day move   : {prev_day_pct:+.2f}%",
        f"  Inside bar      : {'YES' if inside_bar else 'no'}",
        f"",
        f"VOLATILITY",
        f"  India VIX       : {vix:.2f}",
        f"  Regime          : {'PANIC (skip trading)' if vix > 25 else 'HIGH' if vix > 20 else 'ELEVATED' if vix > 15 else 'NORMAL' if vix > 12 else 'LOW (range day)'}",
    ]

    if premarket:
        bias = premarket.get("market_bias", "neutral").upper()
        gift_chg = premarket.get("gift_nifty_change", 0.0)
        pcr = premarket.get("fno_pcr", 1.0)
        gainers = premarket.get("top_gainers", [])
        losers = premarket.get("top_losers", [])
        sectors = premarket.get("sector_trends", {})

        lines += [
            f"",
            f"PRE-MARKET INTELLIGENCE",
            f"  Overnight bias  : {bias} (Gift Nifty: {gift_chg:+.2f}%)",
            f"  F&O PCR         : {pcr:.2f}  {'(bullish - call heavy)' if pcr < 0.8 else '(bearish - put heavy)' if pcr > 1.2 else '(neutral)'}",
            f"  Top gainers     : {', '.join(gainers) if gainers else 'none'}",
            f"  Top losers      : {', '.join(losers) if losers else 'none'}",
        ]
        if sectors:
            lines.append(f"  Sectors         :")
            for name, data in sectors.items():
                chg = data.get("change_pct", 0)
                lines.append(f"    {name.upper():<10} {chg:+.2f}%")

    # FII/DII institutional flows
    fii_dii = (premarket or {}).get("fii_dii", {})
    if fii_dii.get("available"):
        fii_net = fii_dii.get("fii_net", 0)
        dii_net = fii_dii.get("dii_net", 0)
        total_net = fii_dii.get("total_net", 0)
        fii_trend = fii_dii.get("fii_trend", "neutral")
        lines += [
            f"",
            f"FII/DII INSTITUTIONAL FLOWS (previous day)",
            f"  FII (Foreign)   : Rs {fii_net:+,.0f} crore {'← NET BUYING' if fii_net > 0 else '← NET SELLING' if fii_net < 0 else '(flat)'}",
            f"  DII (Domestic)  : Rs {dii_net:+,.0f} crore",
            f"  Combined net    : Rs {total_net:+,.0f} crore",
            f"  FII trend       : {fii_trend.upper()}",
            f"  NOTE: FII flows drive 60-70% of Indian market direction.",
        ]

    # Market news headlines and events
    if market_intel:
        headlines = market_intel.get("headlines", [])
        events = market_intel.get("upcoming_events", [])
        event_alerts = market_intel.get("event_alerts", [])

        if headlines:
            lines += ["", "MARKET NEWS (last 24 hours - from Google News)"]
            for i, h in enumerate(headlines[:12], 1):
                source = f" - {h['source']}" if h.get('source') else ""
                age = f", {h['published']}" if h.get('published') else ""
                lines.append(f"  {i:2d}. \"{h['title']}\"{source}{age}")

        if event_alerts:
            lines += ["", "⚠️ HIGH-IMPACT EVENT ALERTS (read carefully!)"]
            for e in event_alerts:
                when = "TODAY" if e.get("is_today") else "TOMORROW"
                lines.append(f"  {when}: {e['event']}")
                lines.append(f"    Impact: {e['impact'].upper()}")
                lines.append(f"    Guidance: {e.get('expected_effect', 'Exercise caution')}")

        if events:
            non_alert_events = [e for e in events if e["impact"] != "high" or e["days_away"] > 1]
            if non_alert_events:
                lines += ["", "UPCOMING EVENTS (next 3 days)"]
                for e in non_alert_events[:5]:
                    lines.append(
                        f"  {e['date']} ({e['days_away']:+d}d): {e['event']} "
                        f"[{e['impact'].upper()}]"
                    )

    # Per-stock technical snapshot
    lines += ["", "UNIVERSE STOCKS (top 20 by gap size)", ""]

    stock_rows = []
    for _, row in universe_df.head(20).iterrows():
        sym = row["symbol"]
        try:
            candles_15m = get_today_candles(kite, sym, interval="15minute", only_complete=True)
            daily = get_daily_candles(kite, sym, days=22)

            gap_pct = round(float(row.get("gap_pct", 0)), 2)
            current = round(float(row.get("current_price", 0)), 2)
            prev_close = round(float(row.get("prev_close", 0)), 2)

            atr14 = 0.0
            rsi = None
            ema50 = None
            vol_ratio = None

            if not daily.empty and len(daily) >= 5:
                enriched_d = compute_all(daily)
                atr14 = round(float(_safe_last(enriched_d.get("atr_14", pd.Series(dtype=float)), default=current * 0.01)), 2)

            if not candles_15m.empty and len(candles_15m) >= 3:
                enriched = compute_all(candles_15m)
                rsi = round(float(_safe_last(enriched.get("rsi_14", pd.Series(dtype=float)))), 1) if not pd.isna(_safe_last(enriched.get("rsi_14", pd.Series(dtype=float)))) else None
                ema50 = round(float(_safe_last(enriched.get("ema_50", pd.Series(dtype=float)))), 2) if not pd.isna(_safe_last(enriched.get("ema_50", pd.Series(dtype=float)))) else None

            stock_rows.append({
                "symbol": sym,
                "price": current,
                "prev_close": prev_close,
                "gap_pct": gap_pct,
                "atr14": atr14,
                "rsi": rsi,
                "ema50": ema50,
            })
        except Exception as e:
            logger.debug(f"Snapshot error for {sym}: {e}")
            stock_rows.append({
                "symbol": sym,
                "price": float(row.get("current_price", 0)),
                "prev_close": float(row.get("prev_close", 0)),
                "gap_pct": float(row.get("gap_pct", 0)),
                "atr14": 0.0,
                "rsi": None,
                "ema50": None,
            })

    for s in stock_rows:
        rsi_str = f"RSI={s['rsi']}" if s["rsi"] is not None else "RSI=n/a"
        ema_str = f"EMA50={s['ema50']}" if s["ema50"] is not None else "EMA50=n/a"
        lines.append(
            f"  {s['symbol']:<15} price={s['price']:.2f}  gap={s['gap_pct']:+.1f}%"
            f"  ATR={s['atr14']:.2f}  {rsi_str}  {ema_str}"
        )

    lines += [
        "",
        "TRADING RULES (must be obeyed)",
        "  - Product type   : MIS (intraday only). ALL positions MUST be closed by 15:15 IST.",
        "  - Direction      : BUY only (long-only, no short selling).",
        "  - Stop-loss      : mandatory on every trade.",
        "  - Risk per trade : max {:.0f}% of capital = Rs{:.2f}".format(RISK_PER_TRADE_PCT * 100, capital * RISK_PER_TRADE_PCT),
        "  - Max positions  : {}.".format(MAX_OPEN_POSITIONS),
        "  - Last entry     : {}.".format(LAST_ENTRY_TIME.strftime("%H:%M IST")),
        "  - If VIX > 25    : return empty list (no trades).",
        "  - Leverage cap   : position value must not exceed capital × {}.".format(MIS_LEVERAGE),
    ]

    return "\n".join(lines)


def _build_position_snapshot(
    open_trades: dict,
    capital: float,
    realized_pnl: float,
    market_context: dict,
) -> str:
    """Build a position management briefing for mid-session Claude calls."""
    vix = market_context.get("vix", 15.0)
    nifty_ltp = market_context.get("nifty_ltp", 0.0)
    now_ist = datetime.now(IST).strftime("%H:%M IST")

    total_unrealized = sum(t.get("unrealized_pnl", 0) for t in open_trades.values())
    total_pnl = realized_pnl + total_unrealized
    pnl_pct = total_pnl / capital * 100 if capital else 0

    lines = [
        f"=== POSITION REVIEW {now_ist} ===",
        f"Capital: Rs{capital:.2f}  |  Realized: Rs{realized_pnl:+.2f}  |  Unrealized: Rs{total_unrealized:+.2f}  |  Total: Rs{total_pnl:+.2f} ({pnl_pct:+.1f}%)",
        f"Nifty: {nifty_ltp:.2f}  |  VIX: {vix:.2f}",
        "",
        "OPEN POSITIONS",
    ]

    for trade_id, t in open_trades.items():
        entry = t.get("entry_price", 0)
        curr = t.get("current_price", entry)
        sl = t.get("stop_loss", 0)
        target = t.get("target_price", 0)
        atr = t.get("atr14", entry * 0.01)
        unreal = t.get("unrealized_pnl", 0)
        profit_from_entry = curr - entry
        pct_to_target = (target - curr) / target * 100 if target else 0
        pct_to_sl = (curr - sl) / curr * 100 if curr else 0

        lines.append(
            f"  trade_id={trade_id}  {t['symbol']}  qty={t['quantity']}"
            f"  entry={entry:.2f}  ltp={curr:.2f}  sl={sl:.2f}  target={target:.2f}"
            f"  ATR={atr:.2f}  unreal=Rs{unreal:+.2f}"
            f"  to_target={pct_to_target:.1f}%  to_sl={pct_to_sl:.1f}%"
        )

    lines += [
        "",
        "INSTRUCTIONS",
        "For each position, respond with one of:",
        "  hold          - do nothing",
        "  exit_now      - close immediately at market (reason required)",
        "  tighten_sl    - move SL to a specific price (provide new_sl)",
        "  trail_sl      - move SL up to entry + offset (provide new_sl)",
        "",
        "RULES",
        "  - Never lower a stop-loss.",
        "  - Do not exit a position if unrealized P&L is positive and > 0.5 ATR - let it run.",
        "  - Recommend exit_now if: VIX has spiked above 25, or the trade has been open > 3 hours with no progress.",
        "  - If time is after 14:45 IST and position is profitable, recommend tightening SL to lock gains.",
    ]

    return "\n".join(lines)


# ── Tool schemas for structured output ───────────────────────────────────────

_TRADE_TOOL = {
    "name": "submit_trade_decisions",
    "description": (
        "Submit your intraday trade decisions for today. "
        "Return a list of trades to place. Return an empty list if conditions are unfavourable. "
        "Each trade must have a clear rationale based on the market data provided."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "strategy_today": {
                "type": "string",
                "enum": ["momentum", "mean_reversion", "range_trading", "skip"],
                "description": "The primary strategy for today based on market conditions.",
            },
            "strategy_rationale": {
                "type": "string",
                "description": "Why you chose this strategy today (1-2 sentences).",
            },
            "trades": {
                "type": "array",
                "description": "List of trades to place. Empty list = no trades today.",
                "items": {
                    "type": "object",
                    "required": ["symbol", "direction", "entry_price", "stop_loss", "target_price", "atr14", "rationale"],
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "NSE trading symbol e.g. RELIANCE",
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["BUY"],
                            "description": "Always BUY - long-only bot.",
                        },
                        "entry_price": {
                            "type": "number",
                            "description": "Entry price in Rs. Use current market price or slightly above breakout level.",
                        },
                        "stop_loss": {
                            "type": "number",
                            "description": "Stop-loss price. Must be below entry_price.",
                        },
                        "target_price": {
                            "type": "number",
                            "description": "Target price. Must give at least 1.5:1 reward-to-risk ratio.",
                        },
                        "atr14": {
                            "type": "number",
                            "description": "ATR(14) from daily candles. Used for trailing SL.",
                        },
                        "confidence": {
                            "type": "number",
                            "description": "Signal confidence 0.0 to 1.0.",
                        },
                        "rationale": {
                            "type": "string",
                            "description": "Why this trade - specific data points that support it.",
                        },
                    },
                },
            },
        },
        "required": ["strategy_today", "strategy_rationale", "trades"],
    },
}

_MONITOR_TOOL = {
    "name": "submit_position_advice",
    "description": "Submit position management instructions for each open trade.",
    "input_schema": {
        "type": "object",
        "properties": {
            "actions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["trade_id", "action"],
                    "properties": {
                        "trade_id": {
                            "type": "integer",
                            "description": "The trade_id integer from the position snapshot.",
                        },
                        "action": {
                            "type": "string",
                            "enum": ["hold", "exit_now", "tighten_sl", "trail_sl"],
                        },
                        "new_sl": {
                            "type": "number",
                            "description": "Required for tighten_sl and trail_sl. New stop-loss price.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Brief reason for this action.",
                        },
                    },
                },
            },
        },
        "required": ["actions"],
    },
}


# ── Public API ────────────────────────────────────────────────────────────────

def get_trade_signals(
    kite: KiteConnect,
    universe_df: pd.DataFrame,
    capital: float,
    conn: sqlite3.Connection,
    premarket: dict | None,
    market_context: dict,
    market_intel: dict | None = None,
) -> tuple[str, list[dict]]:
    """
    Ask Claude to analyse today's market and return trade signals.

    Returns: (strategy_name: str, signals: list[dict])
    Each signal dict is compatible with main._execute_signal().

    Returns ("skip", []) on any failure - bot falls back to rule-based.
    """
    client = _get_client()
    if client is None:
        return "skip", []

    if universe_df is None or universe_df.empty:
        return "skip", []

    try:
        briefing = _build_market_snapshot(kite, universe_df, capital, premarket, market_context, market_intel)

        system_prompt = (
            "You are an expert Indian intraday trader managing a Zerodha account. "
            "You trade Nifty 50 stocks using MIS (intraday) product - all positions MUST close by 15:15 IST. "
            "You are long-only. Your goal is to maximise end-of-day profit while strictly respecting risk rules. "
            "You have deep knowledge of Opening Range Breakout (momentum), RSI oversold bounce (mean reversion), "
            "and range trading strategies. You also understand Indian market microstructure: "
            "pre-open session, FII/DII flows, F&O expiry effects, sector rotation. "
            "\n\nIMPORTANT: You also have access to LIVE MARKET NEWS HEADLINES and an EVENTS CALENDAR. "
            "Factor these into your decisions:\n"
            "- If there are HIGH-IMPACT EVENTS today (elections, RBI policy, budget), adjust strategy accordingly.\n"
            "- If FII are NET SELLING heavily, lean bearish - avoid momentum, prefer defensive mean reversion.\n"
            "- If news headlines indicate panic or crisis, consider skipping or wait for dip to buy.\n"
            "- On F&O expiry days, avoid range trading (gamma breaks ranges). Tighter SLs recommended.\n"
            "- Election result days: if market gaps down on uncertainty, it often recovers - consider buying the dip after 10 AM.\n\n"
            "Be decisive. If conditions are good, place trades. If conditions are unfavourable, return an empty list. "
            "Use the submit_trade_decisions tool to return your structured decisions."
        )

        logger.info("Calling Claude for trade decisions...")
        response = client.messages.create(
            model=CLAUDE_TRADE_MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=[_TRADE_TOOL],
            tool_choice={"type": "tool", "name": "submit_trade_decisions"},
            messages=[{"role": "user", "content": briefing}],
        )

        # Extract tool use result
        tool_result = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_trade_decisions":
                tool_result = block.input
                break

        if not tool_result:
            logger.warning("Claude returned no tool_use block - falling back to rule-based")
            return "skip", []

        strategy = tool_result.get("strategy_today", "skip")
        strategy_rationale = tool_result.get("strategy_rationale", "")
        raw_trades = tool_result.get("trades", [])

        logger.info(
            f"Claude chose strategy: {strategy.upper()} - {strategy_rationale} "
            f"| {len(raw_trades)} trade(s) proposed"
        )

        if strategy == "skip" or not raw_trades:
            return strategy, []

        # Convert Claude's trades into signal dicts compatible with _execute_signal()
        signals = []
        for t in raw_trades:
            symbol = t.get("symbol", "").strip().upper()
            entry = float(t.get("entry_price", 0))
            sl = float(t.get("stop_loss", 0))
            target = float(t.get("target_price", 0))
            atr14 = float(t.get("atr14", entry * 0.01))

            if not symbol or entry <= 0 or sl <= 0 or target <= 0:
                logger.warning(f"Claude trade skipped - missing/invalid fields: {t}")
                continue
            if sl >= entry:
                logger.warning(f"Claude trade {symbol} skipped - SL {sl} >= entry {entry}")
                continue
            if target <= entry:
                logger.warning(f"Claude trade {symbol} skipped - target {target} <= entry {entry}")
                continue
            rr = (target - entry) / (entry - sl) if (entry - sl) > 0 else 0
            if rr < 1.2:
                logger.warning(f"Claude trade {symbol} skipped - R:R {rr:.2f} < 1.2:1")
                continue

            qty = calculate_position_size(capital, entry, sl, RISK_PER_TRADE_PCT)
            if qty <= 0:
                logger.warning(f"Claude trade {symbol} skipped - qty=0 (capital Rs{capital:.0f}, SL dist={entry-sl:.2f})")
                continue

            # Look up instrument_token from universe_df (informational only - not used by order_manager)
            token_row = universe_df[universe_df["symbol"] == symbol]
            instrument_token = int(token_row.iloc[0]["instrument_token"]) if not token_row.empty and token_row.iloc[0]["instrument_token"] else None

            signals.append({
                "symbol":           symbol,
                "exchange":         "NSE",
                "instrument_token": instrument_token,
                "direction":        "BUY",
                "entry_price":      round(entry, 2),
                "stop_loss":        round(sl, 2),
                "target_price":     round(target, 2),
                "atr14":            round(atr14, 2),
                "quantity":         qty,
                "strategy":         strategy,
                "rationale":        t.get("rationale", ""),
                "score":            float(t.get("confidence", 0.8)),
            })

        logger.info(f"Claude signals validated: {len(signals)}/{len(raw_trades)} passed checks")
        return strategy, signals

    except Exception as e:
        logger.error(f"Claude trade signal call failed: {e} - falling back to rule-based")
        return "skip", []


def get_position_advice(
    kite: KiteConnect,
    open_trades: dict,
    capital: float,
    realized_pnl: float,
    market_context: dict,
) -> list[dict]:
    """
    Ask Claude for mid-session position management advice.

    Returns list of action dicts:
      {"trade_id": int, "action": "hold"|"exit_now"|"tighten_sl"|"trail_sl",
       "new_sl": float|None, "reason": str}

    Returns [] on any failure or if no open trades.
    """
    client = _get_client()
    if client is None or not open_trades:
        return []

    try:
        snapshot = _build_position_snapshot(open_trades, capital, realized_pnl, market_context)

        system_prompt = (
            "You are an expert intraday position manager for a Zerodha account. "
            "You manage open MIS (intraday) positions. "
            "Your job is to protect profits, cut losses early, and let winners run. "
            "You must never lower a stop-loss. "
            "Be conservative - if a trade is healthy and moving in the right direction, say 'hold'. "
            "Only recommend exits for genuine risk reasons, not just because the trade is not moving. "
            "Use the submit_position_advice tool to return your structured instructions."
        )

        response = client.messages.create(
            model=CLAUDE_MONITOR_MODEL,
            max_tokens=1024,
            system=system_prompt,
            tools=[_MONITOR_TOOL],
            tool_choice={"type": "tool", "name": "submit_position_advice"},
            messages=[{"role": "user", "content": snapshot}],
        )

        tool_result = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_position_advice":
                tool_result = block.input
                break

        if not tool_result:
            return []

        actions = tool_result.get("actions", [])

        # Validate: never lower SL
        validated = []
        for action in actions:
            trade_id = action.get("trade_id")
            act = action.get("action", "hold")
            new_sl = action.get("new_sl")
            reason = action.get("reason", "")

            trade = open_trades.get(trade_id)
            if not trade:
                logger.debug(f"Claude position advice: unknown trade_id={trade_id}, skipping")
                continue

            if act in ("tighten_sl", "trail_sl") and new_sl is not None:
                current_sl = trade.get("stop_loss", 0)
                if float(new_sl) <= current_sl:
                    logger.debug(f"Claude wanted to lower SL for {trade['symbol']} - rejected")
                    continue

            logger.info(
                f"Claude position advice: trade_id={trade_id} {trade.get('symbol','?')} -> {act}"
                + (f" new_sl={new_sl:.2f}" if new_sl else "")
                + (f" reason: {reason}" if reason else "")
            )
            validated.append({
                "trade_id": trade_id,
                "action":   act,
                "new_sl":   float(new_sl) if new_sl else None,
                "reason":   reason,
            })

        return validated

    except Exception as e:
        logger.error(f"Claude position advice call failed: {e}")
        return []
