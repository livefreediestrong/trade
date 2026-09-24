"""
Polygon.io market data adapter (edu / paper desk).

Env: POLYGON_API_KEY
Docs: https://polygon.io/docs

Tiers (honest):
  - Free / Basic: delayed US equities aggregates + limited snapshot; rate limits apply.
  - Stocks Starter / Developer / Advanced: real-time / richer snapshots (paid).
This client never invents quotes. Missing key → not_configured; HTTP errors → [].
Never used for live broker submits — radar / research only.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests

BASE = "https://api.polygon.io"
DEFAULT_TIMEOUT = 12


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
    return (os.environ.get("POLYGON_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(api_key())


def public_status() -> dict[str, Any]:
    return {
        "provider": "polygon",
        "configured": is_configured(),
        "tier_note": (
            "Free/Basic is typically delayed US equity data with rate limits; "
            "paid plans unlock real-time snapshots. Desk degrades without a key."
        ),
        "docs": "https://polygon.io/docs",
        "signup": "https://polygon.io/dashboard/signup",
    }


def _get(path: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> Any | None:
    key = api_key()
    if not key:
        return None
    p = dict(params or {})
    p["apiKey"] = key
    url = f"{BASE}/{path.lstrip('/')}"
    try:
        r = requests.get(url, params=p, timeout=timeout, headers={"Accept": "application/json"})
        if r.status_code == 429:
            time.sleep(1.0)
            r = requests.get(url, params=p, timeout=timeout, headers={"Accept": "application/json"})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
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
    """Batch via full-market snapshot filter when possible; else per-ticker (capped)."""
    if not is_configured():
        return []
    wanted = {(s or "").strip().upper() for s in (symbols or []) if s}
    if not wanted:
        return []
    # Prefer full-market snapshot once, then filter (fewer calls on free tier)
    data = _get("v2/snapshot/locale/us/markets/stocks/tickers")
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

    # Fallback: individual snapshots (rate-limit friendly cap)
    for sym in list(wanted)[: min(limit, 25)]:
        snap = snapshot(sym)
        if snap and snap.get("price"):
            out.append(snap)
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
    snaps = snapshots(syms, limit=100) if syms else snapshots([], limit=0)
    # If no universe filter worked via full market, try universe individually
    if not snaps and syms:
        snaps = []
        for s in syms[:40]:
            one = snapshot(s)
            if one:
                snaps.append(one)
    return snaps
