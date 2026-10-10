"""Read-only social attention intelligence for research context.

Reddit (OAuth via buzz_sources), Stocktwits, public RSS and, when an X API
token is configured, X posts are normalized into one bounded, auditable pulse. This module is deliberately display-only: social activity can
explain attention and uncertainty, but never creates or approves a trade.
"""
from __future__ import annotations

import html
import copy
import hashlib
import re
import threading
import time
import os
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import requests

from news_stream import AMBIGUOUS_TICKERS, FUTURE_TOLERANCE_SEC, MAX_FEED_BYTES, _ts_seconds, short_error

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_TTL = 60.0
WINDOW_SEC = 24 * 3600
_UA = "daytrade-signal-desk/1.0 (research; read-only)"
_TICKER_RE = re.compile(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,5})\b|(?<![A-Za-z0-9])([A-Z]{2,5})(?![A-Za-z0-9])")
_POSITIVE = {"bullish", "calls", "moon", "squeeze", "breakout", "buy", "rocket", "long", "upside"}
_NEGATIVE = {"bearish", "puts", "dump", "sell", "short", "fraud", "scam", "downside", "bagholder"}
_STOPWORDS = {
    "THE", "AND", "FOR", "THIS", "THAT", "WITH", "FROM", "HAVE", "JUST", "YOUR",
    "WHAT", "WHEN", "WILL", "BEEN", "ONLY", "VERY", "CALL", "PUT", "YOLO", "DD",
    "CEO", "CFO", "USA", "USD", "IMO", "ATH", "FOMO", "IV", "EPS",
}
_SUBREDDIT_RE = re.compile(r"[A-Za-z0-9_]{2,21}")
_SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.]{0,9}")
_source_status: dict[str, dict[str, Any]] = {}
_stocktwits_retry: dict[str, Any] = {"until": 0.0, "error": None}
DEFAULT_SUBREDDITS = ("wallstreetbets", "stocks", "investing", "options", "Daytrading", "Shortsqueeze")
DEFAULT_RSS_FEEDS = (
    "https://www.reddit.com/r/wallstreetbets/.rss",
    "https://www.reddit.com/r/stocks/.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://feeds.bbci.co.uk/news/business/rss.xml",
)


def _clean_text(value: Any, limit: int = 500) -> str:
    text = html.unescape(re.sub(r"\s+", " ", str(value or ""))).strip()
    return text[:limit]


def _tickers(text: str, allowed: set[str] | None = None) -> list[str]:
    found: list[str] = []
    for match in _TICKER_RE.finditer(text or ""):
        cashtag = match.group(1)
        symbol = (cashtag or match.group(2) or "").upper()
        if symbol in _STOPWORDS or (allowed and symbol not in allowed):
            continue
        # "AI", "ON", "NOW"... are words far more often than tickers: count
        # them only when written as a $cashtag.
        if not cashtag and symbol in AMBIGUOUS_TICKERS:
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


def _note_source(name: str, error: str | None, rows: int = 0) -> None:
    with _lock:
        _source_status[name] = {"ok": error is None, "error": short_error(error),
                                "rows": rows, "checked_at": datetime.now(timezone.utc).isoformat()}


def _created_ts(value: Any, now: float) -> float | None:
    ts = _ts_seconds(value)
    if ts is None or ts > now + FUTURE_TOLERANCE_SEC:
        return None
    return ts


def _reddit_rows(subreddit: str, limit: int = 40) -> list[dict[str, Any]]:
    """Newest posts in one subreddit via buzz_sources' Reddit client.

    Uses OAuth when REDDIT_CLIENT_ID/SECRET are set. The historical public JSON
    opt-in is not an access guarantee or a workaround for Reddit approval.
    """
    source = f"reddit r/{subreddit}"
    if not _SUBREDDIT_RE.fullmatch(subreddit or ""):
        _note_source("reddit", f"Invalid subreddit name: {subreddit!r}")
        return []
    import buzz_sources
    data, error = buzz_sources._reddit_get(
        f"/r/{subreddit}/new", params={"limit": min(max(limit, 1), 100), "raw_json": 1})
    if error or not isinstance(data, dict):
        _note_source(source, error or "Unexpected Reddit response")
        return []
    children = (data.get("data") or {}).get("children") or []
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
        try:
            created = float(data.get("created_utc") or 0)
        except (TypeError, ValueError):
            created = 0.0
        rows.append({
            "source": f"reddit_{subreddit.lower()}",
            "source_id": str(data.get("id") or data.get("name") or ""),
            "thread_id": str(data.get("id") or ""),
            "author_hash": _author_hash(data.get("author")),
            "created_at": _iso(_created_ts(created, time.time())),
            "title": title,
            "excerpt": body[:300],
            "ticker_candidates": _tickers(text),
            "sentiment": _sentiment(text),
            "score": int(data.get("score") or 0),
            "comments": int(data.get("num_comments") or 0),
            "permalink": f"https://www.reddit.com{data.get('permalink')}" if data.get("permalink") else None,
        })
    _note_source(source, None, len(rows))
    return rows


def _stocktwits_rows(symbols: list[str], limit: int = 12) -> list[dict[str, Any]]:
    clean = [s for s in dict.fromkeys(str(x).upper().lstrip("$") for x in symbols) if _SYMBOL_RE.fullmatch(s)][:20]
    if not clean:
        return []
    # One request per symbol; bounded concurrency instead of up to 20 x 6s in series.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="stocktwits") as pool:
        batches = list(pool.map(lambda sym: _stocktwits_symbol(sym, limit), clean))
    rows = [row for batch in batches if batch for row in batch]
    failed = sum(1 for batch in batches if batch is None)
    with _lock:
        unavailable = (_stocktwits_retry["error"] if time.time() < _stocktwits_retry["until"] else None)
    _note_source("stocktwits", (unavailable or f"Stocktwits unavailable for {failed} of {len(clean)} symbols") if failed else None, len(rows))
    return rows


def _stocktwits_symbol(symbol: str, limit: int) -> list[dict[str, Any]] | None:
    """Messages for one symbol; None when the request failed (vs [] for no messages)."""
    rows: list[dict[str, Any]] = []
    with _lock:
        if time.time() < _stocktwits_retry["until"]:
            return None
    try:
        response = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json",
            headers={"User-Agent": _UA, "Accept": "application/json"},
            timeout=6,
        )
        if response.status_code != 200:
            if response.status_code in (401, 403, 429):
                delay = 3600 if response.status_code in (401, 403) else 900
                try:
                    retry = float(getattr(response, "headers", {}).get("Retry-After", 0))
                    if 0 <= retry < float("inf"):
                        delay = max(delay, retry)
                except (TypeError, ValueError):
                    pass
                with _lock:
                    _stocktwits_retry.update(until=time.time() + delay,
                        error=f"Stocktwits access unavailable (HTTP {response.status_code}); automatic reads are backing off. Check provider access.")
            return None
        payload = response.json()
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            return None
        messages = messages[:limit]
    except (OSError, ValueError, requests.RequestException):
        return None
    for message in messages:
        if not isinstance(message, dict):
            continue
        body = _clean_text(message.get("body"), 700)
        if not body:
            continue
        created = str(message.get("created_at") or "")
        entities = message.get("entities") if isinstance(message.get("entities"), dict) else {}
        sentiment = entities.get("sentiment") if isinstance(entities.get("sentiment"), dict) else {}
        label = str(sentiment.get("basic") or "").lower()
        if label not in {"bullish", "bearish"}:
            label = _sentiment(body)
        user = message.get("user") if isinstance(message.get("user"), dict) else {}
        conversation = message.get("conversation") if isinstance(message.get("conversation"), dict) else {}
        likes = message.get("likes")
        likes = likes.get("total") if isinstance(likes, dict) else likes
        try:
            likes = max(0, int(likes or 0))
        except (TypeError, ValueError, OverflowError):
            likes = 0
        username = str(user.get("username") or "")
        message_id = str(message.get("id") or "")
        permalink = (f"https://stocktwits.com/{username}/message/{message_id}"
                     if username and message_id.isdigit() else f"https://stocktwits.com/symbol/{symbol}")
        rows.append({
            "source": "stocktwits",
            "source_id": message_id,
            "thread_id": str(conversation.get("parent_message_id") or ""),
            "author_hash": _author_hash(user.get("id")),
            "created_at": created,
            "title": f"${symbol.upper()} Stocktwits message",
            "excerpt": body[:300],
            "ticker_candidates": [symbol.upper()],
            "sentiment": label,
            "score": likes,
            "comments": 0,
            "permalink": permalink,
        })
    return rows


def _rss_rows(feeds: tuple[str, ...], limit: int = 15) -> list[dict[str, Any]]:
    feeds = tuple(f for f in feeds if str(f).startswith(("https://", "http://")))[:6]
    if not feeds:
        return []
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="social-rss") as pool:
        batches = list(pool.map(lambda feed: _rss_feed(feed, limit), feeds))
    rows = [row for batch in batches for row in batch]
    _note_source("rss", None if rows or not feeds else "No RSS feed returned items", len(rows))
    return rows


def _rss_feed(feed: str, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        response = requests.get(
            feed, headers={"User-Agent": _UA, "Accept": "application/rss+xml, application/xml"},
            timeout=6,
        )
        if response.status_code != 200:
            return rows
        content = getattr(response, "content", b"") or b""
        if len(content) > MAX_FEED_BYTES:
            return rows
        root = ET.fromstring(content)
    except (OSError, ET.ParseError, requests.RequestException):
        return rows
    for item in _feed_entries(root)[:limit]:
        title = _clean_text(item["title"], 300)
        link = _clean_text(item["link"], 500)
        description = _clean_text(re.sub(r"<[^>]+>", " ", item["summary"] or ""), 700)
        if not title:
            continue
        text = f"{title} {description}"
        rows.append({
            "source": "public_rss",
            "source_id": hashlib.sha256(f"{feed}|{link}|{title}".encode("utf-8")).hexdigest()[:16],
            "thread_id": "",
            "author_hash": None,  # A feed is not an identified person.
            "kind": "headline",
            "created_at": _iso(_ts_seconds(_clean_text(item["published"], 80))),
            "title": title,
            "excerpt": description[:300],
            "ticker_candidates": _tickers(text),
            "sentiment": _sentiment(text),
            "score": 0,
            "comments": 0,
            "permalink": link or None,
        })
    return rows


_ATOM = "{http://www.w3.org/2005/Atom}"


def _feed_entries(root: ET.Element) -> list[dict[str, str]]:
    """RSS <item> and Atom <entry> as plain dicts (user feeds may be either)."""
    entries = []
    for item in root.findall(".//item"):
        entries.append({"title": item.findtext("title") or "", "link": item.findtext("link") or "",
                        "summary": item.findtext("description") or "", "published": item.findtext("pubDate") or ""})
    for entry in root.findall(f".//{_ATOM}entry"):
        link = next((el.get("href") or "" for el in entry.findall(f"{_ATOM}link")
                     if el.get("rel") in (None, "alternate")), "")
        entries.append({"title": entry.findtext(f"{_ATOM}title") or "", "link": link,
                        "summary": entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content") or "",
                        "published": entry.findtext(f"{_ATOM}published") or entry.findtext(f"{_ATOM}updated") or ""})
    return entries


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _x_rows(symbols: list[str]) -> list[dict[str, Any]]:
    """X posts for the watchlist when X_BEARER_TOKEN is set; [] (no network) otherwise."""
    import x_watcher
    rows = x_watcher.social_rows(symbols)
    _note_source("x", x_watcher.status().get("error"), len(rows))
    return rows


def _configured_feeds() -> tuple[str, ...]:
    raw = os.environ.get("SOCIAL_RSS_FEEDS", "")
    return tuple(x.strip() for x in raw.split(",") if x.strip()) or DEFAULT_RSS_FEEDS


def _author_hash(value: Any) -> str | None:
    author = str(value or "").strip()
    if not author or author.lower() in {"[deleted]", "[unknown]", "[removed]"}:
        return None
    return hashlib.sha256(author.encode("utf-8")).hexdigest()[:16]


def _family(row: dict[str, Any]) -> str:
    source = str(row.get("source") or "unknown")
    return "reddit" if source.startswith("reddit_") else source


def _recent_unique(rows: list[dict[str, Any]], now: float) -> tuple[list[dict[str, Any]], dict[str, int]]:
    excluded = {"stale": 0, "undated_or_future": 0, "duplicates": 0}
    unique: dict[tuple, dict[str, Any]] = {}
    # Prefer dated API posts to RSS copies of the same Reddit discussion.
    for original in sorted(rows, key=lambda r: r.get("source") == "public_rss"):
        row = copy.deepcopy(original)
        stamp = _created_ts(row.get("created_at"), now)
        if stamp is None:
            excluded["undated_or_future"] += 1
            continue
        if now - stamp > WINDOW_SEC:
            excluded["stale"] += 1
            continue
        row["created_ts"] = stamp
        # Stable message IDs deduplicate messages returned for several symbols.
        url = str(row.get("permalink") or "").split("?", 1)[0].rstrip("/")
        key = (("url", url) if url and _family(row) != "stocktwits" else
               (_family(row), str(row["source_id"])) if row.get("source_id") else
               (_family(row), stamp, row.get("author_hash"), row.get("title"), row.get("excerpt")))
        if key in unique:
            excluded["duplicates"] += 1
            saved = unique[key]
            saved["ticker_candidates"] = sorted(set(saved.get("ticker_candidates") or []) | set(row.get("ticker_candidates") or []))
        else:
            unique[key] = row
    return sorted(unique.values(), key=lambda r: r["created_ts"], reverse=True), excluded


def _summarize(rows: list[dict[str, Any]], watchlist: list[str]) -> dict[str, Any]:
    allowed = {str(t).upper().lstrip("$") for t in watchlist if t}
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        tickers = _tickers(f"{row.get('title', '')} {row.get('excerpt', '')}", allowed) if allowed else row.get("ticker_candidates", [])
        if allowed:
            tickers = sorted(set(tickers) | {t for t in (row.get("ticker_candidates") or []) if t in allowed})
        for ticker in tickers:
            by_ticker.setdefault(ticker, []).append(row)
    pulse = []
    for ticker, items in by_ticker.items():
        posts = [i for i in items if i.get("source") != "public_rss"]
        # A single text label cannot reliably assign opposing views to several tickers.
        directional = [i for i in posts if len(set(i.get("ticker_candidates") or []) |
                       set(_tickers(f"{i.get('title', '')} {i.get('excerpt', '')}"))) == 1]
        bullish = sum(i.get("sentiment") == "bullish" for i in directional)
        bearish = sum(i.get("sentiment") == "bearish" for i in directional)
        authors = len({(_family(i), i["author_hash"]) for i in posts if i.get("author_hash")})
        threads = len({(_family(i), i.get("thread_id") or i.get("source_id") or i.get("permalink") or id(i)) for i in posts})
        sources = sorted({str(i.get("source") or "unknown") for i in items})
        pulse.append({
            "ticker": ticker,
            "mentions": len(items),
            "sources": sources,
            "unique_threads": threads,
            "unique_authors": authors,
            "bullish": bullish,
            "bearish": bearish,
            "uncertain": len(posts) - bullish - bearish,
            "post_mentions": len(posts), "headline_mentions": len(items) - len(posts),
            "newest_at": _iso(max(i["created_ts"] for i in items)),
            "oldest_at": _iso(min(i["created_ts"] for i in items)),
            "attention_score": round(sum(max(0, int(i.get("score") or 0)) + max(0, int(i.get("comments") or 0)) for i in items) ** 0.5, 2),
            "contributor_share": round(authors / len(posts), 3) if posts else 0,
            "quality": "low" if authors < 2 or authors / max(len(posts), 1) < 0.35 else "moderate",
            "items": items[:5],
        })
    pulse.sort(key=lambda x: (x["mentions"], x["attention_score"]), reverse=True)
    counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or "unknown")
        counts[source] = counts.get(source, 0) + 1
    return {
        "pulse": pulse[:30], "items": rows[:80], "source_counts": counts,
        "display_only": True, "attention_only": True,
    }


def snapshot(watchlist: list[str] | None = None, *, force: bool = False) -> dict[str, Any]:
    watchlist = list(watchlist or [])
    key = tuple(sorted(str(t).upper() for t in watchlist))
    now = time.time()
    with _lock:
        if not force and _cache.get("payload") and 0 <= now - float(_cache.get("at") or 0) < _TTL and _cache.get("key") == key:
            return copy.deepcopy(_cache["payload"])
    subreddits = tuple(
        x.strip() for x in os.environ.get("SOCIAL_SUBREDDITS", "").split(",") if x.strip()
    ) or DEFAULT_SUBREDDITS
    symbols = [str(t).upper().lstrip("$") for t in watchlist if t]
    feeds = _configured_feeds()
    # Sources are independent: fetch them together instead of back to back.
    jobs = [(f"reddit r/{sub}", lambda sub=sub: _reddit_rows(sub)) for sub in subreddits]
    jobs += [("stocktwits", lambda: _stocktwits_rows(symbols)), ("rss", lambda: _rss_rows(feeds)), ("x", lambda: _x_rows(symbols))]
    with _lock:
        _source_status.pop("unexpected", None)  # Superseded by named provider failures.
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix="social-fetch") as pool:
        for name, future in [(name, pool.submit(job)) for name, job in jobs]:
            try:
                rows.extend(future.result() or [])
            except Exception as exc:  # one source never sinks the snapshot
                _note_source(name, f"Could not read source response ({type(exc).__name__})")
    rows, excluded = _recent_unique(rows, now)
    active = [name for name in ("reddit", "stocktwits", "rss", "x")
              if any(str(r.get("source") or "").startswith({"rss": "public_rss"}.get(name, name)) for r in rows)]
    with _lock:
        status = {k: dict(v) for k, v in _source_status.items()}
    payload = {
        "ok": bool(rows),
        "provider": "social_public",
        "source": "reddit_stocktwits_rss" + ("_x" if "x" in active else ""),
        "active_sources": active,
        "source_status": status,
        "subreddits": list(subreddits),
        "rss_feeds": list(_configured_feeds()),
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "error": None if rows else "No dated social posts or headlines from the last 24 hours are available.",
        "window_hours": 24, "window_ends_at": _iso(now), "excluded": excluded,
        "method_note": "Sampled posts, not all activity. Word cues and provider labels can miss sarcasm or negation. Multi-ticker posts are unclear. Feed headlines do not vote. Identified accounts are not verified people or independent opinions.",
        **_summarize(rows, watchlist),
    }
    with _lock:
        _cache.update(at=now, key=key, payload=payload)
    return copy.deepcopy(payload)


def public_status() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "social_public",
        "configured": True,
        "read_only": True,
        "display_only": True,
        "sources": ["reddit", "stocktwits_public", "public_rss", "x"],
        "x": _x_public_status(),
        "docs": {
            "x": "https://docs.x.com/x-api/posts/search-recent-posts (X_BEARER_TOKEN)",
            "reddit": "https://www.reddit.com/dev/api/",
            "stocktwits": "https://api.stocktwits.com/developers/docs",
            "rss": "SOCIAL_RSS_FEEDS environment setting",
        },
    }


def _x_public_status() -> dict[str, Any]:
    try:
        import x_watcher
        return x_watcher.status()
    except Exception as exc:  # noqa: BLE001
        return {"configured": False, "error": f"{type(exc).__name__}: {exc}"[:160]}
