"""Live read of r/wallstreetbets discussion threads.

Threads: the Daily Discussion, "What Are Your Moves Tomorrow" (the nightly thread), the
Weekend Discussion, post-market threads and any other stickied megathread. The newest
comments are read every two minutes around the trading day (ten minutes otherwise) through
the Reddit client in buzz_sources (OAuth; public JSON only with REDDIT_PUBLIC_JSON=1).

Per ticker it keeps mentions in the last 15 and 60 minutes, the hour before, distinct
authors, and a bullish/bearish lean from plain keywords ("calls", "puts", 🚀...).

Owner decision: WSB can only make automated trading more careful. A crowded ticker (a
mention spike from many authors) gets the configured caution: half size, skip, or a note.
WSB never starts, adds to or sizes up a trade, comment text is never sent to a model, and
when Reddit is unavailable nothing is blocked (the caution simply does not apply).

Live Chat: WSB's Live Chat tab runs on Reddit's chat service, which has no public API.
A pasted chat export (Buzz → Live chat) is included while it is fresh.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SUB = "wallstreetbets"
WINDOW_SEC = 3 * 3600          # mentions kept (the last hour and the hour before, with margin)
DISCOVERY_SEC = 15 * 60
MAX_THREADS = 4
MAX_MENTIONS = 60_000
MAX_SEEN = 4_000               # remembered comment ids per thread
FRESH_SEC = 15 * 60            # older data is not used for cautions
THREAD_LABELS = {
    "wsb_daily": "Daily Discussion",
    "wsb_waymt": "What Are Your Moves Tomorrow",
    "wsb_weekend": "Weekend Discussion",
    "wsb_post_day": "After-hours / post-market thread",
    "sticky": "Stickied thread",
}
BOTS = {"automoderator", "visualmod", "wsbapp", "[deleted]"}
# WSB slang and abbreviations that look like tickers (in addition to buzz_sources' list).
EXTRA_DENY = {"LFG", "EOD", "EOW", "GUH", "FD", "FDS", "LMAO", "JPOW", "BTFD", "TA", "RSI", "MACD",
              "PT", "ER", "AH", "PM", "OP", "MM", "MMS", "IRS", "ETA", "WAYMT", "SPAC", "OTC", "TLDR",
              "DCA", "PDT", "YTD", "QE", "QT", "WW", "III", "II", "LOL", "GG", "RIP", "NGL", "TBH"}
BULL = {"call", "calls", "moon", "mooning", "rocket", "bull", "bullish", "long", "buy", "buying", "bought",
        "tendies", "printing", "squeeze", "send", "sending", "ripping", "breakout", "green"}
BEAR = {"put", "puts", "short", "shorting", "shorted", "bear", "bearish", "dump", "dumping", "drill",
        "drilling", "crash", "crashing", "sell", "selling", "sold", "rug", "rugged", "red", "tank",
        "tanking", "bagholder", "bagholding", "overvalued"}
BULL_EMOJI = ("🚀", "📈", "🐂", "💎")
BEAR_EMOJI = ("📉", "🌈🐻", "🐻", "🩸")
_TICKER_RE = re.compile(r"\$([A-Za-z]{1,5})\b|(?<![A-Za-z0-9$])([A-Z]{2,5})(?![A-Za-z0-9])")

_lock = threading.RLock()
_poll_lock = threading.Lock()
_state: dict[str, Any] = {"threads": {}, "mentions": deque(), "last_poll": 0.0, "last_ok": 0.0,
                          "last_discovery": 0.0, "coverage_since": None, "error": None,
                          "comments_read": 0, "loaded": False}
_desk = None


# --- text -----------------------------------------------------------------------

def _deny() -> set[str]:
    try:
        import buzz_sources
        base = set(buzz_sources.TICKER_DENYLIST)
    except Exception:  # noqa: BLE001
        base = set()
    try:
        from news_stream import AMBIGUOUS_TICKERS
    except Exception:  # noqa: BLE001
        AMBIGUOUS_TICKERS = set()
    return base | EXTRA_DENY | set(AMBIGUOUS_TICKERS)


_DENY_CACHE: set[str] | None = None


def tickers_in(text: str, valid=None) -> list[str]:
    """$cashtags, or 2-5 capital letters that are a listed symbol and not slang/word-like."""
    global _DENY_CACHE
    if _DENY_CACHE is None:
        _DENY_CACHE = _deny()
    found: list[str] = []
    for match in _TICKER_RE.finditer(text or ""):
        cash = match.group(1)
        sym = (cash or match.group(2) or "").upper()
        if not cash and sym in _DENY_CACHE:
            continue
        if valid is not None and not valid(sym):
            continue
        if sym not in found:
            found.append(sym)
    return found[:6]


def sentiment(text: str) -> str:
    raw = text or ""
    words = re.findall(r"[a-z]+", raw.lower())
    pos = sum(w in BULL for w in words) + sum(raw.count(e) for e in BULL_EMOJI)
    neg = sum(w in BEAR for w in words) + sum(raw.count(e) for e in BEAR_EMOJI)
    return "bull" if pos > neg else "bear" if neg > pos else "neutral"


def _author_key(name: Any) -> str:
    return hashlib.sha1(str(name or "").lower().encode()).hexdigest()[:10]


# --- thread discovery and polling ---------------------------------------------------

def configured() -> bool:
    try:
        import buzz_sources
        if buzz_sources._reddit_oauth_configured():
            return True
    except Exception:  # noqa: BLE001
        return False
    return (os.environ.get("REDDIT_PUBLIC_JSON") or "").strip().lower() in ("1", "true", "yes", "on")


def pick_threads(posts: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    """Newest thread per megathread kind (last 36 hours), plus other stickied posts."""
    import buzz_sources
    best: dict[str, dict[str, Any]] = {}
    for post in posts:
        pid = str(post.get("id") or "")
        created = post.get("created_utc")
        if not pid or not isinstance(created, (int, float)) or created > now + 300:
            continue
        tag = buzz_sources._classify_megathread(str(post.get("title") or ""))
        if not tag:
            if not post.get("stickied"):
                continue
            tag = "sticky"
        elif now - created > 36 * 3600:
            continue
        key = tag if tag != "sticky" else "sticky:" + pid
        if key not in best or created > best[key]["created_utc"]:
            best[key] = {"id": pid, "tag": tag, "label": THREAD_LABELS.get(tag, "WSB thread"),
                         "title": str(post.get("title") or "")[:160], "created_utc": float(created),
                         "permalink": str(post.get("permalink") or ""), "num_comments": post.get("num_comments"),
                         "stickied": bool(post.get("stickied"))}
    order = ["wsb_daily", "wsb_waymt", "wsb_weekend", "wsb_post_day"]
    rows = sorted(best.values(), key=lambda r: (order.index(r["tag"]) if r["tag"] in order else 9, -r["created_utc"]))
    return rows[:MAX_THREADS]


def _valid_symbol():
    if _desk is None:
        return None
    try:
        import market_watch
        index = market_watch.listing_index(_desk)
        return index.valid if index.loaded else None
    except Exception:  # noqa: BLE001
        return None


def ingest_comments(thread: dict[str, Any], comments: list[dict[str, Any]], now: float, valid=None) -> int:
    """Record ticker mentions from unseen comments. Returns how many new comments were read."""
    new = 0
    with _lock:
        slot = _state["threads"].setdefault(thread["id"], dict(thread, seen=[], comments_read=0))
        seen = set(slot.get("seen") or [])
        for comment in comments:
            cid = str(comment.get("id") or "")
            created = comment.get("created_utc")
            if not cid or cid in seen or not isinstance(created, (int, float)):
                continue
            seen.add(cid)
            slot["seen"].append(cid)
            if created > now + 120 or now - created > WINDOW_SEC:
                continue
            if str(comment.get("author") or "").lower() in BOTS:
                continue
            new += 1
            body = str(comment.get("body") or "")
            symbols = tickers_in(body, valid)
            if not symbols:
                continue
            lean, author = sentiment(body), _author_key(comment.get("author"))
            for sym in symbols:
                _state["mentions"].append((float(created), sym, author, lean, thread["tag"]))
        slot["seen"] = slot["seen"][-MAX_SEEN:]
        slot["comments_read"] = int(slot.get("comments_read") or 0) + new
        slot.update({k: thread[k] for k in ("title", "num_comments", "label", "tag", "permalink")})
        slot["last_read_at"] = now
        while len(_state["mentions"]) > MAX_MENTIONS or (_state["mentions"] and now - _state["mentions"][0][0] > WINDOW_SEC):
            _state["mentions"].popleft()
        _state["comments_read"] = int(_state["comments_read"]) + new
    return new


def poll(now: float | None = None, *, force_discovery: bool = False) -> dict[str, Any]:
    """Discover threads when due, then read the newest comments of each. Never raises."""
    import buzz_sources
    now = time.time() if now is None else now
    if not configured():
        with _lock:
            _state["error"] = "Reddit is not connected: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET"
        return status(now)
    if not _poll_lock.acquire(blocking=False):
        return status(now)
    try:
        _load()
        errors = []
        with _lock:
            due = force_discovery or not _state["threads"] or now - _state["last_discovery"] > DISCOVERY_SEC
        if due:
            posts = []
            for sort, limit in (("hot", 25), ("new", 50)):
                rows, err = buzz_sources.fetch_subreddit_listing(SUB, sort=sort, limit=limit)
                if err:
                    errors.append(f"r/{SUB}/{sort}: {_short(err)}")
                posts += rows
            threads = pick_threads(posts, now)
            with _lock:
                if threads or not errors:
                    keep = {t["id"] for t in threads}
                    _state["threads"] = {tid: row for tid, row in _state["threads"].items() if tid in keep}
                    for thread in threads:
                        _state["threads"].setdefault(thread["id"], dict(thread, seen=[], comments_read=0)).update(
                            {k: v for k, v in thread.items()})
                    _state["last_discovery"] = now
        with _lock:
            current = [dict(t) for t in _state["threads"].values()]
        valid, ok = _valid_symbol(), 0
        for thread in current:
            comments, err = buzz_sources.fetch_post_comments(thread.get("permalink") or "", limit=500,
                                                             article_id=thread["id"], sort="new", depth=1)
            with _lock:
                _state["threads"].get(thread["id"], {})["error"] = _short(err) if err else None
            if err:
                errors.append(f"{thread.get('label')}: {_short(err)}")
                continue
            ingest_comments(thread, comments, now, valid)
            ok += 1
        with _lock:
            _state["last_poll"] = now
            if ok:
                _state["last_ok"] = now
                _state["coverage_since"] = _state["coverage_since"] or now
            elif current:
                _state["coverage_since"] = None  # a gap: velocity needs a fresh hour of history
            _state["error"] = "; ".join(errors[:3]) or (None if current else "No WSB discussion thread found yet")
        _save()
        return status(now)
    except Exception as exc:  # noqa: BLE001 - WSB problems never reach trading
        with _lock:
            _state["error"] = f"WSB read failed: {type(exc).__name__}"
        return status(now)
    finally:
        _poll_lock.release()


def _short(err: Any) -> str:
    text = str(err or "")
    match = re.search(r"HTTP \d{3}", text)
    if match:
        return match.group(0)
    if "timeout" in text.lower():
        return "timed out"
    if "oauth" in text.lower():
        return "Reddit sign-in failed"
    return text.split(" for http")[0][:80]


# --- statistics -------------------------------------------------------------------

def _live_chat_counts(now: float) -> dict[str, int]:
    try:
        import buzz_sources
        paste = buzz_sources.get_live_chat_paste()
        stamp = datetime.fromisoformat(str(paste.get("parsed_at"))).timestamp() if paste.get("parsed_at") else None
    except Exception:  # noqa: BLE001
        return {}
    if stamp is None or not 0 <= now - stamp <= 1800:
        return {}
    return {str(r.get("ticker")): int(r.get("mentions") or 0) for r in paste.get("tickers") or [] if r.get("ticker")}


def ticker_stats(now: float | None = None) -> list[dict[str, Any]]:
    now = time.time() if now is None else now
    with _lock:
        rows = list(_state["mentions"])
        since = _state["coverage_since"]
    history = since is not None and now - since >= 2 * 3600
    per: dict[str, dict[str, Any]] = {}
    for ts, sym, author, lean, tag in rows:
        age = now - ts
        if age < -120 or age > 2 * 3600:
            continue
        row = per.setdefault(sym, {"ticker": sym, "m15": 0, "m60": 0, "prev60": 0, "bull": 0, "bear": 0,
                                   "authors": set(), "threads": {}})
        if age <= 3600:
            row["m60"] += 1
            row["authors"].add(author)
            row["bull"] += lean == "bull"
            row["bear"] += lean == "bear"
            row["threads"][tag] = row["threads"].get(tag, 0) + 1
            if age <= 900:
                row["m15"] += 1
        else:
            row["prev60"] += 1
    for sym, count in _live_chat_counts(now).items():
        per.setdefault(sym, {"ticker": sym, "m15": 0, "m60": 0, "prev60": 0, "bull": 0, "bear": 0,
                             "authors": set(), "threads": {}})["live_chat_pasted"] = count
    out = []
    for row in per.values():
        leaned = row["bull"] + row["bear"]
        out.append({
            "ticker": row["ticker"], "mentions_15m": row["m15"], "mentions_60m": row["m60"],
            "mentions_prev_60m": row["prev60"] if history else None,
            "velocity": round(row["m60"] / max(row["prev60"], 1), 2) if history else None,
            "authors_60m": len(row["authors"]),
            "bull_share": round(row["bull"] / leaned, 2) if leaned else None,
            "threads": row["threads"], "live_chat_pasted": row.get("live_chat_pasted", 0),
        })
    out.sort(key=lambda r: (-r["mentions_60m"], -r["live_chat_pasted"], r["ticker"]))
    for rank, row in enumerate(out, 1):
        row["rank"] = rank
    return out


def fresh(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    with _lock:
        return bool(_state["last_ok"]) and now - _state["last_ok"] <= FRESH_SEC


def crowding(symbol: str, cfg: dict[str, Any] | None = None, now: float | None = None,
             stats: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Is this ticker crowded on WSB right now? Unknown (no fresh data) is never crowded."""
    cfg = cfg or {}
    now = time.time() if now is None else now
    sym = str(symbol or "").upper()
    if not fresh(now):
        return {"available": False, "crowded": False, "ticker": sym}
    rows = stats if stats is not None else ticker_stats(now)
    row = next((r for r in rows if r["ticker"] == sym), None)
    if not row:
        return {"available": True, "crowded": False, "ticker": sym, "mentions_60m": 0}
    min_mentions = int(cfg.get("wsb_crowd_min_mentions", 25) or 25)
    velocity_min = float(cfg.get("wsb_crowd_velocity", 3.0) or 3.0)
    enough = row["mentions_60m"] >= min_mentions and row["authors_60m"] >= max(8, min_mentions // 3)
    spike = row["velocity"] is not None and row["velocity"] >= velocity_min
    heavy = row["rank"] <= 3 and row["mentions_60m"] >= 2 * min_mentions
    crowded = bool(enough and (spike or heavy))
    lean = row["bull_share"]
    lean_text = "" if lean is None else f", {round(lean * 100)}% bullish" if lean >= 0.5 else f", {round((1 - lean) * 100)}% bearish"
    trend = f" ({row['velocity']}x the hour before)" if row["velocity"] is not None else ""
    return {"available": True, "crowded": crowded, "ticker": sym, "mentions_60m": row["mentions_60m"],
            "authors_60m": row["authors_60m"], "velocity": row["velocity"], "bull_share": lean, "rank": row["rank"],
            "reason": (f"{sym} is crowded on WSB: {row['mentions_60m']} mentions from {row['authors_60m']} people "
                       f"in the last hour{trend}{lean_text}") if crowded else None}


def status(now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    with _lock:
        threads = [{"tag": t.get("tag"), "label": t.get("label"), "title": t.get("title"),
                    "url": "https://www.reddit.com" + t["permalink"] if str(t.get("permalink") or "").startswith("/r/") else None,
                    "comments_read": t.get("comments_read", 0), "num_comments": t.get("num_comments"),
                    "last_read_at": _iso(t.get("last_read_at")), "error": t.get("error")}
                   for t in _state["threads"].values()]
        since = _state["coverage_since"]
        return {"configured": configured(), "fresh": bool(_state["last_ok"]) and now - _state["last_ok"] <= FRESH_SEC,
                "threads": threads, "comments_read": _state["comments_read"], "last_poll_at": _iso(_state["last_poll"]),
                "last_ok_at": _iso(_state["last_ok"]), "error": _state["error"],
                "history_minutes": round((now - since) / 60) if since else 0,
                "live_chat_note": ("WSB Live Chat runs on Reddit's chat service, which has no public API. "
                                   "Paste a chat export under Buzz → Live chat to include it for 30 minutes.")}


def snapshot(now: float | None = None, *, limit: int = 15) -> dict[str, Any]:
    now = time.time() if now is None else now
    stats = ticker_stats(now)
    return dict(status(now), ok=True, top=stats[:limit], stats_note=(
        "Mentions count comments naming a ticker; one comment counts once per ticker. Trend (x the hour "
        "before) appears after two hours of continuous reading. Lean comes from plain keywords and is rough."))


def _iso(ts: Any) -> str | None:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat() if ts else None


# --- persistence and scheduling ----------------------------------------------------------

def _path() -> Path | None:
    return (_desk.DATA_DIR / "wsb_monitor.json") if _desk is not None else None


def _save() -> None:
    path = _path()
    if not path:
        return
    with _lock:
        data = {"mentions": list(_state["mentions"])[-MAX_MENTIONS:], "threads": _state["threads"],
                "last_ok": _state["last_ok"], "coverage_since": _state["coverage_since"],
                "last_discovery": _state["last_discovery"], "comments_read": _state["comments_read"]}
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _load() -> None:
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
    path = _path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None
    except (OSError, ValueError):
        data = None  # optional cache
    if not isinstance(data, dict):
        return
    now = time.time()
    mentions = [tuple(m) for m in data.get("mentions") or [] if isinstance(m, list) and len(m) == 5
                and isinstance(m[0], (int, float)) and now - m[0] <= WINDOW_SEC]
    with _lock:
        _state["mentions"] = deque(mentions)
        _state["threads"] = data.get("threads") if isinstance(data.get("threads"), dict) else {}
        last_ok = float(data.get("last_ok") or 0)
        _state["last_ok"] = last_ok
        # History only continues if the desk was reading until recently.
        _state["coverage_since"] = data.get("coverage_since") if now - last_ok <= FRESH_SEC else None
        _state["last_discovery"] = float(data.get("last_discovery") or 0)
        _state["comments_read"] = int(data.get("comments_read") or 0)


def poll_interval(now: float | None = None) -> int:
    moment = datetime.fromtimestamp(time.time() if now is None else now, ET)
    if moment.weekday() >= 5:
        return 900
    minutes = moment.hour * 60 + moment.minute
    return 120 if 8 * 60 <= minutes <= 17 * 60 else 600


def register(desk) -> None:
    global _desk
    _desk = desk
    if (os.environ.get("TOMAHAWK_NO_BG") or "").strip().lower() in ("1", "true", "yes", "on"):
        return

    def loop() -> None:
        while True:
            try:
                if configured():
                    poll()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(poll_interval())

    threading.Thread(target=loop, name="wsb-monitor", daemon=True).start()
