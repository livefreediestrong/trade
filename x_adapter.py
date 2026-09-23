"""Authorized X (Twitter) API v2 adapter — research-only, off by default.

Compliance notes (read before touching this file):
  * Uses ONLY the official X API v2 "recent search" endpoint with a bearer
    token the operator provisions themselves. No scraping, no browser
    automation, no private/paywalled endpoints.
  * Disabled unless BOTH the desk's `social_enabled` config flag AND the
    `x_enabled` config flag are true, AND `X_BEARER_TOKEN` is set. Social
    research stays research-only/off-by-default per desk policy either way.
  * Enforces a local rate limiter under the X API's documented recent-search
    caps so this adapter cannot exceed its authorized quota even if called
    frequently by the polling loop.
  * Every row is display-only / attention-only, exactly like the Reddit and
    Stocktwits rows in `social_intelligence.py` — it can never create,
    approve, size, or gate a trade.

Docs: https://developer.x.com/en/docs/x-api/tweets/search/api-reference/get-tweets-search-recent
"""
from __future__ import annotations

import hashlib
import html
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
_UA = "daytrade-signal-desk/1.0 (research; read-only; authorized-api)"
_DEFAULT_QUERY_SUFFIX = "-is:retweet -is:reply lang:en"
_DEFAULT_MAX_RESULTS = 10
# X API v2 "recent search" (essential/basic tiers) documents a 300 req / 15 min
# app-auth cap. Default far below that so this research adapter never risks
# the account's authorized quota even under bursty polling.
_DEFAULT_RATE_LIMIT_PER_WINDOW = 30
_RATE_WINDOW_SEC = 900.0

_lock = threading.RLock()
_request_times: list[float] = []
_cache: dict[str, Any] = {}
_CACHE_TTL = 60.0


def _load_env() -> None:
    try:
        import data_sources as _ds

        _ds._load_env()
    except Exception:
        for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
            if not env_path.exists():
                continue
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except OSError:
                pass
            break


_load_env()


def bearer_token() -> str:
    return (os.environ.get("X_BEARER_TOKEN") or os.environ.get("TWITTER_BEARER_TOKEN") or "").strip()


def is_configured() -> bool:
    return bool(bearer_token())


def rate_limit_per_window() -> int:
    try:
        return max(1, int(os.environ.get("X_RATE_LIMIT_PER_15MIN") or _DEFAULT_RATE_LIMIT_PER_WINDOW))
    except (TypeError, ValueError):
        return _DEFAULT_RATE_LIMIT_PER_WINDOW


def max_results() -> int:
    try:
        return min(100, max(10, int(os.environ.get("X_API_MAX_RESULTS") or _DEFAULT_MAX_RESULTS)))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS


def query_suffix() -> str:
    return os.environ.get("X_SEARCH_QUERY_SUFFIX", _DEFAULT_QUERY_SUFFIX)


def is_enabled(cfg: dict[str, Any] | None) -> bool:
    """Authorized-adapter gate: social research must be on, x_enabled must be
    explicitly on, and a bearer token must be configured. All default off."""
    cfg = cfg or {}
    return bool(cfg.get("social_enabled")) and bool(cfg.get("x_enabled")) and is_configured()


def _prune_request_times(now: float) -> None:
    cutoff = now - _RATE_WINDOW_SEC
    while _request_times and _request_times[0] < cutoff:
        _request_times.pop(0)


def _try_reserve_request_slot() -> bool:
    """Local, conservative token-bucket over the authorized quota window.
    Returns False (no network call made) when the window budget is spent."""
    now = time.time()
    with _lock:
        _prune_request_times(now)
        if len(_request_times) >= rate_limit_per_window():
            return False
        _request_times.append(now)
        return True


def rate_limit_status() -> dict[str, Any]:
    now = time.time()
    with _lock:
        _prune_request_times(now)
        used = len(_request_times)
    limit = rate_limit_per_window()
    return {
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used),
        "window_sec": _RATE_WINDOW_SEC,
    }


def public_status() -> dict[str, Any]:
    return {
        "provider": "x_public",
        "configured": is_configured(),
        "docs": "https://developer.x.com/en/docs/x-api/tweets/search/api-reference/get-tweets-search-recent",
        "requires_config": ["social_enabled", "x_enabled", "X_BEARER_TOKEN"],
        "read_only": True,
        "display_only": True,
        "attention_only": True,
        "rate_limit": rate_limit_status(),
        "note": "Authorized X API v2 recent-search only. No scraping. Off by default.",
    }


def _clean_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(re.sub(r"\s+", " ", str(value or ""))).strip()
    return text[:limit]


def search_recent(symbol: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Public tweets mentioning `symbol` via the authorized recent-search
    endpoint. Empty list on any config/rate/network issue — never raises."""
    sym = (symbol or "").strip().upper().lstrip("$")
    if not sym or not is_configured():
        return []
    cache_key = sym
    now = time.time()
    with _lock:
        hit = _cache.get(cache_key)
        if hit and (now - hit["at"]) < _CACHE_TTL:
            return list(hit["rows"])
    if not _try_reserve_request_slot():
        return []
    query = f"${sym} {query_suffix()}".strip()
    params = {
        "query": query,
        "max_results": limit or max_results(),
        "tweet.fields": "created_at,author_id,public_metrics,lang",
    }
    headers = {"Authorization": f"Bearer {bearer_token()}", "User-Agent": _UA}
    try:
        response = requests.get(_SEARCH_URL, params=params, headers=headers, timeout=8)
        if response.status_code != 200:
            return []
        payload = response.json()
    except (OSError, ValueError, requests.RequestException):
        return []
    rows: list[dict[str, Any]] = []
    for tweet in (payload.get("data") or []):
        if not isinstance(tweet, dict):
            continue
        text = _clean_text(tweet.get("text"), 500)
        if not text:
            continue
        metrics = tweet.get("public_metrics") or {}
        author_hash = hashlib.sha256(str(tweet.get("author_id") or "unknown").encode("utf-8")).hexdigest()[:16]
        rows.append({
            "source": "x_public",
            "source_id": str(tweet.get("id") or ""),
            "thread_id": str(tweet.get("id") or ""),
            "author_hash": author_hash,
            "created_at": tweet.get("created_at"),
            "title": f"${sym} X post",
            "excerpt": text[:300],
            "ticker_candidates": [sym],
            "sentiment": None,
            "score": int(metrics.get("like_count") or 0),
            "comments": int(metrics.get("reply_count") or 0),
            "permalink": f"https://x.com/i/web/status/{tweet.get('id')}" if tweet.get("id") else None,
        })
    with _lock:
        _cache[cache_key] = {"at": now, "rows": rows}
    return rows


def rows_for_watchlist(symbols: list[str], cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not is_enabled(cfg):
        return []
    rows: list[dict[str, Any]] = []
    for symbol in (symbols or [])[:10]:
        rows.extend(search_recent(symbol))
    return rows
