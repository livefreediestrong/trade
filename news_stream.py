"""
Watchlist news stream — deepen Finnhub company-news + Yahoo; optional Benzinga stub.

Exposes watchlist_news for Advanced panel / state. Graceful degrade without keys.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

import requests

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "key": "", "payload": None}
_CACHE_TTL = 120.0


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
        "configured": True,  # Yahoo always available best-effort
        "docs": {
            "finnhub": "https://finnhub.io/docs/api/company-news",
            "benzinga": "https://docs.benzinga.io/benzinga-apis/news-v2/news",
            "yahoo": "Yahoo finance search (no key)",
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


def news_for_symbol(symbol: str, *, limit: int = 6) -> list[dict[str, Any]]:
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(rows: list[dict], src: str) -> None:
        for n in rows:
            title = (n.get("title") or "").strip()
            if not title:
                continue
            key = title.lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            row = dict(n)
            row["source"] = row.get("source") or src
            row["ticker"] = sym
            items.append(row)

    try:
        import data_sources as ds

        _add(ds.finnhub_news(sym, days=5, limit=limit) or [], "finnhub")
        _add(ds.yahoo_news(sym, count=limit) or [], "yahoo")
    except Exception:
        pass
    _add(_benzinga_news(sym, limit=min(4, limit)), "benzinga")
    # Prefer fresher / finnhub first already; trim
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
    flat = flat[:max_total]
    payload = {
        "ok": True,
        "items": flat,
        "by_ticker": by_ticker,
        "count": len(flat),
        "symbols": syms,
        "providers": public_status(),
        "as_of": time.time(),
    }
    with _lock:
        _cache["at"] = time.time()
        _cache["key"] = cache_key
        _cache["payload"] = payload
    return dict(payload)
