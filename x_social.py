"""Authorized X API research pulse; never an execution input."""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any

import requests

_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5})\b")


def public_status() -> dict[str, Any]:
    return {
        "enabled": bool(os.environ.get("X_BEARER_TOKEN")),
        "configured": bool(os.environ.get("X_BEARER_TOKEN")),
        "provider": "x_api_v2",
        "research_only": True,
        "endpoint": "/2/tweets/search/recent",
    }


def _sentiment(text: str) -> str:
    words = set(re.findall(r"[a-z]+", text.lower()))
    pos = len(words & {"bullish", "buy", "breakout", "calls", "upside"})
    neg = len(words & {"bearish", "sell", "breakdown", "puts", "downside"})
    return "bullish" if pos > neg else "bearish" if neg > pos else "uncertain"


def snapshot(watchlist: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
    token = (os.environ.get("X_BEARER_TOKEN") or "").strip()
    if not token:
        return {"ok": False, "enabled": False, "items": [], "error": "x_not_configured", "research_only": True}
    symbols = [str(s).upper().lstrip("$") for s in watchlist or [] if s]
    query = (os.environ.get("X_SEARCH_QUERY") or "").strip() or " OR ".join(f"${s}" for s in symbols[:20])
    try:
        max_results = max(10, min(100, int(os.environ.get("X_MAX_RESULTS", "25"))))
    except ValueError:
        max_results = 25
    base = (os.environ.get("X_API_BASE_URL") or "https://api.x.com").rstrip("/")
    try:
        response = requests.get(
            f"{base}/2/tweets/search/recent",
            params={"query": query, "max_results": max_results, "tweet.fields": "created_at,public_metrics,author_id"},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=8,
        )
        if response.status_code != 200:
            return {"ok": False, "enabled": True, "items": [], "error": f"x_http_{response.status_code}", "research_only": True}
        payload = response.json()
        users = {str(u.get("id")): u for u in payload.get("includes", {}).get("users", []) if isinstance(u, dict)}
        items = []
        seen: set[str] = set()
        for row in payload.get("data", []) or []:
            if not isinstance(row, dict) or not row.get("id") or str(row["id"]) in seen:
                continue
            seen.add(str(row["id"]))
            text = str(row.get("text") or "")
            author = users.get(str(row.get("author_id") or ""), {})
            items.append({
                "source": "x_api",
                "tweet_id": str(row["id"]),
                "url": f"https://x.com/i/web/status/{row['id']}",
                "author_id": str(row.get("author_id") or ""),
                "author_metrics": author.get("public_metrics") or {},
                "created_at": row.get("created_at"),
                "ticker_candidates": [s for s in symbols if f"${s}" in text.upper()] or [x.upper() for x in _TICKER_RE.findall(text)],
                "sentiment": _sentiment(text),
                "text": text[:500],
                "dedupe_key": hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16],
            })
        return {"ok": True, "enabled": True, "items": items, "count": len(items), "query": query, "research_only": True}
    except (requests.RequestException, ValueError) as exc:
        return {"ok": False, "enabled": True, "items": [], "error": str(exc)[:160], "research_only": True}
