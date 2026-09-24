"""Market watch: market-wide headlines, X posts, social pulse and an
internet-wide trend scan in one display-only view.

Sources
  - Google News RSS: Business topic + "stock market" (last day) headlines
  - MARKET_WATCH_RSS_FEEDS: up to six extra RSS/Atom feeds of the owner's choice
  - X (x_watcher): watchlist cashtags and X_WATCH_ACCOUNTS, when X_BEARER_TOKEN is set
  - social_intelligence: Reddit / Stocktwits / RSS pulse, when social research is on
  - Trend scan: tickers trending on Reddit (buzz cache), Stocktwits trending,
    Yahoo trending, Google Trends daily searches (name-matched), Google News
    market headlines and X, ranked by how many independent places show them
  - Google Finance: quote links (no scraping; Google Finance has no public API)

Nothing here creates, sizes or approves a trade. Trending means attention,
not quality or direction; a ticker on many lists can still be a pump.
"""
from __future__ import annotations

import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlparse

import requests
from flask import Blueprint, jsonify, request

import news_stream

TTL = 300.0
MAX_HEADLINE_AGE = 36 * 3600
UA = "TomahawkDesk/1.0 (market watch; read-only)"
GOOGLE_NEWS_FEEDS = (
    ("Business", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"),
    ("Stock market", "https://news.google.com/rss/search?q=%22stock+market%22+when:1d&hl=en-US&gl=US&ceid=US:en"),
)
YAHOO_TRENDING = "https://query1.finance.yahoo.com/v1/finance/trending/US"
# Nasdaq directory "Listing Exchange" codes -> Google Finance exchange suffixes.
GOOGLE_FINANCE_EXCHANGES = {"Q": "NASDAQ", "N": "NYSE", "A": "NYSEAMERICAN", "P": "NYSEARCA", "Z": "BATS"}
# Source weights for the trend scan: breadth across sources matters more than any one list.
TREND_WEIGHTS = {"reddit": 1.0, "stocktwits": 0.9, "yahoo": 1.0, "google_trends": 1.2, "google_news": 1.1, "x": 0.9}
_CASHTAG_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Z]{1,5})(?![A-Za-z0-9])")
_EXCHANGE_RE = re.compile(r"\b(?:NASDAQ|NYSE|NYSEARCA|NYSE American|AMEX|Nasdaq)\s*:\s*([A-Z]{1,5}(?:\.[A-Z])?)\b")
_CORP_WORDS = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|holdings?|group|n\.?v|s\.?a|ag|se|"
    r"lp|l\.?p|llc|class [a-z]|common stock|ordinary shares?|american depositary shares?|ads|adr|sponsored)\b\.?",
    re.I,
)
# Company short names that are everyday words; never matched from free text.
_COMMON_NAME_WORDS = {
    "target", "match", "gap", "box", "block", "snap", "shell", "visa", "apple", "global", "american", "united",
    "first", "general", "national", "international", "energy", "capital", "financial", "digital", "data", "health",
    "power", "life", "live", "open", "real", "one", "best", "home", "golden", "ocean", "sun", "star", "spirit",
    "compass", "focus", "fortune", "genie", "hope", "liberty", "legacy", "matrix", "nature", "north", "south",
    "summit", "vital", "wave", "bank", "trust", "value", "prime", "select", "core",
}
_FINANCE_TERMS = re.compile(r"\b(stocks?|shares?|earnings|market|dow|nasdaq|s&p|fed|ipo|crypto|bitcoin|rate cut|inflation|tariffs?)\b", re.I)

_lock = threading.RLock()
_refresh_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "key": None, "payload": None}
_index_cache: dict[str, Any] = {"updated_at": object(), "index": None}


# --- small helpers ---------------------------------------------------------

def _get(url: str, *, params: dict | None = None, accept: str = "application/rss+xml, application/xml",
         timeout: float = 8.0) -> bytes:
    response = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA, "Accept": accept})
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}")
    content = response.content or b""
    if len(content) > news_stream.MAX_FEED_BYTES:
        raise ValueError("Feed too large")
    return content


def describe_error(exc: BaseException) -> str:
    """Short, readable reason for the status table (no raw URLs or stack text)."""
    if isinstance(exc, requests.Timeout):
        return "timed out"
    if isinstance(exc, requests.ConnectionError):
        return "could not connect"
    if isinstance(exc, ET.ParseError):
        return "unreadable feed"
    if isinstance(exc, ValueError) and str(exc).startswith(("HTTP ", "Feed too large")):
        return str(exc)
    if isinstance(exc, requests.RequestException):
        return "request failed"
    return type(exc).__name__


def _status(ok: bool, error: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "error": news_stream.short_error(error),
            "checked_at": datetime.now(timezone.utc).isoformat(), **extra}


def short_name(name: str) -> str:
    """'The Home Depot, Inc. - Common Stock' -> 'home depot'."""
    text = re.sub(r"\s+-\s+.*$", "", str(name or ""))
    text = text.replace("&", " and ")
    text = _CORP_WORDS.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    text = re.sub(r"^the ", "", " ".join(text.split()))
    return text.strip()


def google_finance_url(symbol: str, exchange_code: str | None = None) -> str:
    sym = str(symbol or "").upper()  # Google writes class shares with a dot: BRK.B:NYSE
    exchange = GOOGLE_FINANCE_EXCHANGES.get(str(exchange_code or "").upper())
    if exchange:
        return f"https://www.google.com/finance/quote/{quote(sym)}:{exchange}"
    return f"https://www.google.com/finance?q={quote(sym)}"


# --- US listing index (Nasdaq directory already downloaded by the desk) ----

class ListingIndex:
    """Symbols, names and exchanges of US listings, plus a name -> symbol map."""

    def __init__(self, rows: list[dict[str, Any]]):
        self.symbols: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}
        for row in rows or []:
            sym = str(row.get("symbol") or "").upper()
            if not sym:
                continue
            self.symbols[sym] = row
            if row.get("etf"):
                continue
            name = short_name(row.get("name") or "")
            if len(name) < 4 or name in _COMMON_NAME_WORDS:
                continue
            current = self.names.get(name)
            # One company, several share classes (GOOG/GOOGL): keep the shortest symbol.
            if current is None or (len(sym), sym) < (len(current), current):
                self.names[name] = sym
        self.max_words = max((len(n.split()) for n in self.names), default=1)

    @property
    def loaded(self) -> bool:
        return len(self.symbols) >= 1000

    def valid(self, symbol: str) -> bool:
        sym = str(symbol or "").upper()
        if self.loaded:
            return sym in self.symbols
        return bool(re.fullmatch(r"[A-Z]{1,5}", sym))

    def info(self, symbol: str) -> dict[str, Any]:
        sym = str(symbol or "").upper()
        row = self.symbols.get(sym) or {}
        return {"symbol": sym, "name": row.get("name"), "etf": bool(row.get("etf")),
                "google_finance_url": google_finance_url(sym, row.get("exchange"))}

    def match_text(self, text: str) -> list[str]:
        """Tickers named in free text: $cashtags, 'NYSE: X' mentions, distinctive company names."""
        found: list[str] = []
        for sym in _CASHTAG_RE.findall(text or "") + _EXCHANGE_RE.findall(text or ""):
            if self.valid(sym) and sym not in found:
                found.append(sym)
        words = re.sub(r"[^a-z0-9 ]+", " ", (text or "").replace("&", " and ").lower()).split()
        for size in range(min(self.max_words, 4), 0, -1):
            for start in range(len(words) - size + 1):
                phrase = " ".join(words[start:start + size])
                sym = self.names.get(phrase)
                if sym and (size > 1 or len(phrase) >= 5) and sym not in found:
                    found.append(sym)
        return found[:8]

    def match_search_term(self, term: str) -> str | None:
        """A trending search term that names one company ('nvidia', 'nvidia stock', 'nvda stock')."""
        clean = " ".join(re.sub(r"[^a-z0-9 $]+", " ", (term or "").lower()).split())
        if not clean:
            return None
        base = re.sub(r"\s+(stock|stocks|shares|share price|earnings|news)$", "", clean).strip()
        if base.startswith("$") and self.valid(base[1:].upper()):
            return base[1:].upper()
        if base != clean and self.valid(base.upper()) and len(base) >= 2:
            return base.upper()  # "nvda stock"
        return self.names.get(base)


def listing_index(desk) -> ListingIndex:
    try:
        import market_universe
        raw = market_universe.load(desk) if desk is not None else {}
    except Exception:  # noqa: BLE001
        raw = {}
    stamp = raw.get("updated_at")
    with _lock:
        if _index_cache["index"] is not None and _index_cache["updated_at"] == stamp:
            return _index_cache["index"]
    index = ListingIndex(raw.get("symbols") or [])
    with _lock:
        _index_cache.update(updated_at=stamp, index=index)
    return index


# --- headlines --------------------------------------------------------------

def google_headlines(now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    status: dict[str, Any] = {}
    for label, url in GOOGLE_NEWS_FEEDS:
        try:
            parsed = news_stream.parse_google_news_rss(_get(url), limit=40)
            status[label] = _status(True, rows=len(parsed))
        except (ValueError, ET.ParseError, requests.RequestException) as exc:
            status[label] = _status(False, describe_error(exc))
            continue
        for row in parsed:
            row["feed"] = label
            rows.append(row)
    return _fresh_unique(rows, now), status


def configured_feeds() -> list[str]:
    raw = os.environ.get("MARKET_WATCH_RSS_FEEDS") or ""
    feeds = []
    for url in raw.replace(";", ",").split(","):
        url = url.strip()
        parsed = urlparse(url)
        if parsed.scheme == "https" and parsed.netloc and not parsed.username and url not in feeds:
            feeds.append(url)
    return feeds[:6]


def custom_headlines(now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import social_intelligence
    rows: list[dict[str, Any]] = []
    status: dict[str, Any] = {}
    for url in configured_feeds():
        host = urlparse(url).netloc
        try:
            entries = social_intelligence._feed_entries(ET.fromstring(_get(url, accept="application/rss+xml, application/atom+xml, application/xml")))
            status[host] = _status(True, rows=len(entries))
        except (ValueError, ET.ParseError, requests.RequestException) as exc:
            status[host] = _status(False, describe_error(exc))
            continue
        for entry in entries[:30]:
            link = entry["link"].strip()
            if not entry["title"].strip() or not link.startswith(("https://", "http://")):
                continue
            rows.append({"title": " ".join(entry["title"].split()), "link": link, "publisher": host,
                         "ts": entry["published"], "source": "custom_rss", "feed": host})
    return _fresh_unique(rows, now), status


def _fresh_unique(rows: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Enrich, drop undated/stale/future rows and duplicate headlines, newest first."""
    seen: set[str] = set()
    out = []
    for row in rows:
        item = news_stream.enrich_headline(row, now=now)
        ts = item.get("published_ts")
        if ts is None or now - ts > MAX_HEADLINE_AGE or item["headline_key"] in seen:
            continue
        seen.add(item["headline_key"])
        out.append(item)
    out.sort(key=lambda r: r["published_ts"], reverse=True)
    return out[:60]


# --- internet-wide trend scan ------------------------------------------------

def _reddit_trend(watchlist: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import buzz_sources
    buzz = buzz_sources.get_cached_buzz()
    if buzz is None or buzz.get("stale"):
        buzz_sources.kick_background_refresh(watchlist)  # non-blocking; next scan sees it
    if not buzz:
        return [], _status(False, "Reddit buzz is refreshing; it appears on the next scan")
    rows = []
    for row in buzz.get("tickers") or []:
        subs = [s for s in (row.get("subreddits") or []) if s != "stocktwits"]
        if subs and row.get("ticker"):
            rows.append({"ticker": str(row["ticker"]).upper(), "metric": row.get("mentions"),
                         "detail": f"{row.get('mentions')} mentions in r/{', r/'.join(subs[:3])}"})
    errors = buzz.get("errors") or []
    return rows[:25], _status(bool(rows), None if rows else (str(errors[0]) if errors else "No Reddit mentions"),
                             age_sec=buzz.get("cache_age_sec"), stale=bool(buzz.get("stale")))


def _stocktwits_trend() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import buzz_sources
    rows, error = buzz_sources.fetch_stocktwits_trending()
    out = [{"ticker": r["ticker"], "metric": r.get("watchlist_count"), "detail": "Stocktwits trending"}
           for r in rows if r.get("ticker")]
    return out[:30], _status(bool(out), error)


def _yahoo_trend() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import data_sources
    response = requests.get(YAHOO_TRENDING, params={"count": 30}, headers=data_sources.HEADERS, timeout=8)
    if response.status_code != 200:
        return [], _status(False, f"HTTP {response.status_code}")
    result = ((response.json().get("finance") or {}).get("result") or [{}])[0] or {}
    out = [{"ticker": str(q.get("symbol") or "").upper(), "metric": None, "detail": "Yahoo Finance trending"}
           for q in result.get("quotes") or [] if isinstance(q, dict)]
    return out, _status(bool(out), None if out else "Yahoo trending returned no symbols")


def parse_google_trends(content: bytes) -> list[dict[str, Any]]:
    """Google Trends daily trending-searches RSS: term, approximate traffic, related headlines."""
    root = ET.fromstring(content)
    rows = []
    for item in root.findall(".//item"):
        term = " ".join((item.findtext("title") or "").split())
        if not term:
            continue
        traffic, news = None, []
        for child in item.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "approx_traffic":
                traffic = _traffic(child.text)
            elif tag == "news_item_title" and child.text:
                news.append(" ".join(child.text.split()))
        rows.append({"term": term, "traffic": traffic, "news": news[:3]})
    return rows


def _traffic(text: str | None) -> int | None:
    match = re.match(r"\s*([\d.,]+)\s*([KkMm]?)\+?", text or "")
    if not match:
        return None
    try:
        value = float(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return int(value * {"k": 1_000, "m": 1_000_000}.get(match.group(2).lower(), 1))


def _google_trends(index: ListingIndex) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    url = os.environ.get("GOOGLE_TRENDS_RSS") or "https://trends.google.com/trending/rss?geo=US"
    searches = parse_google_trends(_get(url))
    tickers, topics = [], []
    for row in searches:
        sym = index.match_search_term(row["term"])
        if not sym:
            for headline in row["news"]:
                matched = index.match_text(headline)
                if len(matched) == 1:  # one company named across the related coverage
                    sym = matched[0]
                    break
        if sym:
            tickers.append({"ticker": sym, "metric": row["traffic"],
                            "detail": f"Google search \"{row['term']}\" ({row['traffic'] or '?'}+ searches)"})
        elif _FINANCE_TERMS.search(row["term"]) or any(_FINANCE_TERMS.search(h) for h in row["news"]):
            topics.append({"term": row["term"], "traffic": row["traffic"], "news": row["news"]})
    return tickers, topics[:12], _status(True, rows=len(searches), matched=len(tickers))


def _headline_trend(headlines: list[dict[str, Any]], index: ListingIndex) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for row in headlines:
        for sym in index.match_text(row.get("title") or ""):
            counts[sym] = counts.get(sym, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"ticker": s, "metric": n, "detail": f"named in {n} market headline{'s' if n != 1 else ''}"} for s, n in ranked]


def _x_trend() -> list[dict[str, Any]]:
    import x_watcher
    counts: dict[str, int] = {}
    for row in x_watcher.cached_rows():
        for sym in row.get("ticker_candidates") or []:
            counts[sym] = counts.get(sym, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"ticker": s, "metric": n, "detail": f"{n} recent X posts"} for s, n in ranked]


def rank_trends(by_source: dict[str, list[dict[str, Any]]], index: ListingIndex,
                watchlist: list[str] | None = None, limit: int = 30) -> list[dict[str, Any]]:
    """Combine per-source ranked lists; breadth across independent sources ranks first."""
    watch = {str(s).upper() for s in watchlist or []}
    combined: dict[str, dict[str, Any]] = {}
    for source, rows in by_source.items():
        valid = []
        for row in rows:
            sym = str(row.get("ticker") or "").upper().lstrip("$")
            if index.valid(sym) and sym not in {r["ticker"] for r in valid}:
                valid.append(dict(row, ticker=sym))
        size = max(len(valid), 1)
        for rank, row in enumerate(valid, 1):
            slot = combined.setdefault(row["ticker"], {"ticker": row["ticker"], "score": 0.0, "sources": {}})
            slot["sources"][source] = {"rank": rank, "of": len(valid), "metric": row.get("metric"), "detail": row.get("detail")}
            slot["score"] += TREND_WEIGHTS.get(source, 1.0) * (1.0 - (rank - 1) / size)
    out = []
    for slot in combined.values():
        breadth = len(slot["sources"])
        info = index.info(slot["ticker"])
        try:
            import data_sources
            leveraged = data_sources.is_leveraged_or_inverse(slot["ticker"])
        except Exception:  # noqa: BLE001
            leveraged = False
        out.append({
            **info,
            "breadth": breadth,
            "score": round(slot["score"] + 0.5 * (breadth - 1), 3),
            "sources": slot["sources"],
            "in_watchlist": slot["ticker"] in watch,
            "leveraged_or_inverse": leveraged,
            "label": "broad" if breadth >= 3 else ("two sources" if breadth == 2 else "single source"),
        })
    out.sort(key=lambda r: (-r["breadth"], -r["score"], r["symbol"]))
    return out[:limit]


def trend_scan(desk, watchlist: list[str], headlines: list[dict[str, Any]]) -> dict[str, Any]:
    index = listing_index(desk)
    status: dict[str, Any] = {}
    by_source: dict[str, list[dict[str, Any]]] = {}
    topics: list[dict[str, Any]] = []

    def run(name: str, fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - one source never sinks the scan
            status[name] = _status(False, describe_error(exc))
            return None

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="trend-scan") as pool:
        jobs = {
            "reddit": pool.submit(run, "reddit", lambda: _reddit_trend(watchlist)),
            "stocktwits": pool.submit(run, "stocktwits", _stocktwits_trend),
            "yahoo": pool.submit(run, "yahoo", _yahoo_trend),
            "google_trends": pool.submit(run, "google_trends", lambda: _google_trends(index)),
        }
        for name, future in jobs.items():
            result = future.result()
            if result is None:
                continue
            if name == "google_trends":
                rows, topics, status[name] = result
            else:
                rows, status[name] = result
            by_source[name] = rows
    by_source["google_news"] = _headline_trend(headlines, index)
    status["google_news"] = _status(bool(headlines), None if headlines else "no market headlines available",
                                    rows=len(by_source["google_news"]))
    x_rows = run("x", _x_trend) or []
    if x_rows:
        by_source["x"] = x_rows
    status.setdefault("x", _status(bool(x_rows), None if x_rows else "No cached X posts (X off or nothing yet)"))
    return {
        "tickers": rank_trends(by_source, index, watchlist),
        "topics": topics,
        "sources": status,
        "universe_loaded": index.loaded,
        "note": ("Trending means attention, not quality or direction. Several sources can echo one "
                 "story; leveraged/inverse funds and pumps trend too."),
    }


# --- snapshot -----------------------------------------------------------------

def snapshot(desk, cfg: dict[str, Any], *, force: bool = False) -> dict[str, Any]:
    watchlist = [str(s).upper() for s in (cfg.get("watchlist") or []) if s][:40]
    key = (tuple(watchlist), bool(cfg.get("social_enabled")))
    now = time.time()
    with _lock:
        cached = _cache["payload"]
        fresh = cached and _cache["key"] == key and now - _cache["at"] < TTL
        if fresh and not force:
            return dict(cached, from_cache=True)
    if not _refresh_lock.acquire(blocking=cached is None):
        return dict(cached, from_cache=True, refreshing=True)  # another request is refreshing
    try:
        with _lock:  # a request that waited for the lock can reuse what the other one built
            cached = _cache["payload"]
            if not force and cached and _cache["key"] == key and time.time() - _cache["at"] < TTL:
                return dict(cached, from_cache=True)
        payload = _build(desk, cfg, watchlist, now)
        with _lock:
            _cache.update(at=now, key=key, payload=payload)
        return dict(payload, from_cache=False)
    finally:
        _refresh_lock.release()


def _build(desk, cfg: dict[str, Any], watchlist: list[str], now: float) -> dict[str, Any]:
    import social_intelligence
    import x_watcher

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="market-watch") as pool:
        google_job = pool.submit(google_headlines, now)
        custom_job = pool.submit(custom_headlines, now)
        headlines, google_status = google_job.result()
        custom, custom_status = custom_job.result()
    trends = trend_scan(desk, watchlist, headlines + custom)
    x_watcher.poll(watchlist)
    x_rows = x_watcher.cached_rows()
    if cfg.get("social_enabled"):
        try:
            social = social_intelligence.snapshot(watchlist)
        except Exception as exc:  # noqa: BLE001
            social = {"ok": False, "error": str(exc)[:160], "pulse": [], "items": []}
    else:
        social = {"ok": False, "enabled": False, "pulse": [], "items": [],
                  "error": "Social research is off; turn it on in Settings to include Reddit and Stocktwits."}
    index = listing_index(desk)
    return {
        "ok": True,
        "display_only": True,
        "as_of": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "headlines": headlines[:30],
        "custom_headlines": custom[:30],
        "custom_feeds": configured_feeds(),
        "trends": trends,
        "x": {"status": x_watcher.status(),
              "accounts": [r for r in x_rows if r.get("kind") == "account"][:20],
              "cashtags": [r for r in x_rows if r.get("kind") == "cashtag"][:20]},
        "social": {k: social.get(k) for k in ("ok", "enabled", "error", "pulse", "source_status", "active_sources", "scanned_at")},
        "watchlist": [index.info(sym) for sym in watchlist],
        "sources": {"google_news": google_status, "custom_rss": custom_status},
        "limitations": ("Headlines and posts are untrusted third-party text, shown for context only. "
                        "Missing items do not mean no news. Google Finance links open Google's page; "
                        "no Google Finance data is scraped."),
    }


def register(app, desk) -> None:
    bp = Blueprint("market_watch", __name__)

    @bp.get("/api/market-watch")
    def market_watch():
        force = str(request.args.get("force") or "").lower() in {"1", "true", "yes"}
        try:
            return jsonify(snapshot(desk, desk.load_config(), force=force))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)[:200], "display_only": True}), 500

    app.register_blueprint(bp)
