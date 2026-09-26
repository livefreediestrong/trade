"""Read-only X (Twitter) stock watcher for research context.

Polls X API v2 recent search for the watchlist's cashtags and, optionally, a
few accounts the owner chose (X_WATCH_ACCOUNTS). Posts are display-only
attention signals: they never create, size or approve a trade.

Needs X_BEARER_TOKEN from an X developer app whose plan includes recent
search. X bills or caps posts read, so every poll is budgeted:
  - X_MONTHLY_POST_CAP (default 3000) is spread evenly over the month's days
    (X_DAILY_POST_CAP overrides), so one busy session cannot spend the month.
  - since_id per query fetches only posts newer than the last poll.
  - X_POLL_MINUTES (default 15, 5..240) between polls; 429/401/403 back off.
Without a token nothing is requested and the status says how to enable it.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

import social_intelligence as _social

API_URL = (os.environ.get("X_API_BASE") or "https://api.x.com").rstrip("/") + "/2/tweets/search/recent"
_HANDLE_RE = re.compile(r"[A-Za-z0-9_]{1,15}")
_CASHTAG_SYMBOL_RE = re.compile(r"[A-Z]{1,6}")
MAX_CASHTAGS_PER_POST = 5  # posts tagging more tickers than this are almost always spam
MAX_QUERIES_PER_POLL = 3
MAX_ROWS = 300
MAX_AGE_SEC = 48 * 3600

_lock = threading.RLock()
_poll_lock = threading.Lock()
_state: dict[str, Any] = {
    "rows": {},            # post id -> social row
    "since": {},           # query key -> newest_id
    "last_poll_at": 0.0,
    "backoff_until": 0.0,
    "error": None,
    "cashtag_operator": True,
    "spam_dropped": 0,
    "cursor": 0,           # rotates query chunks when a watchlist needs more than one poll
}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(float(os.environ.get(name, default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def token() -> str:
    return (os.environ.get("X_BEARER_TOKEN") or "").strip()


def configured() -> bool:
    return bool(token())


def watched_accounts() -> list[str]:
    raw = os.environ.get("X_WATCH_ACCOUNTS") or ""
    out: list[str] = []
    for handle in raw.replace(";", ",").split(","):
        handle = handle.strip().lstrip("@")
        if _HANDLE_RE.fullmatch(handle) and handle.lower() not in {h.lower() for h in out}:
            out.append(handle)
    return out[:20]


def _poll_seconds() -> int:
    return _env_int("X_POLL_MINUTES", 15, 5, 240) * 60


def _max_results() -> int:
    return _env_int("X_MAX_RESULTS", 10, 10, 100)  # the API's own range is 10..100


def _data_dir() -> Path:
    root = os.environ.get("TOMAHAWK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return Path(root)


def _usage_path() -> Path:
    return _data_dir() / "x_watch_usage.json"


def _caps(now: datetime) -> tuple[int, int]:
    monthly = _env_int("X_MONTHLY_POST_CAP", 3000, 0, 10_000_000)
    days = calendar.monthrange(now.year, now.month)[1]
    daily_default = math.ceil(monthly / days) if monthly else 0
    daily = _env_int("X_DAILY_POST_CAP", daily_default, 0, 10_000_000)
    return monthly, daily


def usage(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    month, day = now.strftime("%Y-%m"), now.strftime("%Y-%m-%d")
    try:
        data = json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict) or data.get("month") != month:
        data = {"month": month, "posts": 0, "day": day, "day_posts": 0}
    if data.get("day") != day:
        data.update(day=day, day_posts=0)
    monthly, daily = _caps(now)
    posts, day_posts = int(data.get("posts") or 0), int(data.get("day_posts") or 0)
    remaining = min(max(0, monthly - posts), max(0, daily - day_posts))
    return dict(data, monthly_cap=monthly, daily_cap=daily, remaining_today=remaining)


def _record_usage(count: int, now: datetime) -> None:
    data = usage(now)
    data["posts"] = int(data.get("posts") or 0) + count
    data["day_posts"] = int(data.get("day_posts") or 0) + count
    path = _usage_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        keep = {k: data[k] for k in ("month", "posts", "day", "day_posts")}
        tmp.write_text(json.dumps(keep), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def build_queries(symbols: list[str], accounts: list[str], *, cashtags: bool = True,
                  max_chars: int | None = None) -> list[tuple[str, str]]:
    """(key, query) pairs, each within X's query length limit.

    Cashtags need an API tier with the $ operator; without it the watcher
    falls back to plain ticker keywords plus market context words, skipping
    tickers that are ordinary words.
    """
    max_chars = max_chars or _env_int("X_QUERY_MAX_CHARS", 512, 64, 4096)
    suffix = " -is:retweet lang:en"
    clean = [s for s in dict.fromkeys(str(x).upper().lstrip("$") for x in symbols) if _CASHTAG_SYMBOL_RE.fullmatch(s)]
    if cashtags:
        terms, tail = [f"${s}" for s in clean], suffix
    else:
        from news_stream import AMBIGUOUS_TICKERS
        terms = [s for s in clean if len(s) > 2 and s not in AMBIGUOUS_TICKERS]
        tail = " (stock OR shares OR earnings OR calls OR puts)" + suffix
    queries = [(f"{'cashtag' if cashtags else 'keyword'}:{key}", q) for key, q in _chunk(terms, tail, max_chars)]
    handles = [h for h in dict.fromkeys(str(a).strip().lstrip("@") for a in accounts) if _HANDLE_RE.fullmatch(h)]
    queries += [(f"accounts:{key}", q) for key, q in _chunk([f"from:{h}" for h in handles], " -is:retweet", max_chars)]
    return queries


def _chunk(terms: list[str], tail: str, max_chars: int) -> list[tuple[str, str]]:
    out, group = [], []
    for term in terms:
        candidate = group + [term]
        if group and len("(" + " OR ".join(candidate) + ")" + tail) > max_chars:
            out.append(group)
            group = [term]
        else:
            group = candidate
    if group:
        out.append(group)
    return [(",".join(g), "(" + " OR ".join(g) + ")" + tail) for g in out]


def _cashtags(post: dict[str, Any]) -> list[str]:
    return [str(t.get("tag") or "").upper() for t in ((post.get("entities") or {}).get("cashtags") or [])
            if isinstance(t, dict) and t.get("tag")]


def is_spam(post: dict[str, Any]) -> bool:
    """Posts tagging many tickers are overwhelmingly pump/spam lists."""
    return len(set(_cashtags(post))) > MAX_CASHTAGS_PER_POST


def _row(post: dict[str, Any], users: dict[str, dict], kind: str) -> dict[str, Any] | None:
    text = _social._clean_text(post.get("text"), 700)
    post_id = str(post.get("id") or "")
    if not text or not post_id.isdigit():
        return None
    tags = _cashtags(post)
    user = users.get(str(post.get("author_id") or "")) or {}
    username = str(user.get("username") or "")
    metrics = post.get("public_metrics") or {}

    def metric(name: str) -> int:
        try:
            return max(0, int(metrics.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    tickers = list(dict.fromkeys(t for t in tags if t)) or _social._tickers(text)
    return {
        "source": "x",
        "kind": kind,
        "source_id": post_id,
        "thread_id": str(post.get("conversation_id") or post_id),
        "author_hash": hashlib.sha256(str(post.get("author_id") or "[unknown]").encode("utf-8")).hexdigest()[:16],
        "account": username if kind == "account" else None,
        "created_at": post.get("created_at"),
        "title": f"@{username} on X" if kind == "account" and username else "X post",
        "excerpt": text[:300],
        "ticker_candidates": tickers[:12],
        "sentiment": _social._sentiment(text),
        "score": metric("like_count") + metric("retweet_count") + metric("quote_count"),
        "comments": metric("reply_count"),
        "permalink": f"https://x.com/{username}/status/{post_id}" if username else f"https://x.com/i/web/status/{post_id}",
    }


def _request(query: str, since_id: str | None, max_results: int) -> requests.Response:
    params = {
        "query": query,
        "max_results": max_results,
        "tweet.fields": "created_at,public_metrics,author_id,conversation_id,entities,lang",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    if since_id:
        params["since_id"] = since_id
    return requests.get(API_URL, params=params, timeout=10,
                        headers={"Authorization": f"Bearer {token()}", "User-Agent": _social._UA})


def _error_text(response: requests.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    detail = body.get("detail") or body.get("title") if isinstance(body, dict) else None
    errors = body.get("errors") if isinstance(body, dict) else None
    if not detail and isinstance(errors, list) and errors:
        detail = errors[0].get("message") if isinstance(errors[0], dict) else None
    return f"HTTP {response.status_code}: {str(detail or '')[:160]}".rstrip(": ")


def poll(symbols: list[str], *, force: bool = False, now: float | None = None) -> dict[str, Any]:
    """Fetch new posts when due and within budget; always returns status()."""
    if not configured():
        return status()
    now = time.time() if now is None else now
    with _lock:
        due = force or now - _state["last_poll_at"] >= _poll_seconds()
        if not due or now < _state["backoff_until"]:
            return status()
    if not _poll_lock.acquire(blocking=False):
        return status()  # another caller is already polling
    try:
        _poll(symbols, now)
    finally:
        _poll_lock.release()
    return status()


def _poll(symbols: list[str], now: float) -> None:
    today = datetime.fromtimestamp(now, timezone.utc)
    with _lock:
        _state["last_poll_at"] = now
        cashtags = _state["cashtag_operator"]
    queries = build_queries(symbols, watched_accounts(), cashtags=cashtags)
    if len(queries) > MAX_QUERIES_PER_POLL:
        # Rotate so every chunk of a long watchlist is searched across polls.
        with _lock:
            start = _state["cursor"] % len(queries)
            _state["cursor"] = start + MAX_QUERIES_PER_POLL
        queries = (queries[start:] + queries[:start])[:MAX_QUERIES_PER_POLL]
    error = None
    for key, query in queries:
        budget = usage(today)["remaining_today"]
        if budget < 10:  # the API cannot return fewer than 10 per request
            error = "X post budget for today is used up; polling resumes tomorrow"
            break
        with _lock:
            since_id = _state["since"].get(key)
        try:
            response = _request(query, since_id, min(_max_results(), budget))
        except requests.RequestException as exc:
            error = f"X unavailable: {type(exc).__name__}"
            _backoff(now + 300)
            break
        if response.status_code == 400 and since_id:
            # since_id older than the 7-day search window is rejected; start fresh once.
            with _lock:
                _state["since"].pop(key, None)
            response = _request(query, None, min(_max_results(), budget))
        # X reports a plan without the $ operator as "Reference to invalid operator ... not
        # available in current product"; accept either status it has used for that.
        if (response.status_code in (400, 403) and key.startswith("cashtag:")
                and "invalid operator" in _error_text(response).lower()):
            with _lock:
                _state["cashtag_operator"] = False
            error = "This X API plan does not allow the $cashtag operator; using ticker keywords from the next poll"
            continue
        if response.status_code == 429:
            try:
                reset = float(response.headers.get("x-rate-limit-reset") or 0)
            except (TypeError, ValueError):
                reset = 0.0
            _backoff(max(reset, now + 60))
            error = "X rate limit reached; waiting for the reset"
            break
        if response.status_code == 401:
            _backoff(now + 3600)
            error = "X rejected X_BEARER_TOKEN (401); check the token"
            break
        if response.status_code == 403:
            _backoff(now + 6 * 3600)
            error = "X API access denied (403); the plan may not include recent search. " + _error_text(response)
            break
        if response.status_code != 200:
            _backoff(now + 300)
            error = "X search failed: " + _error_text(response)
            break
        try:
            body = response.json()
        except ValueError:
            error = "X returned an unreadable response"
            continue
        _ingest(key, body, today)
    with _lock:
        _state["error"] = error


def _backoff(until: float) -> None:
    with _lock:
        _state["backoff_until"] = max(_state["backoff_until"], until)


def _ingest(key: str, body: Any, today: datetime) -> None:
    if not isinstance(body, dict):
        return
    posts = body.get("data") if isinstance(body.get("data"), list) else []
    users = {str(u.get("id")): u for u in ((body.get("includes") or {}).get("users") or []) if isinstance(u, dict)}
    meta = body.get("meta") if isinstance(body.get("meta"), dict) else {}
    kind = "account" if key.startswith("accounts:") else "cashtag"
    _record_usage(len(posts), today)
    rows, spam = [], 0
    for post in posts:
        if not isinstance(post, dict):
            continue
        if is_spam(post):
            spam += 1
            continue
        row = _row(post, users, kind)
        if row:
            rows.append(row)
    cutoff = today.timestamp() - MAX_AGE_SEC
    with _lock:
        if meta.get("newest_id"):
            _state["since"][key] = str(meta["newest_id"])
        _state["spam_dropped"] += spam
        stored = _state["rows"]
        for row in rows:
            stored[row["source_id"]] = row
        keep = sorted(stored.values(), key=lambda r: int(r["source_id"]), reverse=True)
        keep = [r for r in keep if (_social._ts_seconds(r.get("created_at")) or 0) >= cutoff][:MAX_ROWS]
        _state["rows"] = {r["source_id"]: r for r in keep}


def cached_rows() -> list[dict[str, Any]]:
    with _lock:
        return [dict(r) for r in sorted(_state["rows"].values(), key=lambda r: int(r["source_id"]), reverse=True)]


def social_rows(symbols: list[str]) -> list[dict[str, Any]]:
    """Poll when due, then return cached posts in the social row schema."""
    if not configured():
        return []
    poll(symbols)
    return cached_rows()


def status() -> dict[str, Any]:
    info = usage()
    with _lock:
        state = dict(_state)
        rows = len(_state["rows"])
    now = time.time()
    if not configured():
        error = "X is off: set X_BEARER_TOKEN (X developer app with recent search) to turn it on."
    else:
        error = state["error"]
    return {
        "configured": configured(),
        "display_only": True,
        "accounts": watched_accounts(),
        "poll_minutes": _poll_seconds() // 60,
        "max_results": _max_results(),
        "query_mode": "cashtag" if state["cashtag_operator"] else "keyword",
        "last_poll_at": datetime.fromtimestamp(state["last_poll_at"], timezone.utc).isoformat() if state["last_poll_at"] else None,
        "backoff_until": (datetime.fromtimestamp(state["backoff_until"], timezone.utc).isoformat()
                          if state["backoff_until"] > now else None),
        "error": error,
        "rows": rows,
        "spam_dropped": state["spam_dropped"],
        "usage": {k: info[k] for k in ("month", "posts", "monthly_cap", "day_posts", "daily_cap", "remaining_today")},
    }
