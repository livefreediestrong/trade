"""Third-party WSB aggregates, kept separate from raw posts and trading inputs.

Public documented endpoints only. Process-memory cache; no account data, raw
posts, persistent history, broker calls or model calls are sent or stored here.
"""
from __future__ import annotations

import copy
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

TTL = 15 * 60
MAX_BYTES = 512 * 1024
PROVIDERS = {
    "tradestie": {"name": "Tradestie", "url": "https://tradestie.com/api/v1/apps/reddit",
                  "docs": "https://tradestie.com/apps/reddit/api/", "limit": 50},
    "apewisdom": {"name": "ApeWisdom", "url": "https://apewisdom.io/api/v1.0/filter/wallstreetbets",
                  "docs": "https://apewisdom.io/api/", "limit": 100},
}
_locks = {name: threading.Lock() for name in PROVIDERS}
_cache: dict[str, dict] = {}
_SYMBOL = re.compile(r"[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z])?")


def _iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None


def _number(value, *, count=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number):
        return None
    if count:
        return int(number) if 0 <= number <= 1_000_000_000 and number.is_integer() else None
    return number if -1 <= number <= 1 else None


def _normalize(name, payload):
    values = payload if name == "tradestie" else payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(values, list):
        raise ValueError("Unexpected response format")
    rows, seen, rejected = [], set(), 0
    for raw in values[:PROVIDERS[name]["limit"]]:
        if not isinstance(raw, dict):
            rejected += 1
            continue
        symbol = str(raw.get("ticker") or "").strip().upper()
        count_key = "no_of_comments" if name == "tradestie" else "mentions"
        count = _number(raw.get(count_key), count=True)
        if not _SYMBOL.fullmatch(symbol) or count is None or symbol in seen:
            rejected += 1
            continue
        seen.add(symbol)
        row = {"symbol": symbol, "count": count}
        if name == "tradestie":
            label = str(raw.get("sentiment") or "").lower()
            row.update(tone=label if label in {"bullish", "bearish", "neutral"} else "unclear",
                       score=_number(raw.get("sentiment_score")))
        else:
            row.update(rank=_number(raw.get("rank"), count=True),
                       previous_rank=_number(raw.get("rank_24h_ago"), count=True),
                       previous_mentions=_number(raw.get("mentions_24h_ago"), count=True),
                       upvotes=_number(raw.get("upvotes"), count=True))
        rows.append(row)
    if values and not rows:
        raise ValueError("No usable records in response")
    rows.sort(key=lambda r: (r.get("rank") or 1_000_000, -r["count"], r["symbol"]))
    return rows, rejected


def _retry_after(response, now):
    value = response.headers.get("Retry-After", "")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    return min(86400, max(0, seconds)) if math.isfinite(seconds) else 0


def _read(name):
    with _locks[name]:
        now = time.time()
        old = _cache.get(name, {})
        if 0 <= now - old.get("checked_ts", -math.inf) and now < old.get("next_ts", 0):
            return copy.deepcopy(dict(old["payload"], cached=True))
        provider = PROVIDERS[name]
        result = dict(provider=name, name=provider["name"], docs=provider["docs"],
                      ok=False, rows=[], checked_at=_iso(now), fetched_at=None,
                      source_updated_at=None, freshness="unverified", cached=False,
                      error=None, rejected_rows=0, last_success_at=old.get("last_success_at"))
        failures = old.get("failures", 0) + 1
        delay = min(900, 30 * 2 ** min(failures - 1, 5))
        try:
            with requests.get(provider["url"], timeout=(3, 6), stream=True,
                              allow_redirects=False, headers={
                                  "User-Agent": "TomahawkResearch/1.0 (WSB aggregate reader)",
                                  "Accept": "application/json"}) as response:
                if response.status_code != 200:
                    if response.status_code in (401, 402, 403):
                        delay = 3600
                    delay = max(delay, _retry_after(response, now))
                    result["error"] = f"Source unavailable (HTTP {response.status_code}); retrying later."
                else:
                    import json
                    body = bytearray()
                    for chunk in response.iter_content(16384):
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise ValueError("Response exceeded size limit")
                    rows, rejected = _normalize(name, json.loads(body))
                    result.update(ok=True, rows=rows, rejected_rows=rejected,
                                  fetched_at=_iso(time.time()), last_success_at=_iso(time.time()))
                    failures, delay = 0, TTL
        except (requests.RequestException, ValueError, OSError, OverflowError):
            # Do not expose exception URLs or masquerade a failed fetch as no activity.
            result["error"] = "Could not read this source. It will retry automatically."
        finished = time.time()
        result["next_check_at"] = _iso(finished + delay)
        _cache[name] = dict(checked_ts=finished, next_ts=finished + delay,
                            last_success_at=result["last_success_at"], failures=failures,
                            payload=copy.deepcopy(result))
        return result


def snapshot(*, enabled=True):
    enabled = enabled and os.environ.get("COMMUNITY_FEEDS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
    result = {"enabled": bool(enabled), "display_only": True, "providers": {},
              "note": "Both services summarize WSB and may overlap. Counts are not added together. "
                      "Attention is not a positive outlook. Source update times and underlying coverage are unverified."}
    if not enabled:
        result["note"] = "Community feeds are off. Social research in Settings controls these reads."
        return result
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="community-feeds") as pool:
        jobs = {name: pool.submit(_read, name) for name in PROVIDERS}
        for name, job in jobs.items():
            try:
                result["providers"][name] = job.result()
            except Exception:
                result["providers"][name] = dict(provider=name, name=PROVIDERS[name]["name"],
                    docs=PROVIDERS[name]["docs"], ok=False, rows=[], error="Source unavailable; retry later.",
                    freshness="unverified", source_updated_at=None)
    return result
