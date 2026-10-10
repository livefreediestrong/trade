"""
Massive (formerly Polygon) market data adapter for research / paper.

Env: MASSIVE_API_KEY, or existing POLYGON_API_KEY
Docs: https://massive.com/docs

Tiers (honest):
  - Basic: end-of-day aggregates; no snapshots; five requests/minute.
  - Starter / Developer: 15-minute delayed. Advanced: real-time entitlement.
This client never invents quotes. Missing key → not_configured; HTTP errors → [].
Never used for live broker submits — radar / research only.
"""
from __future__ import annotations

import os
import time
import copy
import hashlib
import math
import threading
from collections import OrderedDict, deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

import requests

BASE = "https://api.massive.com"
DEFAULT_TIMEOUT = 12
_lock = threading.RLock()
_request_lock = threading.Lock()
_requests_at = deque()
_responses = OrderedDict()
_blocked = {}
_status = {"state": "not_checked", "error": None, "checked_at": None}
_endpoint_status = {}
_key_fingerprint = None
_probe_after = 0.0
_probe_worker = None


def maybe_check_access():
    """Bounded research-only capability read; never on an account/order thread."""
    global _probe_after, _probe_worker
    if os.environ.get("TOMAHAWK_NO_BG") == "1" or not is_configured():
        return False
    with _lock:
        if time.monotonic() < _probe_after or (_probe_worker and _probe_worker.is_alive()):
            return False
        _probe_after = time.monotonic() + 900
        def run():
            from datetime import timedelta
            try:
                today = datetime.now(timezone.utc).date()
                aggregates("SPY", from_date=(today-timedelta(days=7)).isoformat(), to_date=today.isoformat(), limit=5)
                snapshot("SPY")
            except Exception:
                with _lock:
                    _note("unavailable", "Access check failed; the next scheduled research check will retry.")
        _probe_worker = threading.Thread(target=run, name="massive-access-check", daemon=True)
        _probe_worker.start()
        return True


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


def api_key() -> str:
    return (os.environ.get("MASSIVE_API_KEY") or "").strip() or (os.environ.get("POLYGON_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(api_key())


def public_status() -> dict[str, Any]:
    with _lock:
        observed = dict(_status)
        endpoints = copy.deepcopy(_endpoint_status)
    plan = _plan()
    return {
        "provider": "polygon",
        "name": "Massive (formerly Polygon)",
        "configured": is_configured(),
        "declared_plan": plan, "plan_verified": False,
        "declared_delay": {"basic": "end_of_day", "starter": "15_minutes", "developer": "15_minutes",
                           "advanced": "real_time"}.get(plan, "unknown"),
        "max_requests_per_minute": _request_limit(),
        **observed,
        "endpoints": endpoints,
        "tier_note": (
            "Basic is end-of-day and excludes snapshots. Starter/Developer are 15-minute delayed. "
            "Advanced requires real-time access. A configured key or declared plan does not verify entitlement."
        ),
        "docs": "https://massive.com/docs", "signup": "https://massive.com/dashboard/signup",
    }


def _plan():
    plan = os.environ.get("MASSIVE_STOCKS_PLAN", "unknown").strip().lower()
    return plan if plan in {"basic", "starter", "developer", "advanced"} else "unknown"


def _request_limit():
    try:
        value = int(os.environ.get("MASSIVE_MAX_REQUESTS_PER_MINUTE", "5"))
    except (TypeError, ValueError):
        value = 5
    return max(1, min(5 if _plan() == "basic" else 120, value))


def _note(state, error=None, *, family=None):
    _status.update(state=state, error=error, checked_at=datetime.now(timezone.utc).isoformat())
    if family:
        _endpoint_status[family] = dict(_status)


def _retry_delay(response):
    value = response.headers.get("Retry-After", "")
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            delay = 60
    return min(86400, max(60, delay)) if math.isfinite(delay) else 60


def _get(path: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any | None:
    global _key_fingerprint
    key = api_key()
    if not key:
        return None
    # Single in-flight read; health/status never waits for the network lock.
    if not _request_lock.acquire(blocking=False):
        return None
    try:
        family = "snapshot" if path.startswith("v2/snapshot/") else "aggregates"
        cache_key = (path, tuple(sorted((params or {}).items())))
        now = time.monotonic()
        with _lock:
            fingerprint = hashlib.sha256(key.encode()).digest()
            if fingerprint != _key_fingerprint:
                _blocked.clear(); _responses.clear(); _requests_at.clear()
                _endpoint_status.clear()
                _key_fingerprint = fingerprint
                _note("not_checked")
            if family == "snapshot" and _plan() == "basic":
                _note("plan_excluded", "The declared Basic plan does not include snapshots. Historical end-of-day bars remain available.", family=family)
                return None
            if now < _blocked.get("all", 0) or now < _blocked.get(family, 0):
                return None
            cached = _responses.get(cache_key)
            if cached and 0 <= now - cached[0] < 60:
                _responses.move_to_end(cache_key)
                _note("cached", family=family)
                return copy.deepcopy(cached[1])
            while _requests_at and now - _requests_at[0] >= 60:
                _requests_at.popleft()
            if len(_requests_at) >= _request_limit():
                _note("rate_limited", "Local request allowance reached; waiting for the next minute.", family=family)
                return None
            _requests_at.append(now)
        try:
            r = requests.get(f"{BASE}/{path.lstrip('/')}", params=dict(params or {}), timeout=timeout,
                             allow_redirects=False, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"})
            with _lock:
                if r.status_code != 200:
                    code = r.status_code
                    scope, delay = (family, 300)
                    state, error = "unavailable", f"Market data unavailable (HTTP {code}); retrying later."
                    if code == 429:
                        scope, delay, state = "all", _retry_delay(r), "rate_limited"
                        error = "Provider request limit reached; honoring its retry delay."
                    elif code == 401:
                        scope, delay, state = "all", 3600, "authentication_required"
                        error = "Massive could not authenticate the configured key. Check account access."
                    elif code in (402, 403):
                        delay, state = 3600, "access_denied"
                        error = "This data endpoint is not available to the account. Check the plan and access permissions."
                    _blocked[scope] = time.monotonic() + delay
                    _note(state, error, family=family)
                    return None
            data = r.json()
            if not isinstance(data, dict) or str(data.get("status", "")).upper() in {"ERROR", "NOT_AUTHORIZED"}:
                raise ValueError("Invalid provider response")
            with _lock:
                _responses[cache_key] = (time.monotonic(), copy.deepcopy(data))
                while len(_responses) > 64:
                    _responses.popitem(last=False)
                _note("available", family=family)
            return data
        except (requests.RequestException, OSError, ValueError, TypeError):
            with _lock:
                _blocked[family] = time.monotonic() + 60
                _note("unavailable", "Could not read market data; retrying later.", family=family)
            return None
    finally:
        _request_lock.release()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        value = float(v)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def snapshot(symbol: str) -> Optional[dict[str, Any]]:
    """Single-ticker snapshot. Returns normalized dict or None."""
    sym = (symbol or "").strip().upper()
    if not sym or not is_configured():
        return None
    data = _get(f"v2/snapshot/locale/us/markets/stocks/tickers/{sym}")
    if not isinstance(data, dict):
        return None
    ticker = data.get("ticker") if isinstance(data.get("ticker"), dict) else data
    if not isinstance(ticker, dict):
        return None
    day = ticker.get("day") or {}
    prev = ticker.get("prevDay") or {}
    last_trade = ticker.get("lastTrade") or {}
    last_quote = ticker.get("lastQuote") or {}
    price = _safe_float(
        last_trade.get("p")
        or last_quote.get("P")
        or last_quote.get("p")
        or day.get("c")
        or ticker.get("min", {}).get("c")
    )
    prev_close = _safe_float(prev.get("c") or day.get("o"))
    pct = _safe_float(ticker.get("todaysChangePerc"))
    if pct == 0.0 and prev_close > 0 and price > 0:
        pct = ((price - prev_close) / prev_close) * 100.0
    vol = _safe_float(day.get("v"))
    return {
        "ticker": sym,
        "price": price,
        "prev_close": prev_close,
        "pct": round(pct, 4),
        "volume": vol,
        "dollar_volume": round(price * vol, 2) if price and vol else 0.0,
        "source": "polygon_snapshot",
        "updated": ticker.get("updated"),
        "market_time": (last_trade.get("t") if last_trade.get("p") else last_quote.get("t")
                        if last_quote.get("P") or last_quote.get("p") else day.get("t")
                        if day.get("c") else (ticker.get("min") or {}).get("t")),
        "market_time_unit": "ns" if last_trade.get("p") or last_quote.get("P") or last_quote.get("p") else "ms",
    }


def snapshots(symbols: list[str], *, limit: int = 80) -> list[dict[str, Any]]:
    """One batch read; failure never fans out into dozens of individual requests."""
    if not is_configured():
        return []
    wanted = {(s or "").strip().upper() for s in (symbols or []) if s}
    if not wanted:
        return []
    # Prefer full-market snapshot once, then filter (fewer calls on free tier)
    if limit <= 0:
        return []
    data = _get("v2/snapshot/locale/us/markets/stocks/tickers", {"tickers": ",".join(sorted(wanted)[:100])})
    out: list[dict[str, Any]] = []
    tickers = []
    if isinstance(data, dict):
        tickers = data.get("tickers") or []
    if isinstance(tickers, list) and tickers:
        for row in tickers:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("ticker") or "").upper()
            if sym not in wanted:
                continue
            day = row.get("day") or {}
            prev = row.get("prevDay") or {}
            last_trade = row.get("lastTrade") or {}
            price = _safe_float(last_trade.get("p") or day.get("c"))
            prev_close = _safe_float(prev.get("c") or day.get("o"))
            pct = _safe_float(row.get("todaysChangePerc"))
            if pct == 0.0 and prev_close > 0 and price > 0:
                pct = ((price - prev_close) / prev_close) * 100.0
            vol = _safe_float(day.get("v"))
            out.append(
                {
                    "ticker": sym,
                    "price": price,
                    "prev_close": prev_close,
                    "pct": round(pct, 4),
                    "volume": vol,
                    "dollar_volume": round(price * vol, 2) if price and vol else 0.0,
                    "source": "polygon_snapshot",
                    "market_time": last_trade.get("t") if last_trade.get("p") else day.get("t"),
                    "market_time_unit": "ns" if last_trade.get("p") else "ms",
                }
            )
            if len(out) >= limit:
                break
        return out

    return out


def aggregates(
    symbol: str,
    *,
    multiplier: int = 1,
    timespan: str = "day",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 120,
) -> list[dict[str, Any]]:
    """OHLCV aggregates. Dates YYYY-MM-DD. Empty when unconfigured or error."""
    sym = (symbol or "").strip().upper()
    if not sym or not is_configured():
        return []
    if not to_date:
        to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not from_date:
        from datetime import timedelta

        from_date = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%d")
    path = f"v2/aggs/ticker/{sym}/range/{int(multiplier)}/{timespan}/{from_date}/{to_date}"
    data = _get(path, {"adjusted": "true", "sort": "asc", "limit": int(limit)})
    if not isinstance(data, dict):
        return []
    results = data.get("results") or []
    out = []
    for bar in results:
        if not isinstance(bar, dict):
            continue
        out.append(
            {
                "ts": bar.get("t"),
                "open": _safe_float(bar.get("o")),
                "high": _safe_float(bar.get("h")),
                "low": _safe_float(bar.get("l")),
                "close": _safe_float(bar.get("c")),
                "volume": _safe_float(bar.get("v")),
                "vwap": _safe_float(bar.get("vw")),
                "source": "polygon_aggs",
            }
        )
    return out


def fetch_radar_movers(universe: list[str] | None = None) -> list[dict[str, Any]]:
    """Rows shaped for market_radar._row_from_quote consumers (price/pct/volume)."""
    if not is_configured():
        return []
    syms = list(universe or [])
    return snapshots(syms, limit=100) if syms else []
