"""Read-only social attention intelligence for research context.

The adapter uses Reddit's public JSON endpoints only. It is deliberately
display-only: social activity can explain attention and uncertainty, but never
creates or approves a trade.
"""
from __future__ import annotations

import html
import hashlib
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_TTL = 60.0
_UA = "daytrade-signal-desk/1.0 (research; read-only)"
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5})\b|(?<![A-Za-z0-9])([A-Z]{2,5})(?![A-Za-z0-9])")
_POSITIVE = {"bullish", "calls", "moon", "squeeze", "breakout", "buy", "rocket", "long", "upside"}
_NEGATIVE = {"bearish", "puts", "dump", "sell", "short", "fraud", "scam", "downside", "bagholder"}
_STOPWORDS = {
    "THE", "AND", "FOR", "THIS", "THAT", "WITH", "FROM", "HAVE", "JUST", "YOUR",
    "WHAT", "WHEN", "WILL", "BEEN", "ONLY", "VERY", "CALL", "PUT", "YOLO", "DD",
    "CEO", "CFO", "USA", "USD", "IMO", "ATH", "FOMO", "IV", "EPS",
}


def _clean_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(re.sub(r"\s+", " ", str(value or ""))).strip()
    return text[:limit]


def _tickers(text: str, allowed: set[str] | None = None) -> list[str]:
    found: list[str] = []
    for match in _TICKER_RE.finditer(text or ""):
        symbol = (match.group(1) or match.group(2) or "").upper()
        if symbol in _STOPWORDS or (allowed and symbol not in allowed):
            continue
        if symbol not in found:
            found.append(symbol)
    return found[:12]


def _sentiment(text: str) -> str:
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    pos, neg = len(words & {x.lower() for x in _POSITIVE}), len(words & {x.lower() for x in _NEGATIVE})
    if pos > neg:
        return "bullish"
    if neg > pos:
        return "bearish"
    return "uncertain"


def _reddit_rows(subreddit: str, limit: int = 50) -> list[dict[str, Any]]:
    try:
        response = requests.get(
            f"https://www.reddit.com/r/{subreddit}/new.json",
            params={"limit": min(max(limit, 1), 100), "raw_json": 1},
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        children = (response.json().get("data") or {}).get("children") or []
    except (OSError, ValueError, requests.RequestException):
        return []
    rows: list[dict[str, Any]] = []
    for child in children:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        title = _clean_text(data.get("title"))
        body = _clean_text(data.get("selftext"), 700)
        text = f"{title} {body}".strip()
        if not text:
            continue
        created = float(data.get("created_utc") or 0)
        rows.append({
            "source": "reddit_wsb",
            "source_id": str(data.get("id") or data.get("name") or ""),
            "thread_id": str(data.get("id") or ""),
            "author_hash": hashlib.sha256(
                str(data.get("author") or "[deleted]").encode("utf-8")
            ).hexdigest()[:16],
            "created_at": datetime.fromtimestamp(created, timezone.utc).isoformat() if created > 0 else None,
            "title": title,
            "excerpt": body[:300],
            "ticker_candidates": _tickers(text),
            "sentiment": _sentiment(text),
            "score": int(data.get("score") or 0),
            "comments": int(data.get("num_comments") or 0),
            "permalink": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else None,
        })
    return rows


def _summarize(rows: list[dict[str, Any]], watchlist: list[str]) -> dict[str, Any]:
    allowed = {str(t).upper().lstrip("$") for t in watchlist if t}
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tickers = _tickers(f"{row.get('title', '')} {row.get('excerpt', '')}", allowed) if allowed else row.get("ticker_candidates", [])
        for ticker in tickers:
            by_ticker.setdefault(ticker, []).append(row)
    pulse = []
    for ticker, items in by_ticker.items():
        bullish = sum(i.get("sentiment") == "bullish" for i in items)
        bearish = sum(i.get("sentiment") == "bearish" for i in items)
        authors = len({i.get("author_hash") for i in items})
        pulse.append({
            "ticker": ticker,
            "mentions": len(items),
            "unique_threads": authors,
            "unique_authors": authors,
            "bullish": bullish,
            "bearish": bearish,
            "uncertain": len(items) - bullish - bearish,
            "attention_score": round(sum(max(0, int(i.get("score") or 0)) + max(0, int(i.get("comments") or 0)) for i in items) ** 0.5, 2),
            "independence": round(authors / len(items), 3) if items else 0,
            "quality": "low" if authors < 2 or authors / max(len(items), 1) < 0.35 else "moderate",
            "items": items[:5],
        })
    pulse.sort(key=lambda x: (x["mentions"], x["attention_score"]), reverse=True)
    return {"pulse": pulse[:30], "items": rows[:80], "display_only": True, "attention_only": True}


def snapshot(watchlist: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
    watchlist = list(watchlist or [])
    key = tuple(sorted(str(t).upper() for t in watchlist))
    now = time.time()
    with _lock:
        if not force and _cache.get("payload") and now - float(_cache.get("at") or 0) < _TTL and _cache.get("key") == key:
            return dict(_cache["payload"])
    rows = _reddit_rows("wallstreetbets")
    payload = {
        "ok": bool(rows),
        "provider": "reddit_wsb",
        "source": "public_reddit_json",
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "error": None if rows else "No public Reddit rows available",
        **_summarize(rows, watchlist),
    }
    with _lock:
        _cache.update(at=now, key=key, payload=payload)
    return dict(payload)


def public_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "reddit_wsb",
        "configured": True,
        "read_only": True,
        "display_only": True,
        "docs": "https://www.reddit.com/dev/api/",
    }
