"""
Multi-source data layer.

Order of preference for daily OHLCV:
  1. Yahoo (via yfinance, already used elsewhere)
  2. Stooq (free, no key)

Extras (no key needed):
  - Yahoo News search endpoint
  - Yahoo direct quote endpoint (faster batched quotes than yfinance.info)

Designed to be drop-in friendly — every function returns either a sane object
or None / empty list on failure. Never raises.
"""
from __future__ import annotations

import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

UA = (__import__("os").environ.get("DATA_SOURCES_UA") or "Mozilla/5.0 (compatible; TomahawkDesk/1.0)").strip()
HEADERS = {"User-Agent": UA, "Accept": "application/json,text/csv,*/*"}


def yahoo_symbol(symbol: str) -> str:
    """Map class shares BRK.B / BRK/B → BRK-B for Yahoo chart/quote APIs."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return sym
    import re as _re
    if _re.match(r"^[A-Z]{1,5}[./][AB]$", sym):
        return sym.replace(".", "-").replace("/", "-")
    return sym



# ---------- Tiny .env loader (no extra deps) ----------

def _env_search_paths():
    """Locations to look for .env, in priority order. Frozen-aware (PyInstaller)
    so the user can edit a .env right next to the exe without rebuilding."""
    paths = []
    import sys as _sys
    # 1. Next to the exe (what the user can edit) — only when frozen.
    if getattr(_sys, "frozen", False):
        paths.append(Path(_sys.executable).parent / ".env")
    # 2. Current working directory.
    paths.append(Path.cwd() / ".env")
    # 3. Next to this source file (development mode).
    paths.append(Path(__file__).parent / ".env")
    return paths


def _load_env():
    from dotenv import dotenv_values
    for env_path in _env_search_paths():
        if env_path.exists():
            for key, value in dotenv_values(env_path, encoding="utf-8-sig", interpolate=False).items():
                if value is not None:
                    os.environ.setdefault(key, value)
            return  # first file wins


_load_env()
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")


def _finnhub_get(path: str, params: dict, timeout: int = 8, max_retries: int = 1):
    """GET to api.finnhub.io with retry-on-429 and graceful failure.
    Returns parsed JSON or None. Free tier is 60/min — we back off and retry
    once on 429 so a brief burst doesn't silently drop data."""
    if not FINNHUB_KEY:
        return None
    import time as _time
    url = f"https://finnhub.io/api/v1/{path.lstrip('/')}"
    p = dict(params)
    p["token"] = FINNHUB_KEY
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(url, params=p, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return None
            if r.status_code == 429 and attempt < max_retries:
                _time.sleep(1.2)  # short backoff; free tier resets every minute
                continue
            return None
        except Exception:
            if attempt < max_retries:
                _time.sleep(0.5)
                continue
            return None
    return None


# ---------- Yahoo direct chart API (fallback #1, bypasses yfinance) ----------

def yahoo_chart_daily(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Daily bars from Yahoo's v8 chart endpoint, hit directly with requests.
    Often works when the yfinance wrapper has issues (caching, library bugs).
    Returns Open/High/Low/Close/Volume indexed by date, or None."""
    rng = "3mo" if days <= 90 else ("6mo" if days <= 180 else "1y")
    symbol = yahoo_symbol(symbol)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = requests.get(
            url, params={"range": rng, "interval": "1d"},
            headers=HEADERS, timeout=12,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("chart", {}).get("result")
        if not data:
            return None
        res = data[0]
        ts = res.get("timestamp") or []
        ind = (res.get("indicators", {}).get("quote") or [{}])[0]
        if not ts or not ind.get("close"):
            return None
        df = pd.DataFrame(
            {
                "Open": ind.get("open"),
                "High": ind.get("high"),
                "Low": ind.get("low"),
                "Close": ind.get("close"),
                "Volume": ind.get("volume"),
            },
            index=pd.to_datetime(ts, unit="s"),
        )
        df.index.name = "Date"
        df = df.dropna(subset=["Close"])
        return df if not df.empty else None
    except Exception:
        return None


def yahoo_chart_intraday(
    symbol: str,
    interval: str = "1m",
    range_: str = "1d",
    timeout: int = 8,
) -> Optional[list]:
    """Intraday closes for sparklines. Fail soft → None. No lock."""
    sym = yahoo_symbol(symbol)
    if not sym:
        return None
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    try:
        r = requests.get(
            url,
            params={"range": range_, "interval": interval},
            headers=HEADERS,
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("chart", {}).get("result")
        if not data:
            return None
        ind = (data[0].get("indicators", {}).get("quote") or [{}])[0]
        closes = ind.get("close") or []
        out = [float(c) for c in closes if c is not None]
        return out if len(out) >= 2 else None
    except Exception:
        return None


# ---------- NASDAQ historical API (fallback #2, free, no key) ----------

def nasdaq_historical(symbol: str, days: int = 120) -> Optional[pd.DataFrame]:
    """Daily bars from api.nasdaq.com. Independent host from Yahoo —
    a real second source. Equity assetclass only."""
    end = datetime.utcnow()
    start = end - timedelta(days=int(days * 1.6))
    url = f"https://api.nasdaq.com/api/quote/{symbol.upper()}/historical"
    params = {
        "assetclass": "stocks",
        "fromdate": start.strftime("%Y-%m-%d"),
        "todate": end.strftime("%Y-%m-%d"),
        "limit": days * 2,
    }
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        if r.status_code != 200:
            return None
        rows = (((r.json() or {}).get("data") or {}).get("tradesTable") or {}).get("rows")
        if not rows:
            return None

        def num(s):
            if s is None:
                return None
            s = str(s).replace("$", "").replace(",", "").strip()
            if s in ("", "N/A", "--"):
                return None
            try:
                return float(s)
            except ValueError:
                return None

        df = pd.DataFrame(
            [
                {
                    "Date": pd.to_datetime(row.get("date")),
                    "Open": num(row.get("open")),
                    "High": num(row.get("high")),
                    "Low": num(row.get("low")),
                    "Close": num(row.get("close")),
                    "Volume": num(row.get("volume")),
                }
                for row in rows
            ]
        )
        df = df.dropna(subset=["Close"]).set_index("Date").sort_index()
        return df if not df.empty else None
    except Exception:
        return None


def get_daily_with_fallback(symbol: str, days: int = 120):
    """Try Yahoo chart API, then NASDAQ. Returns (DataFrame, source_label) or (None, None)."""
    df = yahoo_chart_daily(symbol, days)
    if df is not None and not df.empty:
        return df, "Yahoo Chart API"
    df = nasdaq_historical(symbol, days)
    if df is not None and not df.empty:
        return df, "NASDAQ"
    return None, None


# ---------- Yahoo direct quote (fast batched quotes) ----------

def yahoo_quote_batch(symbols: list[str], timeout: int = 10) -> dict:
    """Quote lookup for symbols. Yahoo v7/finance/quote is often dead (401/404);

    Fall back to per-symbol v8 chart meta (regularMarketPrice). Returns {symbol: dict}
    keyed by the *requested* symbol (BRK.B stays BRK.B even when Yahoo uses BRK-B).
    """
    if not symbols:
        return {}
    out: dict = {}
    # Try legacy v7 once (may work from some networks)
    url_v7 = "https://query1.finance.yahoo.com/v7/finance/quote"
    mapped = [(s, yahoo_symbol(s)) for s in symbols if s]
    try:
        r = requests.get(
            url_v7,
            params={"symbols": ",".join(ys for _, ys in mapped[:50])},
            headers=HEADERS,
            timeout=timeout,
        )
        if r.status_code == 200:
            by_ys = {}
            for q in r.json().get("quoteResponse", {}).get("result", []) or []:
                sym = q.get("symbol")
                if sym:
                    by_ys[sym] = q
            for orig, ys in mapped:
                if ys in by_ys:
                    out[orig] = by_ys[ys]
                    out[ys] = by_ys[ys]
    except Exception:
        pass
    # Fill gaps via v8 chart meta
    for orig, ys in mapped:
        if orig in out:
            continue
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}"
            r = requests.get(
                url,
                params={"range": "1d", "interval": "1m"},
                headers=HEADERS,
                timeout=min(timeout, 8),
            )
            if r.status_code != 200:
                continue
            result = (r.json().get("chart") or {}).get("result") or []
            if not result:
                continue
            meta = result[0].get("meta") or {}
            # Never label yesterday's close as the live price.
            price = meta.get("regularMarketPrice")
            if price:
                q = {
                    "symbol": ys,
                    "regularMarketPrice": float(price),
                    "regularMarketTime": meta.get("regularMarketTime"),
                    "regularMarketPreviousClose": meta.get("previousClose"),
                    "marketState": meta.get("marketState"),
                }
                out[orig] = q
                out[ys] = q
        except Exception:
            continue
    return out


# ---------- Yahoo earnings (calendarEvents module) ----------

# ---------- Finnhub (paid free-tier; needs FINNHUB_API_KEY) ----------

def finnhub_earnings(symbol: str) -> Optional[dict]:
    """Next earnings date from Finnhub. Returns
    {date, days_away, is_soon, hour, eps_estimate, revenue_estimate} or None."""
    today = datetime.now(timezone.utc).date()
    data = _finnhub_get("calendar/earnings", {
        "symbol": symbol.upper(),
        "from": today.strftime("%Y-%m-%d"),
        "to": (today + timedelta(days=180)).strftime("%Y-%m-%d"),
    })
    if not data:
        return None
    cal = (data or {}).get("earningsCalendar") or []
    upcoming = [
        e for e in cal
        if e.get("date") and pd.Timestamp(e["date"]).date() >= today
    ]
    if not upcoming:
        return None
    next_e = min(upcoming, key=lambda e: e["date"])
    next_d = pd.Timestamp(next_e["date"]).date()
    days_away = (next_d - today).days
    return {
        "date": next_e["date"],
        "days_away": int(days_away),
        "is_soon": 0 <= days_away <= 7,
        "hour": next_e.get("hour"),
        "eps_estimate": next_e.get("epsEstimate"),
        "revenue_estimate": next_e.get("revenueEstimate"),
    }


def finnhub_recommendation(symbol: str) -> Optional[dict]:
    """Latest analyst recommendation distribution from Finnhub."""
    arr = _finnhub_get("stock/recommendation", {"symbol": symbol.upper()})
    if not arr:
        return None
    latest = arr[0] if isinstance(arr, list) else None
    if not latest:
        return None
    return {
        "strong_buy": int(latest.get("strongBuy") or 0),
        "buy": int(latest.get("buy") or 0),
        "hold": int(latest.get("hold") or 0),
        "sell": int(latest.get("sell") or 0),
        "strong_sell": int(latest.get("strongSell") or 0),
        "period": latest.get("period"),
    }


def finnhub_quote(symbol: str) -> Optional[dict]:
    """Real-time(ish) quote from Finnhub."""
    q = _finnhub_get("quote", {"symbol": symbol.upper()})
    if not q or not q.get("c"):
        return None
    return {
        "current": float(q.get("c")),
        "change_dollar": float(q.get("d") or 0),
        "change_pct": float(q.get("dp") or 0),
        "high": float(q.get("h") or 0),
        "low": float(q.get("l") or 0),
        "open": float(q.get("o") or 0),
        "prev_close": float(q.get("pc") or 0),
        "timestamp": int(q.get("t") or 0),
    }


def quote_snapshot(price, timestamp, source: str, *, now=None) -> dict:
    """Keep market time separate from receipt time. Unknown age is never fresh."""
    import math
    from datetime import timezone
    now = now or datetime.now(timezone.utc)
    stamp = None
    try:
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            stamp = datetime.fromtimestamp(timestamp, timezone.utc)
        elif timestamp is not None:
            stamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = None
    except (TypeError, ValueError, OverflowError, OSError):
        pass
    try:
        value = float(price)
        valid = math.isfinite(value) and value > 0
    except (TypeError, ValueError):
        value, valid = None, False
    age = (now - stamp).total_seconds() if stamp else None
    # One-minute bars can be almost two minutes old near the next bar boundary.
    fresh = bool(valid and age is not None and -15 <= age <= 120)
    return {"price": value if valid else None, "source": source,
            "market_time": stamp.isoformat() if stamp else None,
            "received_at": now.isoformat(), "age_sec": round(age, 1) if age is not None else None,
            "fresh": fresh, "max_age_sec": 120,
            "error": None if fresh else "invalid_price" if not valid else "missing_market_time" if age is None else "stale_or_future_quote"}


def latest_quote(symbol: str) -> dict:
    """Return a timestamped quote; only a recent provider observation is usable."""
    quote = finnhub_quote(symbol)
    last = quote_snapshot(None, None, "unavailable")
    if quote:
        last = quote_snapshot(quote.get("current"), quote.get("timestamp"), "Finnhub")
        if last["fresh"]:
            return last
    try:
        import yfinance as yf
        bars = yf.Ticker(yahoo_symbol(symbol)).history(period="1d", interval="1m", timeout=8)
        if bars is not None and not bars.empty:
            candidate = quote_snapshot(bars["Close"].iloc[-1], bars.index[-1], "Yahoo 1m")
            if candidate["fresh"]:
                return candidate
            if last.get("price") is None:
                last = candidate
    except Exception:
        pass
    try:
        batch = yahoo_quote_batch([symbol])
        row = batch.get(symbol) or batch.get(yahoo_symbol(symbol)) or {}
        candidate = quote_snapshot(row.get("regularMarketPrice"), row.get("regularMarketTime"), "Yahoo quote")
        if candidate["fresh"]:
            return candidate
        if last.get("price") is None and candidate.get("price"):
            last = candidate
    except Exception:
        pass
    return last


def finnhub_news(symbol: str, days: int = 7, limit: int = 8) -> list[dict]:
    """Company news from Finnhub for the last N days."""
    today = datetime.utcnow().date()
    items = _finnhub_get("company-news", {
        "symbol": symbol.upper(),
        "from": (today - timedelta(days=days)).strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
    })
    if not items or not isinstance(items, list):
        return []
    items = sorted(items, key=lambda n: n.get("datetime", 0), reverse=True)[:limit]
    return [
        {
            "title": n.get("headline"),
            "summary": n.get("summary"),
            "publisher": n.get("source"),
            "link": n.get("url"),
            "ts": n.get("datetime"),
            "image": n.get("image") or None,
            "category": n.get("category"),
        }
        for n in items
        if n.get("headline")
    ]


def finnhub_news_sentiment(symbol: str) -> Optional[dict]:
    """Aggregate news sentiment + buzz from Finnhub."""
    d = _finnhub_get("news-sentiment", {"symbol": symbol.upper()})
    if not d:
        return None
    s = d.get("sentiment") or {}
    b = d.get("buzz") or {}
    if not s and not b and d.get("companyNewsScore") is None:
        return None
    return {
        "bullish_pct": float(s.get("bullishPercent") or 0) * 100,
        "bearish_pct": float(s.get("bearishPercent") or 0) * 100,
        "score": float(d.get("companyNewsScore") or 0),
        "articles_in_last_week": int(b.get("articlesInLastWeek") or 0),
        "weekly_avg": float(b.get("weeklyAverage") or 0),
        "buzz": float(b.get("buzz") or 0),
        "sector_avg_score": float(d.get("sectorAverageNewsScore") or 0),
        "sector_avg_bullish_pct": float(d.get("sectorAverageBullishPercent") or 0) * 100,
    }


def finnhub_social_sentiment(symbol: str, days: int = 7) -> Optional[dict]:
    """Reddit + Twitter mention counts and bull/bear scores."""
    today = datetime.utcnow().date()
    d = _finnhub_get("stock/social-sentiment", {
        "symbol": symbol.upper(),
        "from": (today - timedelta(days=days)).strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
    })
    if not d:
        return None

    def agg(rows):
        if not rows:
            return None
        mentions = sum(int(x.get("mention") or 0) for x in rows)
        pos = sum(int(x.get("positiveMention") or 0) for x in rows)
        neg = sum(int(x.get("negativeMention") or 0) for x in rows)
        pos_score = sum(float(x.get("positiveScore") or 0) for x in rows) / len(rows)
        neg_score = sum(float(x.get("negativeScore") or 0) for x in rows) / len(rows)
        return {
            "mentions": mentions,
            "positive": pos,
            "negative": neg,
            "positive_score": round(pos_score, 3),
            "negative_score": round(neg_score, 3),
        }

    out = {
        "reddit": agg(d.get("reddit") or []),
        "twitter": agg(d.get("twitter") or []),
    }
    if not out["reddit"] and not out["twitter"]:
        return None
    return out


def finnhub_insider_transactions(symbol: str, days: int = 90, limit: int = 10) -> list[dict]:
    """Recent insider transactions (officers/directors)."""
    today = datetime.utcnow().date()
    d = _finnhub_get("stock/insider-transactions", {
        "symbol": symbol.upper(),
        "from": (today - timedelta(days=days)).strftime("%Y-%m-%d"),
        "to": today.strftime("%Y-%m-%d"),
    })
    if not d:
        return []
    rows = (d or {}).get("data") or []
    rows = sorted(rows, key=lambda x: x.get("transactionDate", ""), reverse=True)[:limit]
    return [
        {
            "name": x.get("name"),
            "share": int(x.get("share") or 0),
            "change": int(x.get("change") or 0),
            "transactionDate": x.get("transactionDate"),
            "transactionPrice": float(x.get("transactionPrice") or 0),
            "transactionCode": x.get("transactionCode"),
        }
        for x in rows
    ]


def finnhub_transcripts_list(symbol: str, limit: int = 4) -> list[dict]:
    """List of available earnings call transcripts."""
    d = _finnhub_get("stock/transcripts/list", {"symbol": symbol.upper()})
    if not d:
        return []
    items = (d or {}).get("transcripts") or []
    items = sorted(items, key=lambda x: x.get("time", ""), reverse=True)[:limit]
    return [
        {
            "id": x.get("id"),
            "title": x.get("title"),
            "time": x.get("time"),
            "year": x.get("year"),
            "quarter": x.get("quarter"),
        }
        for x in items
    ]


def yahoo_next_earnings(symbol: str, timeout: int = 8) -> Optional[dict]:
    """Next-earnings date via Yahoo's chart endpoint with events=earnings.
    The chart endpoint doesn't require a crumb token, unlike quoteSummary.
    Returns {date, days_away, is_soon} or None."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = requests.get(
            url,
            params={"range": "2y", "interval": "1d", "events": "earnings"},
            headers=HEADERS,
            timeout=timeout,
        )
        if r.status_code != 200:
            return None
        result = (((r.json() or {}).get("chart") or {}).get("result") or [])
        if not result:
            return None
        events = (result[0] or {}).get("events") or {}
        earnings = events.get("earnings") or {}
        if not earnings:
            return None
        now = datetime.utcnow().timestamp()
        future_ts = [
            e.get("date") for e in earnings.values()
            if isinstance(e, dict) and e.get("date") and e["date"] >= now - 86400
        ]
        if not future_ts:
            return None
        next_raw = min(future_ts)
        next_dt = datetime.utcfromtimestamp(next_raw)
        days_away = (next_dt.date() - datetime.utcnow().date()).days
        return {
            "date": next_dt.strftime("%Y-%m-%d"),
            "days_away": int(days_away),
            "is_soon": 0 <= days_away <= 7,
        }
    except Exception:
        return None


# ---------- Yahoo news (free, no key) ----------

def yahoo_news(symbol: str, count: int = 5, timeout: int = 8) -> list[dict]:
    """Recent news headlines for a ticker via Yahoo's search endpoint."""
    url = "https://query1.finance.yahoo.com/v1/finance/search"
    try:
        r = requests.get(
            url,
            params={
                "q": symbol,
                "newsCount": count,
                "quotesCount": 0,
                "enableFuzzyQuery": False,
            },
            headers=HEADERS,
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = r.json().get("news") or []
        out = []
        for n in items[:count]:
            out.append(
                {
                    "title": n.get("title"),
                    "publisher": n.get("publisher"),
                    "link": n.get("link") or n.get("clickThroughUrl", {}).get("url"),
                    "ts": n.get("providerPublishTime"),
                }
            )
        return [x for x in out if x.get("title")]
    except Exception:
        return []


# ---------- Curated theme map for popular sector/thematic ETFs ----------

ETF_THEMES = {
    # Sector SPDRs
    "XLK": "Technology",          "XLF": "Financials",
    "XLV": "Healthcare",          "XLY": "Cons. Discretionary",
    "XLP": "Cons. Staples",       "XLI": "Industrials",
    "XLE": "Energy",              "XLB": "Materials",
    "XLU": "Utilities",           "XLRE": "Real Estate",
    "XLC": "Communications",
    # Thematic / industry
    "SOXX": "Semiconductors",     "SMH": "Semiconductors",
    "GDX": "Gold Miners",         "GDXJ": "Junior Gold Miners",
    "SIL": "Silver Miners",       "SILJ": "Junior Silver Miners",
    "URA": "Uranium",             "URNM": "Uranium",
    "LIT": "Lithium & Battery",   "BATT": "Battery Tech",
    "COPX": "Copper Miners",      "REMX": "Rare Earth & Strategic Metals",
    "TAN": "Solar Energy",        "ICLN": "Clean Energy",
    "XAR": "Aerospace & Defense", "ITA": "Aerospace & Defense",
    "ARKX": "Space Exploration",  "UFO": "Space Exploration",
    "ARKK": "Disruptive Innov.",  "ARKQ": "Robotics & Automation",
    "BOTZ": "Robotics & AI",      "ROBO": "Robotics",
    "JETS": "Airlines",           "IBB": "Biotech",
    "XBI": "Biotech",             "KRE": "Regional Banks",
    "KBE": "Banks",               "OIH": "Oil Services",
    "XOP": "Oil & Gas E&P",       "OIL": "Oil",
    "OIH": "Oil Services",        "OIL": "Oil",
    "FCG": "Natural Gas",         "WEAT": "Wheat",
    "CORN": "Corn",               "GLD": "Gold",
    "SLV": "Silver",              "USO": "Crude Oil",
    "TLT": "20yr Treasuries",     "HYG": "High Yield Bonds",
    "VNQ": "Real Estate",         "EEM": "Emerging Markets",
    "IBIT": "Bitcoin",            "FBTC": "Bitcoin",
    "ETHA": "Ethereum",           "BLOK": "Blockchain",
}


def etf_theme(symbol: str) -> Optional[str]:
    return ETF_THEMES.get(symbol.upper())


# Common leveraged & inverse ETFs. A "STRONG BUY" signal on any of these is
# misleading — they're directional bets, not trade-worthy stocks.
# Sourced from Direxion, ProShares, MicroSectors, GraniteShares public lists.
LEVERAGED_INVERSE_ETFS = frozenset({
    # Direxion 3x
    "TQQQ", "SQQQ", "SPXL", "SPXU", "SPXS", "UPRO", "TNA", "TZA",
    "SOXL", "SOXS", "FAS", "FAZ", "TECL", "TECS", "CURE", "RXD",
    "DUST", "NUGT", "JNUG", "JDST", "GUSH", "DRIP", "ERX", "ERY",
    "LABU", "LABD", "RETL", "WANT", "WEBL", "WEBS", "DPST", "WDRW",
    "TPOR", "DRV", "DRN", "EDC", "EDZ", "YINN", "YANG", "BRZU",
    "MIDU", "TYO", "TYD", "FNGU", "FNGD",
    # ProShares 2x / 3x / inverse
    "SSO", "SDS", "QLD", "QID", "DDM", "DXD", "URTY", "SRTY",
    "MVV", "MZZ", "ROM", "REW", "UYG", "SKF", "DIG", "DUG",
    "UCO", "SCO", "BOIL", "KOLD", "AGQ", "ZSL", "UGL", "GLL",
    "UVXY", "SVXY", "VIXY", "VIXM", "TBT", "TMV", "TMF", "UBT",
    "EFO", "EUM", "EEV", "EFU", "BIB", "BIS", "USD", "SSG",
    "EZJ", "EWV",
    # MicroSectors / Bank of Montreal
    "FNGB", "FNGS", "BNKU", "BNKD", "OILU", "OILD",
    # GraniteShares 3x single-stock
    "TSLL", "TSLT", "TSLZ", "TSDD", "NVDL", "NVD", "NVDD", "NVDU",
    "AAPB", "AAPD", "MSFU", "MSFD", "METAL", "METX"  # was "META L" (invalid token),
})


def is_leveraged_or_inverse(symbol: str) -> bool:
    return symbol.upper() in LEVERAGED_INVERSE_ETFS
