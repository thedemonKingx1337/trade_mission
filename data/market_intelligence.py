"""
Market Intelligence — Trade Mission

Fetches Indian market news headlines from free RSS feeds and reads
a local events calendar. All data is fed to Claude's market briefing
so it can factor in real-world events (elections, RBI policy, budget,
earnings, global crises) when making trade decisions.

Entry point: get_market_intelligence() -> dict

All network calls are exception-safe — returns empty data on failure.
"""
import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from config.settings import IST, KNOWLEDGE_DIR

logger = logging.getLogger(__name__)

# ── RSS feed URLs (all free, no API key) ──────────────────────────────────────

_RSS_FEEDS = [
    {
        "url": "https://news.google.com/rss/search?q=Indian+stock+market+today&hl=en-IN&gl=IN&ceid=IN:en",
        "label": "Indian Stock Market",
    },
    {
        "url": "https://news.google.com/rss/search?q=Nifty+Sensex+NSE+BSE&hl=en-IN&gl=IN&ceid=IN:en",
        "label": "Nifty Sensex",
    },
    {
        "url": "https://news.google.com/rss/search?q=RBI+policy+India+economy+budget&hl=en-IN&gl=IN&ceid=IN:en",
        "label": "RBI Economy",
    },
]

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

_MAX_HEADLINES = 15
_MAX_AGE_HOURS = 24
_EVENTS_FILE = KNOWLEDGE_DIR / "market_events.json"


# ── News fetching ─────────────────────────────────────────────────────────────

def _fetch_single_feed(feed: dict) -> list[dict]:
    """Fetch and parse a single RSS feed. Returns list of headline dicts."""
    try:
        resp = requests.get(feed["url"], headers=_RSS_HEADERS, timeout=10)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        items = root.findall(".//item")

        headlines = []
        cutoff = datetime.now(IST) - timedelta(hours=_MAX_AGE_HOURS)

        for item in items:
            title_el = item.find("title")
            pub_el = item.find("pubDate")
            source_el = item.find("source")
            link_el = item.find("link")

            if title_el is None or title_el.text is None:
                continue

            title = title_el.text.strip()

            # Parse publication date
            pub_dt = None
            pub_str = ""
            if pub_el is not None and pub_el.text:
                try:
                    pub_dt = parsedate_to_datetime(pub_el.text)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=IST)
                    # Skip old headlines
                    if pub_dt < cutoff:
                        continue
                    # Relative time string
                    age = datetime.now(IST) - pub_dt
                    hours = int(age.total_seconds() // 3600)
                    if hours < 1:
                        minutes = int(age.total_seconds() // 60)
                        pub_str = f"{minutes}m ago"
                    else:
                        pub_str = f"{hours}h ago"
                except Exception:
                    pub_str = ""

            source = ""
            if source_el is not None and source_el.text:
                source = source_el.text.strip()

            link = ""
            if link_el is not None and link_el.text:
                link = link_el.text.strip()

            headlines.append({
                "title": title,
                "source": source,
                "published": pub_str,
                "published_dt": pub_dt,
                "link": link,
                "feed": feed["label"],
            })

        return headlines

    except Exception as e:
        logger.debug(f"RSS feed '{feed['label']}' fetch failed (non-fatal): {e}")
        return []


def fetch_market_news() -> list[dict]:
    """
    Fetch Indian market news headlines from multiple free RSS feeds.

    Returns up to 15 recent headlines (last 24 hours), deduplicated by title,
    sorted by recency. Returns [] on any failure.
    """
    all_headlines = []

    try:
        # Fetch all feeds in parallel (max 3 threads)
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_fetch_single_feed, f): f for f in _RSS_FEEDS}
            for future in as_completed(futures, timeout=15):
                try:
                    headlines = future.result()
                    all_headlines.extend(headlines)
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"RSS parallel fetch failed (non-fatal): {e}")

    # Deduplicate by normalized title
    seen_titles = set()
    unique = []
    for h in all_headlines:
        normalized = h["title"].lower().strip()
        if normalized not in seen_titles:
            seen_titles.add(normalized)
            unique.append(h)

    # Sort by recency (most recent first)
    unique.sort(
        key=lambda x: x.get("published_dt") or datetime.min.replace(tzinfo=IST),
        reverse=True,
    )

    # Limit to max headlines
    result = unique[:_MAX_HEADLINES]

    # Remove published_dt (not serializable, was only used for sorting)
    for h in result:
        h.pop("published_dt", None)

    logger.info(f"Market news: fetched {len(result)} headlines from {len(_RSS_FEEDS)} feeds")
    return result


# ── Events calendar ───────────────────────────────────────────────────────────

def _is_last_weekday_of_month(d: date, weekday: int) -> bool:
    """Check if date `d` is the last occurrence of `weekday` (0=Mon..6=Sun) in its month."""
    if d.weekday() != weekday:
        return False
    # Check if next week's same weekday is in a different month
    next_week = d + timedelta(days=7)
    return next_week.month != d.month


def _weekday_from_name(name: str) -> int:
    """Convert weekday name to number (monday=0 ... sunday=6)."""
    mapping = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    return mapping.get(name.lower().strip(), -1)


def get_market_events(lookahead_days: int = 3) -> list[dict]:
    """
    Read market events calendar and return events within ±lookahead_days of today.

    Returns list of event dicts:
      {"date": "2026-05-04", "event": "...", "impact": "high"|"medium"|"low",
       "expected_effect": "...", "is_today": bool, "is_tomorrow": bool, "days_away": int}
    """
    if not _EVENTS_FILE.exists():
        logger.debug(f"Events calendar not found: {_EVENTS_FILE}")
        return []

    try:
        with open(_EVENTS_FILE, "r", encoding="utf-8") as f:
            calendar = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read events calendar: {e}")
        return []

    today = datetime.now(IST).date()
    window_start = today - timedelta(days=1)  # include yesterday for context
    window_end = today + timedelta(days=lookahead_days)

    events = []

    # Fixed-date events
    for evt in calendar.get("fixed_events", []):
        try:
            evt_date = date.fromisoformat(evt["date"])
            if window_start <= evt_date <= window_end:
                days_away = (evt_date - today).days
                events.append({
                    "date": evt["date"],
                    "event": evt["event"],
                    "impact": evt.get("impact", "medium"),
                    "expected_effect": evt.get("expected_effect", ""),
                    "is_today": days_away == 0,
                    "is_tomorrow": days_away == 1,
                    "days_away": days_away,
                })
        except Exception:
            continue

    # Recurring events (weekly/monthly patterns)
    for evt in calendar.get("recurring", []):
        weekday_name = evt.get("weekday", "")
        weekday_num = _weekday_from_name(weekday_name)
        if weekday_num < 0:
            continue

        week_type = evt.get("week", "every")  # "last", "first", "every"

        # Check each day in the window
        d = window_start
        while d <= window_end:
            if d.weekday() == weekday_num:
                match = False
                if week_type == "every":
                    match = True
                elif week_type == "last":
                    match = _is_last_weekday_of_month(d, weekday_num)
                elif week_type == "first":
                    match = d.day <= 7

                if match:
                    days_away = (d - today).days
                    events.append({
                        "date": d.isoformat(),
                        "event": evt["event"],
                        "impact": evt.get("impact", "medium"),
                        "expected_effect": evt.get("expected_effect", ""),
                        "is_today": days_away == 0,
                        "is_tomorrow": days_away == 1,
                        "days_away": days_away,
                    })
            d += timedelta(days=1)

    # Sort: today first, then by date
    events.sort(key=lambda x: (not x["is_today"], not x["is_tomorrow"], x["days_away"]))

    logger.info(f"Market events: {len(events)} events in ±{lookahead_days}-day window")
    return events


# ── Master aggregator ─────────────────────────────────────────────────────────

def get_market_intelligence() -> dict:
    """
    Master market intelligence aggregator.

    Returns:
      {
        "headlines": [...],          # list of headline dicts
        "upcoming_events": [...],    # list of event dicts
        "event_alerts": [...],       # high-impact events today or tomorrow
        "has_high_impact_today": bool,
        "summary": str,              # one-line summary for logging
      }
    """
    headlines = fetch_market_news()
    events = get_market_events()

    # Extract high-impact alerts (today or tomorrow only)
    event_alerts = [
        e for e in events
        if e["impact"] == "high" and e["days_away"] in (0, 1)
    ]

    has_high_impact_today = any(e["is_today"] and e["impact"] == "high" for e in events)

    summary = (
        f"{len(headlines)} headlines, {len(events)} events, "
        f"{len(event_alerts)} high-impact alerts"
    )
    if event_alerts:
        alert_names = [e["event"] for e in event_alerts]
        summary += f" ⚠️ [{', '.join(alert_names)}]"

    logger.info(f"Market Intelligence: {summary}")

    return {
        "headlines": headlines,
        "upcoming_events": events,
        "event_alerts": event_alerts,
        "has_high_impact_today": has_high_impact_today,
        "summary": summary,
    }
