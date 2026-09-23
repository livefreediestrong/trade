"""
Watchlist news stream — deepen Finnhub company-news + Yahoo; optional Benzinga stub.

Exposes watchlist_news for Advanced panel / state. Graceful degrade without keys.
"""
from __future__ import annotations

import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "key": "", "payload": None}
_CACHE_TTL = 120.0
_SOURCE_WEIGHT = {
    "benzinga": 1.0,
    "finnhub": 0.9,
    "google_news": 0.72,
    "gdelt": 0.68,
    "yahoo": 0.65,
}
_EVENT_PATTERNS = {
    "earnings": r"\b(earnings|quarterly results|eps|revenue|profit|loss)\b",
    "guidance": r"\b(guidance|outlook|forecast|raises|cuts|lowers)\b",
    "corporate_action": r"\b(acquire|acquisition|merger|deal|partnership|spin[- ]off)\b",
    "regulatory": r"\b(fda|sec|doj|ftc|approval|lawsuit|investigation|recall)\b",
    "capital": r"\b(offering|dilution|buyback|dividend|debt|bankruptcy)\b",
    "analyst": r"\b(upgrade|downgrade|price target|analyst|rating)\b",
    "executive": r"\b(ceo|cfo|resign|appointed|departure|executive)\b",
    "macro": r"\b(fed|fomc|cpi|inflation|jobs report|tariff|rates?)\b",
}
_HIGH_IMPACT_EVENTS = {"earnings", "guidance", "corporate_action", "regulatory", "capital"}


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


def benzinga_key() -> str:
    return (os.environ.get("BENZINGA_API_KEY") or "").strip()


def public_status() -> dict[str, Any]:
    fh = bool((os.environ.get("FINNHUB_API_KEY") or "").strip())
    return {
        "provider": "news_stream",
        "finnhub": fh,
        "yahoo": True,
        "benzinga": bool(benzinga_key()),
        "google_news": True,
        "gdelt": True,
        "configured": True,  # Yahoo always available best-effort
        "docs": {
            "finnhub": "https://finnhub.io/docs/api/company-news",
            "benzinga": "https://docs.benzinga.io/benzinga-apis/news-v2/news",
            "yahoo": "Yahoo finance search (no key)",
            "google_news": "Google News RSS search (no key)",
            "gdelt": "https://api.gdeltproject.org/api/v2/doc/doc (no key)",
        },
    }


def _benzinga_news(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    key = benzinga_key()
    if not key:
        return []
    # Stub client — documented path; fail soft if plan/endpoint differs
    url = "https://api.benzinga.com/api/v2/news"
    try:
        r = requests.get(
            url,
            params={"token": key, "tickers": symbol.upper(), "pageSize": limit},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        rows = data if isinstance(data, list) else (data.get("data") or [])
        out = []
        for n in rows[:limit]:
            if not isinstance(n, dict):
                continue
            title = n.get("title") or n.get("headline")
            if not title:
                continue
            out.append(
                {
                    "title": title,
                    "publisher": n.get("author") or "Benzinga",
                    "link": n.get("url") or n.get("link"),
                    "ts": n.get("created") or n.get("updated"),
                    "source": "benzinga",
                    "ticker": symbol.upper(),
                }
            )
        return out
    except Exception:
        return []


def _ts_seconds(value: Any) -> float | None:
    """Convert provider timestamps to epoch seconds without trusting bad input."""
    if value is None or value == "":
        return None
    try:
        number = float(value)
        if number > 1e12:
            number /= 1000.0
        return number if number > 0 else None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{14}", text):
        try:
            return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return None


def _google_news(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    """Public RSS search; useful as a broad discovery source, not a truth feed."""
    try:
        response = requests.get(
            "https://news.google.com/rss/search",
            params={"q": f'"{symbol.upper()}" stock', "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={"Accept": "application/rss+xml, application/xml", "User-Agent": "TomahawkDesk/1.0"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.content)
        rows = []
        for item in root.findall(".//item")[:limit]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            published = (item.findtext("pubDate") or "").strip()
            if title:
                rows.append(
                    {
                        "title": title,
                        "publisher": "Google News",
                        "link": link,
                        "ts": published,
                        "source": "google_news",
                        "ticker": symbol.upper(),
                    }
                )
        return rows
    except (ET.ParseError, requests.RequestException, ValueError, TypeError):
        return []


def _gdelt_news(symbol: str, limit: int = 5) -> list[dict[str, Any]]:
    """Public GDELT discovery search; results are attribution and recency signals only."""
    try:
        response = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": f'"{symbol.upper()}"',
                "mode": "artlist",
                "format": "json",
                "maxrecords": limit,
                "sort": "HybridRel",
            },
            headers={"Accept": "application/json", "User-Agent": "TomahawkDesk/1.0"},
            timeout=8,
        )
        if response.status_code != 200:
            return []
        data = response.json()
        rows = data.get("articles") if isinstance(data, dict) else []
        out = []
        for item in (rows or [])[:limit]:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            out.append(
                {
                    "title": item.get("title"),
                    "publisher": item.get("domain") or "GDELT",
                    "link": item.get("url"),
                    "ts": item.get("seendate") or item.get("datetime"),
                    "source": "gdelt",
                    "ticker": symbol.upper(),
                }
            )
        return out
    except (requests.RequestException, ValueError, TypeError):
        return []


def _headline_key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def enrich_headline(item: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
    """Add explainable research metadata; never produces a trade recommendation."""
    row = dict(item)
    title = str(row.get("title") or row.get("headline") or "").strip()
    ts = _ts_seconds(row.get("ts") or row.get("published_at"))
    now_value = float(now if now is not None else time.time())
    age = max(0.0, now_value - ts) if ts is not None else None
    source = str(row.get("source") or "news").lower()
    lowered = title.lower()
    tags = [name for name, pattern in _EVENT_PATTERNS.items() if re.search(pattern, lowered)]
    score = _SOURCE_WEIGHT.get(source, 0.5)
    if ts is not None:
        score += max(0.0, 1.0 - min(age, 172800.0) / 172800.0) * 1.5
    score += min(len(set(tags) & _HIGH_IMPACT_EVENTS), 2) * 0.8
    row.update(
        {
            "title": title,
            "source": source,
            "published_ts": ts,
            "age_seconds": round(age, 1) if age is not None else None,
            "event_tags": tags,
            "materiality": "high" if set(tags) & _HIGH_IMPACT_EVENTS else ("medium" if tags else "low"),
            "intelligence_score": round(score, 4),
            "headline_key": _headline_key(title),
        }
    )
    return row


def news_for_symbol(symbol: str, *, limit: int = 6) -> list[dict[str, Any]]:
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(rows: list[dict], src: str) -> None:
        for n in rows:
            title = (n.get("title") or n.get("headline") or "").strip()
            if not title:
                continue
            row = enrich_headline(dict(n, source=n.get("source") or src, ticker=sym))
            key = row["headline_key"]
            if key in seen:
                continue
            seen.add(key)
            items.append(row)

    try:
        import data_sources as ds

        _add(ds.finnhub_news(sym, days=5, limit=limit) or [], "finnhub")
        _add(ds.yahoo_news(sym, count=limit) or [], "yahoo")
    except Exception:
        pass
    _add(_benzinga_news(sym, limit=min(4, limit)), "benzinga")
    _add(_google_news(sym, limit=min(4, limit)), "google_news")
    _add(_gdelt_news(sym, limit=min(4, limit)), "gdelt")
    items.sort(key=lambda row: (float(row.get("intelligence_score") or 0), float(row.get("published_ts") or 0)), reverse=True)
    return items[:limit]


def watchlist_news(
    symbols: list[str] | None,
    *,
    per_symbol: int = 3,
    max_total: int = 24,
    force: bool = False,
) -> dict[str, Any]:
    """Merged news strip for Advanced panel / state.watchlist_news."""
    syms = []
    for s in symbols or []:
        t = str(s or "").strip().upper()
        if t and t not in syms:
            syms.append(t)
    syms = syms[:12]
    cache_key = ",".join(syms) + f"|{per_symbol}|{max_total}"
    with _lock:
        if (
            not force
            and _cache.get("payload")
            and _cache.get("key") == cache_key
            and (time.time() - float(_cache.get("at") or 0)) < _CACHE_TTL
        ):
            return dict(_cache["payload"])

    by_ticker: dict[str, list] = {}
    flat: list[dict[str, Any]] = []
    for sym in syms:
        rows = news_for_symbol(sym, limit=per_symbol)
        by_ticker[sym] = rows
        flat.extend(rows)
        if len(flat) >= max_total:
            break
    flat.sort(
        key=lambda row: (
            float(row.get("intelligence_score") or 0),
            float(row.get("published_ts") or 0),
        ),
        reverse=True,
    )
    flat = flat[:max_total]
    source_counts: dict[str, int] = {}
    materiality_counts: dict[str, int] = {}
    for row in flat:
        source = str(row.get("source") or "unknown")
        materiality = str(row.get("materiality") or "low")
        source_counts[source] = source_counts.get(source, 0) + 1
        materiality_counts[materiality] = materiality_counts.get(materiality, 0) + 1
    payload = {
        "ok": True,
        "items": flat,
        "by_ticker": by_ticker,
        "count": len(flat),
        "source_counts": source_counts,
        "materiality_counts": materiality_counts,
        "intelligence": {
            "ranked": True,
            "deduplicated": True,
            "display_only": True,
            "latest_published_ts": max(
                (float(row["published_ts"]) for row in flat if row.get("published_ts") is not None),
                default=None,
            ),
        },
        "symbols": syms,
        "providers": public_status(),
        "as_of": time.time(),
    }
    with _lock:
        _cache["at"] = time.time()
        _cache["key"] = cache_key
        _cache["payload"] = payload
    return dict(payload)
