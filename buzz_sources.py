"""
Tomahawk ticker buzz — Reddit (+ optional Stocktwits) mention aggregator.

Paper-research only. Uses Reddit OAuth when REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET
are set (oauth.reddit.com); otherwise falls back to public www *.json (often 403).
No login-wall / Matrix session scraping. Fail soft on blocks/timeouts.
Caches in memory + data/buzz_cache.json for CACHE_TTL_SEC to avoid rate limits.

---------------------------------------------------------------------------
WSB Daily Discussion "Live Chat" research (2026-09):
---------------------------------------------------------------------------
WSB Daily Discussion shows a Live Chat tab (#daily-thread) separate from the
classic comment tree. Messages (tickers like BWET, GOOG, NFLX, etc.) appear
there in real time.

What we probed / found:
- Public `.json` listings/comments remain the only auth-free path for posts
  and classic comment trees (often still blocked/403 from datacenter IPs).
- Reddit Chat is Matrix-backed at ``matrix.redditspace.com``.
  ``GET /_matrix/client/versions`` returns 200 (protocol handshake only).
  Room history / sync / publicRooms require a Reddit-session Matrix access
  token (browser cookie / undocumented login). There is **no** documented
  anonymous public API for post-linked community chat messages.
- Official Reddit API / PRAW do not expose Chat. Scraping login walls or
  spoofing Matrix auth is out of scope (fail soft; no aggressive bypass).

Therefore:
- Auto-poll of Live Chat is **not available** without Reddit login.
- We deepen classic megathread comment scans (higher limit + depth).
- Source tag ``wsb_live_chat`` is reserved; weight ≈ daily discussion.
- Optional Advanced paste: operator can paste Live Chat export text; we
  parse tickers and merge under ``wsb_live_chat`` (manual bridge; Live Chat
  remains Matrix-only even when listing OAuth works).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

try:
    from paper_loop import CURATED_LIQUID_US
except Exception:  # pragma: no cover
    CURATED_LIQUID_US = []

# Match GEMINI / Finnhub: load project .env via data_sources (setdefault).
try:
    import data_sources as _ds

    _ds._load_env()
except Exception:  # pragma: no cover
    def _fallback_load_env() -> None:
        for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
            if not env_path.is_file():
                continue
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            break

    _fallback_load_env()

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
CACHE_PATH = DATA_DIR / "buzz_cache.json"

USER_AGENT = "TomahawkDesk/1.0 (local research; contact: local)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}
HTTP_TIMEOUT = 10  # seconds (8–12 band)
CACHE_TTL_SEC = 7 * 60  # ~7 minutes mid of 5–10
PASTE_TTL_SEC = 30 * 60  # Live Chat paste expires after 30m
REDDIT_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
REDDIT_OAUTH_BASE = "https://oauth.reddit.com"
OAUTH_MISSING_MSG = (
    "reddit_oauth_missing: set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET"
)

DEFAULT_SUBREDDITS = [
    "wallstreetbets",
    "stocks",
    "options",
    "daytrading",
    "investing",
]

# Common false-positive uppercase tokens (not tickers).
TICKER_DENYLIST = {
    "I", "A", "THE", "AND", "FOR", "CEO", "IPO", "FDA", "EPS", "USD", "ATH", "ATL",
    "IMO", "TIL", "EDIT", "ELI5", "AMA", "DD", "YOLO", "FOMO", "HODL",
    "WSB", "OTM", "ITM", "ATM", "ETF", "ETFS", "CEO", "CFO", "CTO",
    "USA", "US", "UK", "EU", "NYSE", "SEC", "FED", "FOMC", "GDP", "CPI",
    "PPE", "NFT", "AI", "EV", "API", "APP", "BUY", "SELL", "HOLD", "CALL", "PUT",
    "CALLS", "PUTS", "LONG", "SHORT", "BEAR", "BULL", "RH", "TD", "IV",
    "HV", "PE", "PB", "ROI", "ROE", "EBIT", "EBITDA", "GAAP", "LOL", "IMO",
    "BTW", "FYI", "PSA", "TLDR", "OP", "MOD", "BOT", "HTTP", "HTTPS", "WWW",
    "PDF", "CSV", "JSON", "HTML", "CSS", "JS", "CEO", "COO", "SVP", "EVP",
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
    "NOV", "DEC", "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",
    "AM", "PM", "EST", "EDT", "PST", "PDT", "CST", "CDT", "UTC", "GMT",
    "ALL", "ANY", "NOT", "BUT", "ARE", "WAS", "WERE", "HAS", "HAVE", "HAD",
    "THIS", "THAT", "WITH", "FROM", "INTO", "OVER", "UNDER", "ABOUT",
    "JUST", "LIKE", "WHAT", "WHEN", "WHERE", "WHICH", "WHO", "WHY", "HOW",
    "NEXT", "LAST", "WEEK", "TODAY", "TOMORROW", "YESTERDAY", "NOW",
    "OPEN", "CLOSE", "HIGH", "LOW", "VOLUME", "PRICE", "STOCK", "STOCKS",
    "MARKET", "MARKETS", "TRADE", "TRADES", "TRADING", "OPTION", "OPTIONS",
    "FUTURE", "FUTURES", "CRYPTO", "BITCOIN", "MOON", "TENDIES", "GAIN",
    "GAINS", "LOSS", "LOSSES", "RED", "GREEN", "PUMP", "DUMP", "SQUEEZE",
    "RISK", "CASH", "MONEY", "BANK", "BANKS", "NEWS", "LIVE", "FREE",
    "BEST", "GOOD", "BAD", "BIG", "SMALL", "NEW", "OLD", "TOP", "HOT",
    "REAL", "FAKE", "TRUE", "FALSE", "YES", "NO", "OK", "OKAY", "PLEASE",
    "THANKS", "THANK", "SORRY", "HELP", "NEED", "WANT", "THINK", "KNOW",
    "SEE", "LOOK", "GET", "GOT", "GOING", "GONE", "COME", "BACK", "OUT",
    "UP", "DOWN", "IN", "ON", "OFF", "AT", "BY", "TO", "OF", "OR", "IF",
    "SO", "AS", "BE", "IS", "IT", "MY", "ME", "WE", "YOU", "THEY", "HE",
    "SHE", "HIS", "HER", "OUR", "YOUR", "THEIR", "ITS", "DO", "DID", "DOES",
    "WILL", "WOULD", "COULD", "SHOULD", "CAN", "MAY", "MIGHT", "MUST",
    "PT", "PTS", "RTH", "AH", "PMCC", "LEAP", "LEAPS", "CSP", "PCS", "CC", "CCS", "DTE", "IVR",
    "RS", "RSI", "MACD", "SMA", "EMA", "VWAP", "OHLC", "BID", "ASK",
    "ERN", "Q1", "Q2", "Q3", "Q4", "YOY", "QOQ", "MOM", "TA", "FA",
    "IRS", "HSA", "IRA", "ROTH", "TSP", "401K", "TAX", "TAXES",
    "WAYMT", "DDT", "EOD", "BOD", "AH", "PRE", "POST",
}

# Title patterns → source tag. Order matters for first match.
_MEGATHREAD_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "wsb_waymt",
        re.compile(
            r"what\s+are\s+your\s+moves\s+(tomorrow|today)|"
            r"\bWAYMT\b|"
            r"what\s+are\s+your\s+moves\b",
            re.I,
        ),
    ),
    (
        "wsb_weekend",
        re.compile(
            r"weekend\s+discussion|"
            r"\bfutures\s+trading\b.*discussion|"
            r"discussion.*\bfutures\b",
            re.I,
        ),
    ),
    # wsb_daily BEFORE wsb_post_day so "Daily Discussion Thread for …" is daily
    (
        "wsb_daily",
        re.compile(
            r"daily\s+discussion(\s+thread)?\b",
            re.I,
        ),
    ),
    (
        "wsb_post_day",
        re.compile(
            r"post[\s\-]?trading\s+day|"
            r"after[\s\-]?hours|"
            r"uncoordinated",
            re.I,
        ),
    ),
]

# Weight multipliers by source tag (applied as mention increments).
SOURCE_WEIGHT: dict[str, float] = {
    "reddit_hot": 1.0,
    "stocktwits": 1.0,
    "wsb_daily": 1.35,
    "wsb_waymt": 1.35,
    "wsb_post_day": 1.15,
    "wsb_weekend": 1.15,
    "wsb_live_chat": 1.35,
}

_DOLLAR_TICKER = re.compile(r"\$([A-Z]{1,5})\b")
_STANDALONE_TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,5})(?![A-Za-z0-9])")

_ET = ZoneInfo("America/New_York")

_cache_lock = threading.RLock()
_mem_cache: dict[str, Any] | None = None
_mem_cached_at: float = 0.0
_mem_cache_key: str | None = None
_refresh_inflight = False
_refresh_lock = threading.Lock()

# Reddit OAuth token cache (thread-safe; same RLock style as buzz cache).
_token_lock = threading.RLock()
_oauth_token: str | None = None
_oauth_expires_at: float = 0.0
_oauth_mode: str | None = None  # oauth_client_credentials | oauth_password
_oauth_last_error: str | None = None
_reddit_degraded_reason: str | None = None



def _reddit_env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _reddit_client_creds() -> tuple[str, str]:
    return _reddit_env("REDDIT_CLIENT_ID"), _reddit_env("REDDIT_CLIENT_SECRET")


def _reddit_oauth_configured() -> bool:
    cid, secret = _reddit_client_creds()
    return bool(cid and secret)


def _reddit_user_agent() -> str:
    override = _reddit_env("REDDIT_USER_AGENT")
    if override:
        return override
    username = _reddit_env("REDDIT_USERNAME")
    if username:
        # strip leading u/ if operator pasted it
        if username.lower().startswith("u/"):
            username = username[2:]
        return f"TomahawkDesk/1.0 by u/{username}"
    return USER_AGENT


def _public_headers() -> dict[str, str]:
    return {
        "User-Agent": _reddit_user_agent(),
        "Accept": "application/json",
    }


def _request_reddit_token() -> tuple[str | None, str | None, float, str | None]:
    """Prefer client_credentials; fall back to password grant.

    Returns (access_token, mode, expires_in_sec, error).
    """
    client_id, client_secret = _reddit_client_creds()
    if not client_id or not client_secret:
        return None, None, 0.0, OAUTH_MISSING_MSG

    ua = _reddit_user_agent()
    headers = {"User-Agent": ua}
    auth = (client_id, client_secret)
    last_err: str | None = None

    try:
        r = requests.post(
            REDDIT_TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=auth,
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code == 200:
            body = r.json() if r.content else {}
            token = (body.get("access_token") or "").strip()
            expires = float(body.get("expires_in") or 3600)
            if token:
                return token, "oauth_client_credentials", expires, None
            last_err = "client_credentials: empty access_token"
        else:
            last_err = f"client_credentials HTTP {r.status_code}"
    except requests.Timeout:
        last_err = "client_credentials timeout"
    except Exception as e:  # noqa: BLE001
        last_err = f"client_credentials {type(e).__name__}: {e}"

    username = _reddit_env("REDDIT_USERNAME")
    password = _reddit_env("REDDIT_PASSWORD")
    if username.lower().startswith("u/"):
        username = username[2:]
    if username and password:
        try:
            r = requests.post(
                REDDIT_TOKEN_URL,
                data={
                    "grant_type": "password",
                    "username": username,
                    "password": password,
                },
                auth=auth,
                headers=headers,
                timeout=HTTP_TIMEOUT,
            )
            if r.status_code == 200:
                body = r.json() if r.content else {}
                token = (body.get("access_token") or "").strip()
                expires = float(body.get("expires_in") or 3600)
                if token:
                    return token, "oauth_password", expires, None
                last_err = "password_grant: empty access_token"
            else:
                last_err = f"password_grant HTTP {r.status_code}"
        except requests.Timeout:
            last_err = "password_grant timeout"
        except Exception as e:  # noqa: BLE001
            last_err = f"password_grant {type(e).__name__}: {e}"

    return None, None, 0.0, last_err or "reddit_oauth_failed"


def _ensure_reddit_token(*, force: bool = False) -> tuple[str | None, str | None]:
    """Return (token, mode). Caches with expires_in - 60s skew. Thread-safe."""
    global _oauth_token, _oauth_expires_at, _oauth_mode, _oauth_last_error, _reddit_degraded_reason
    with _token_lock:
        now = time.time()
        if (
            not force
            and _oauth_token
            and now < _oauth_expires_at
        ):
            return _oauth_token, _oauth_mode
        token, mode, expires_in, err = _request_reddit_token()
        _oauth_last_error = err
        if token and mode:
            _oauth_token = token
            _reddit_degraded_reason = None  # clear on token success
            _oauth_mode = mode
            _oauth_expires_at = time.time() + max(30.0, float(expires_in) - 60.0)
            return _oauth_token, _oauth_mode
        _oauth_token = None
        _oauth_mode = None
        _oauth_expires_at = 0.0
        return None, None


def reddit_auth_status() -> dict[str, Any]:
    """Public status for /api/buzz — no secrets."""
    configured = _reddit_oauth_configured()
    username_present = bool(_reddit_env("REDDIT_USERNAME"))
    with _token_lock:
        mode = _oauth_mode
        last_error = _oauth_last_error
        has_live = bool(_oauth_token) and time.time() < _oauth_expires_at
    if not configured:
        out_mode = "public_json"
        if not last_error:
            last_error = OAUTH_MISSING_MSG
    elif mode and has_live:
        out_mode = mode
    elif mode:
        out_mode = mode
    else:
        out_mode = "none"
    return {
        "configured": configured,
        "mode": out_mode,
        "username_present": username_present,
        "last_error": last_error,
    }


def _resolve_auth_mode() -> str:
    """auth_mode for buzz payload summary."""
    st = reddit_auth_status()
    return str(st.get("mode") or "none")


def _article_id_from_permalink(permalink: str | None, fallback_id: str | None = None) -> str | None:
    if fallback_id:
        fid = str(fallback_id).strip()
        if fid.startswith("t3_"):
            fid = fid[3:]
        if fid:
            return fid
    if not permalink:
        return None
    m = re.search(r"/comments/([A-Za-z0-9]+)", permalink)
    return m.group(1) if m else None


def _to_oauth_url(path_or_url: str) -> str:
    """Map www.reddit.com URL or path to oauth.reddit.com (no .json suffix)."""
    s = (path_or_url or "").strip()
    if s.startswith("https://oauth.reddit.com"):
        url = s
    elif s.startswith("https://www.reddit.com"):
        url = REDDIT_OAUTH_BASE + s[len("https://www.reddit.com") :]
    elif s.startswith("http://www.reddit.com"):
        url = REDDIT_OAUTH_BASE + s[len("http://www.reddit.com") :]
    elif s.startswith("/"):
        url = REDDIT_OAUTH_BASE + s
    else:
        url = REDDIT_OAUTH_BASE + "/" + s.lstrip("/")
    if url.endswith(".json"):
        url = url[:-5]
    # Drop query from path if any; params passed separately
    if "?" in url:
        url = url.split("?", 1)[0]
    return url.rstrip("/") or REDDIT_OAUTH_BASE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _et_today_yesterday() -> tuple[datetime, datetime]:
    now_et = datetime.now(_ET)
    today = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    return today, yesterday


def _post_created_et(created_utc: float | int | None) -> datetime | None:
    if created_utc is None:
        return None
    try:
        return datetime.fromtimestamp(float(created_utc), tz=timezone.utc).astimezone(_ET)
    except (TypeError, ValueError, OSError):
        return None


def _classify_megathread(title: str) -> str | None:
    t = (title or "").strip()
    if not t:
        return None
    for tag, pat in _MEGATHREAD_PATTERNS:
        if pat.search(t):
            return tag
    return None


def extract_tickers(
    text: str,
    *,
    watchlist: set[str] | None = None,
    allowlist: set[str] | None = None,
) -> set[str]:
    """Pull $TICKER and standalone 1–5 letter uppercase tokens.

    Always keep watchlist hits. Other tokens must pass denylist and, when
    allowlist is set (liquid focus), must be in allowlist OR watchlist.
    """
    if not text:
        return set()
    found: set[str] = set()
    wl = watchlist or set()
    allow = allowlist  # None = no liquid intersection

    for m in _DOLLAR_TICKER.finditer(text):
        found.add(m.group(1).upper())

    for m in _STANDALONE_TICKER.finditer(text):
        tok = m.group(1).upper()
        if tok in TICKER_DENYLIST:
            continue
        # Slang / noise: require len >= 2 (drop lone "I", "A" etc. unless watchlist)
        if len(tok) < 2 or len(tok) > 5:
            continue
        found.add(tok)

    out: set[str] = set()
    for t in found:
        if t in wl:
            out.add(t)
            continue
        if t in TICKER_DENYLIST:
            continue
        if allow is not None and t not in allow:
            continue
        out.add(t)
    return out


def _empty_agg() -> dict[str, Any]:
    return {"mentions": 0.0, "raw_mentions": 0, "subreddits": set(), "samples": [], "sources": set()}


def _ensure_ticker(agg: dict[str, dict], ticker: str) -> dict[str, Any]:
    if ticker not in agg:
        agg[ticker] = _empty_agg()
    return agg[ticker]


def _add_mention(
    agg: dict[str, dict],
    ticker: str,
    *,
    weight: float,
    subreddit: str | None,
    source: str,
    sample: dict[str, Any] | None = None,
) -> None:
    slot = _ensure_ticker(agg, ticker)
    slot["mentions"] += weight
    slot["raw_mentions"] += 1
    if subreddit:
        slot["subreddits"].add(subreddit)
    slot["sources"].add(source)
    if sample and len(slot["samples"]) < 5:
        # de-dupe by url
        urls = {s.get("url") for s in slot["samples"]}
        if sample.get("url") not in urls:
            slot["samples"].append(sample)


def _get_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> tuple[Any | None, str | None]:
    """Generic public GET (Stocktwits, Matrix probe, public Reddit JSON)."""
    try:
        r = requests.get(
            url,
            headers=headers or HEADERS,
            params=params,
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code != 200:
            return None, f"HTTP {r.status_code} for {url}"
        return r.json(), None
    except requests.Timeout:
        return None, f"timeout for {url}"
    except requests.RequestException as e:
        return None, f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def _reddit_get(path_or_url: str, *, params: dict | None = None) -> tuple[Any | None, str | None]:
    """GET Reddit listing/comments via OAuth when configured, else public www JSON."""
    global _reddit_degraded_reason
    if _reddit_oauth_configured():
        token, _mode = _ensure_reddit_token()
        oauth_err = None
        if token:
            url = _to_oauth_url(path_or_url)
            headers = {
                "Authorization": f"bearer {token}",
                "User-Agent": _reddit_user_agent(),
                "Accept": "application/json",
            }
            try:
                r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
                if r.status_code == 401:
                    token, _mode = _ensure_reddit_token(force=True)
                    if token:
                        headers["Authorization"] = f"bearer {token}"
                        r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
                    else:
                        with _token_lock:
                            oauth_err = _oauth_last_error or "reddit_oauth_401_refresh_failed"
                        r = None  # type: ignore
                if r is not None and r.status_code == 200:
                    _reddit_degraded_reason = None  # clear on successful OAuth fetch
                    return r.json(), None
                if r is not None:
                    oauth_err = f"HTTP {r.status_code} for {url}"
            except requests.Timeout:
                oauth_err = f"timeout for {url}"
            except requests.RequestException as e:
                oauth_err = f"{type(e).__name__}: {e}"
            except Exception as e:  # noqa: BLE001
                oauth_err = f"{type(e).__name__}: {e}"
        else:
            with _token_lock:
                oauth_err = _oauth_last_error or "reddit_oauth_failed"
        # OAuth fail → public JSON fallback (may 403; caller marks reddit_degraded)
        # Fall through to public path below; stash err on thread-local via module flag
        _reddit_degraded_reason = oauth_err

    # Public www *.json scraping: off by default. Reddit's Data API terms require
    # OAuth for programmatic access, and the public endpoint mostly 403s anyway.
    # Set REDDIT_CLIENT_ID/SECRET (preferred) or opt in with REDDIT_PUBLIC_JSON=1.
    if (os.environ.get("REDDIT_PUBLIC_JSON") or "").strip().lower() not in ("1", "true", "yes", "on"):
        return None, (
            "Reddit is off: add REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET to .env "
            "(free at reddit.com/prefs/apps) to turn it on."
        )

    # Public www *.json (often 403 from datacenter IPs).
    if path_or_url.startswith("http"):
        url = path_or_url
        if not url.endswith(".json") and "/comments/" not in url and ".json?" not in url:
            # listing paths already include .json from callers
            pass
    elif path_or_url.startswith("/"):
        url = "https://www.reddit.com" + path_or_url
        if not url.endswith(".json"):
            url = url.rstrip("/") + ".json"
    else:
        url = "https://www.reddit.com/" + path_or_url.lstrip("/")
        if not url.endswith(".json"):
            url = url.rstrip("/") + ".json"
    return _get_json(url, params=params, headers=_public_headers())


def _permalink_url(permalink: str | None, fallback_id: str | None = None) -> str:
    if permalink:
        if permalink.startswith("http"):
            return permalink
        return "https://www.reddit.com" + permalink
    if fallback_id:
        return f"https://www.reddit.com/{fallback_id}"
    return "https://www.reddit.com"


def fetch_subreddit_listing(
    subreddit: str,
    *,
    sort: str = "hot",
    limit: int = 50,
) -> tuple[list[dict], str | None]:
    if _reddit_oauth_configured():
        path = f"/r/{subreddit}/{sort}"
    else:
        path = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    data, err = _reddit_get(path, params={"limit": limit, "raw_json": 1})
    if err or not isinstance(data, dict):
        return [], err or "bad json"
    children = (data.get("data") or {}).get("children") or []
    posts = []
    for ch in children:
        if not isinstance(ch, dict):
            continue
        d = ch.get("data") or {}
        if d.get("stickied") and sort == "hot":
            # Still include stickies — megathreads are often stickied.
            pass
        posts.append(d)
    return posts, None


def fetch_post_comments(
    permalink: str,
    *,
    limit: int = 200,
    article_id: str | None = None,
) -> tuple[list[dict], str | None]:
    """Fetch comment bodies for a post. Fail soft."""
    aid = _article_id_from_permalink(permalink, article_id)
    if not aid and not permalink:
        return [], "no permalink"
    if _reddit_oauth_configured():
        if not aid:
            return [], "no article id"
        path = f"/comments/{aid}"
    else:
        if not permalink:
            return [], "no permalink"
        path = permalink if permalink.startswith("http") else "https://www.reddit.com" + permalink
        if not path.endswith(".json"):
            path = path.rstrip("/") + ".json"
    data, err = _reddit_get(
        path,
        params={"limit": limit, "raw_json": 1, "depth": 4, "sort": "confidence"},
    )
    if err or not isinstance(data, list) or len(data) < 2:
        return [], err or "bad comments json"
    comments_listing = data[1] if isinstance(data[1], dict) else {}
    children = (comments_listing.get("data") or {}).get("children") or []
    out: list[dict] = []

    def walk(nodes: list, depth: int = 0) -> None:
        for n in nodes:
            if not isinstance(n, dict):
                continue
            kind = n.get("kind")
            d = n.get("data") or {}
            if kind == "t1":
                body = d.get("body") or ""
                if body and body not in ("[deleted]", "[removed]"):
                    out.append(
                        {
                            "body": body,
                            "score": d.get("score") or 0,
                            "author": d.get("author"),
                            "id": d.get("id"),
                        }
                    )
                replies = d.get("replies")
                if isinstance(replies, dict) and depth < 4:
                    walk((replies.get("data") or {}).get("children") or [], depth + 1)
            elif kind == "more":
                continue

    walk(children)
    return out, None


def _prefer_megathreads(posts: list[dict]) -> list[tuple[str, dict]]:
    """Pick megathreads preferring today ET, then yesterday. One per source tag."""
    today, yesterday = _et_today_yesterday()
    tagged: list[tuple[str, dict, int]] = []  # tag, post, priority (0=today,1=yest,2=other)
    for p in posts:
        title = p.get("title") or ""
        tag = _classify_megathread(title)
        if not tag:
            continue
        created = _post_created_et(p.get("created_utc"))
        if created is None:
            pri = 2
        else:
            cd = created.replace(hour=0, minute=0, second=0, microsecond=0)
            if cd == today:
                pri = 0
            elif cd == yesterday:
                pri = 1
            else:
                pri = 2
        tagged.append((tag, p, pri))

    # Prefer best priority per tag
    best: dict[str, tuple[int, dict]] = {}
    for tag, p, pri in tagged:
        prev = best.get(tag)
        if prev is None or pri < prev[0]:
            best[tag] = (pri, p)
        elif pri == prev[0]:
            # Higher score wins tie
            if (p.get("score") or 0) > (prev[1].get("score") or 0):
                best[tag] = (pri, p)

    # Also: for wsb_post_day, if we only have yesterday and it's post-close, keep it
    return [(tag, pair[1]) for tag, pair in best.items()]


def _scan_text_for_agg(
    agg: dict[str, dict],
    text: str,
    *,
    watchlist: set[str],
    allowlist: set[str] | None,
    weight: float,
    subreddit: str,
    source: str,
    sample: dict[str, Any] | None,
) -> None:
    tickers = extract_tickers(text, watchlist=watchlist, allowlist=allowlist)
    for t in tickers:
        _add_mention(
            agg,
            t,
            weight=weight,
            subreddit=subreddit,
            source=source,
            sample=sample,
        )


def aggregate_reddit(
    *,
    subreddits: list[str] | None = None,
    watchlist: list[str] | None = None,
    focus_liquid: bool = False,
) -> tuple[dict[str, dict], list[dict], list[str], list[dict]]:
    """Fetch Reddit hot listings + WSB megathread comments.

    Returns (agg, megathreads_meta, errors, sources_ok).
    """
    subs = list(subreddits or DEFAULT_SUBREDDITS)
    wl = {t.strip().upper() for t in (watchlist or []) if t}
    allowlist: set[str] | None = None
    if focus_liquid:
        allowlist = {t.upper() for t in CURATED_LIQUID_US} | wl

    agg: dict[str, dict] = {}
    errors: list[str] = []
    sources_ok: list[dict] = []
    megathreads: list[dict] = []

    if not _reddit_oauth_configured():
        errors.append(OAUTH_MISSING_MSG)

    wsb_posts_hot: list[dict] = []
    wsb_posts_new: list[dict] = []

    for sub in subs:
        posts, err = fetch_subreddit_listing(sub, sort="hot", limit=50)
        if err:
            errors.append(f"r/{sub} hot: {err}")
        else:
            sources_ok.append({"source": f"reddit:r/{sub}/hot", "posts": len(posts)})
            if sub.lower() == "wallstreetbets":
                wsb_posts_hot = posts
            weight = SOURCE_WEIGHT["reddit_hot"]
            for p in posts:
                title = p.get("title") or ""
                selftext = p.get("selftext") or ""
                # Skip scanning megathread bodies here — handled via comments with tags
                if sub.lower() == "wallstreetbets" and _classify_megathread(title):
                    continue
                text = f"{title}\n{selftext}"
                url = _permalink_url(p.get("permalink"), p.get("id"))
                sample = {
                    "title": title[:160],
                    "url": url,
                    "subreddit": sub,
                    "score": p.get("score") or 0,
                    "source": "reddit_hot",
                }
                _scan_text_for_agg(
                    agg,
                    text,
                    watchlist=wl,
                    allowlist=allowlist,
                    weight=weight,
                    subreddit=sub,
                    source="reddit_hot",
                    sample=sample,
                )

    # WSB new listing for megathread discovery (today/yesterday)
    new_posts, new_err = fetch_subreddit_listing("wallstreetbets", sort="new", limit=50)
    if new_err:
        errors.append(f"r/wallstreetbets new: {new_err}")
    else:
        sources_ok.append({"source": "reddit:r/wallstreetbets/new", "posts": len(new_posts)})
        wsb_posts_new = new_posts

    combined = wsb_posts_hot + wsb_posts_new
    # de-dupe by id
    seen_ids: set[str] = set()
    uniq: list[dict] = []
    for p in combined:
        pid = str(p.get("id") or p.get("name") or "")
        if pid and pid in seen_ids:
            continue
        if pid:
            seen_ids.add(pid)
        uniq.append(p)

    selected = _prefer_megathreads(uniq)
    for tag, post in selected:
        title = post.get("title") or ""
        permalink = post.get("permalink") or ""
        url = _permalink_url(permalink, post.get("id"))
        weight = SOURCE_WEIGHT.get(tag, 1.2)
        comments, cerr = fetch_post_comments(
            permalink, limit=200, article_id=str(post.get("id") or "") or None
        )
        thread_tickers: dict[str, float] = {}
        if cerr:
            errors.append(f"{tag} comments: {cerr}")
            # Still try title
            for t in extract_tickers(title, watchlist=wl, allowlist=allowlist):
                thread_tickers[t] = thread_tickers.get(t, 0) + weight
                _add_mention(
                    agg,
                    t,
                    weight=weight,
                    subreddit="wallstreetbets",
                    source=tag,
                    sample={
                        "title": title[:160],
                        "url": url,
                        "subreddit": "wallstreetbets",
                        "score": post.get("score") or 0,
                        "source": tag,
                    },
                )
        else:
            sources_ok.append(
                {"source": f"reddit:{tag}", "comments": len(comments), "title": title[:80]}
            )
            # Title + each comment
            texts = [title] + [c.get("body") or "" for c in comments]
            for i, text in enumerate(texts):
                tickers = extract_tickers(text, watchlist=wl, allowlist=allowlist)
                sample = None
                if i == 0 or tickers:
                    sample = {
                        "title": (title if i == 0 else f"comment: {(text or '')[:80]}")[:160],
                        "url": url,
                        "subreddit": "wallstreetbets",
                        "score": (post.get("score") if i == 0 else (comments[i - 1].get("score") if i else 0)) or 0,
                        "source": tag,
                    }
                for t in tickers:
                    thread_tickers[t] = thread_tickers.get(t, 0) + weight
                    _add_mention(
                        agg,
                        t,
                        weight=weight,
                        subreddit="wallstreetbets",
                        source=tag,
                        sample=sample,
                    )

        top_in_thread = sorted(thread_tickers.items(), key=lambda x: -x[1])[:12]
        megathreads.append(
            {
                "tag": tag,
                "title": title,
                "url": url,
                "score": post.get("score") or 0,
                "created_utc": post.get("created_utc"),
                "comment_count_scanned": len(comments) if not cerr else 0,
                "top_tickers": [
                    {"ticker": t, "weighted_mentions": round(w, 2)} for t, w in top_in_thread
                ],
                "error": cerr,
            }
        )

    return agg, megathreads, errors, sources_ok


def fetch_stocktwits_trending() -> tuple[list[dict], str | None]:
    """Best-effort Stocktwits trending symbols — no API key.

    Public JSON; some datacenter IPs get 403/429. Soft-retry once with desk UA.
    Documented in DEPLOY_NOTES API pack / .env.example (no key required).
    """
    url = "https://api.stocktwits.com/api/2/trending/symbols.json"
    st_headers = {
        "User-Agent": (HEADERS.get("User-Agent") if isinstance(HEADERS, dict) else None)
        or "TomahawkDesk/1.0 (paper research; Stocktwits trending)",
        "Accept": "application/json",
    }
    data, err = _get_json(url, headers=st_headers)
    if err and ("403" in err or "429" in err or "timeout" in err.lower()):
        import time as _time
        _time.sleep(0.6)
        data, err = _get_json(url, headers=st_headers)
    if err:
        return [], err
    if not isinstance(data, dict):
        return [], "bad stocktwits json"
    # response_status may signal soft errors even with HTTP 200
    rs = data.get("response") if isinstance(data.get("response"), dict) else {}
    if rs.get("status") not in (None, 200) and not data.get("symbols"):
        return [], f"stocktwits status {rs.get('status')}"
    syms = data.get("symbols") or []
    out = []
    for s in syms:
        if not isinstance(s, dict):
            continue
        ticker = (s.get("symbol") or "").upper().strip()
        if not ticker or len(ticker) > 6:
            continue
        out.append(
            {
                "ticker": ticker,
                "title": s.get("title") or ticker,
                "watchlist_count": s.get("watchlist_count"),
            }
        )
    if not out and not err:
        return [], "stocktwits empty trending"
    return out, None


def _finalize_agg(agg: dict[str, dict]) -> list[dict]:
    rows = []
    for ticker, slot in agg.items():
        rows.append(
            {
                "ticker": ticker,
                "mentions": int(round(slot["mentions"])),
                "weighted_mentions": round(float(slot["mentions"]), 2),
                "raw_mentions": int(slot["raw_mentions"]),
                "subreddits": sorted(slot["subreddits"]),
                "sources": sorted(slot["sources"]),
                "samples": slot["samples"][:4],
            }
        )
    rows.sort(key=lambda r: (-r["weighted_mentions"], -r["raw_mentions"], r["ticker"]))
    return rows


def _load_disk_cache() -> dict[str, Any] | None:
    try:
        if not CACHE_PATH.is_file():
            return None
        raw = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return raw
    except Exception:
        return None


def _save_disk_cache(payload: dict[str, Any]) -> None:
    try:
        DATA_DIR.mkdir(exist_ok=True)
        CACHE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _cache_age_sec(payload: dict[str, Any] | None) -> float | None:
    if not payload:
        return None
    ts = payload.get("cached_at_epoch")
    if ts is None:
        return None
    try:
        return max(0.0, time.time() - float(ts))
    except (TypeError, ValueError):
        return None


def _build_payload(
    *,
    watchlist: list[str] | None,
    focus_liquid: bool,
) -> dict[str, Any]:
    wl = list(watchlist or [])
    agg, megathreads, errors, sources_ok = aggregate_reddit(
        watchlist=wl,
        focus_liquid=focus_liquid,
    )

    st_syms, st_err = fetch_stocktwits_trending()
    if st_err:
        errors.append(f"stocktwits: {st_err}")
    else:
        sources_ok.append({"source": "stocktwits_trending", "symbols": len(st_syms)})
        wl_set = {t.upper() for t in wl}
        allow = ({t.upper() for t in CURATED_LIQUID_US} | wl_set) if focus_liquid else None
        for s in st_syms:
            t = s["ticker"]
            if t in TICKER_DENYLIST:
                continue
            if allow is not None and t not in allow and t not in wl_set:
                continue
            _add_mention(
                agg,
                t,
                weight=SOURCE_WEIGHT["stocktwits"],
                subreddit=None,
                source="stocktwits",
                sample={
                    "title": f"Stocktwits trending: {s.get('title') or t}",
                    "url": f"https://stocktwits.com/symbol/{t}",
                    "subreddit": "stocktwits",
                    "score": s.get("watchlist_count") or 0,
                    "source": "stocktwits",
                },
            )

    wl_set = {t.upper() for t in wl}
    allow = ({t.upper() for t in CURATED_LIQUID_US} | wl_set) if focus_liquid else None
    live_chat = _merge_live_chat_paste(agg, watchlist=wl_set, allowlist=allow)

    tickers = _finalize_agg(agg)
    wl_upper = {t.upper() for t in wl}
    watchlist_hits = [r for r in tickers if r["ticker"] in wl_upper][:20]
    top = tickers[:40]

    epoch = time.time()
    auth_mode = _resolve_auth_mode()
    degraded = bool(_reddit_degraded_reason)
    # If OAuth configured but we fell back to public, mark degraded (demote fake WSB heat)
    if _reddit_oauth_configured() and auth_mode in ("public_json", "none"):
        degraded = True
        if auth_mode == "public_json":
            pass
        else:
            auth_mode = "public_json" if any("reddit" in (s or "") for s in sources_ok) else "none"
    if degraded:
        # Demote WSB-sourced mention scores so heat lane does not look authoritative
        for row in tickers:
            srcs = set(row.get("sources") or [])
            # Match WSB/reddit-tagged sources (ids vary: wsb_daily, reddit:r/..., wsb_live_chat)
            is_wsb = any(
                ("wsb" in str(s).lower())
                or ("reddit" in str(s).lower())
                or ("wallstreetbets" in str(s).lower())
                for s in srcs
            )
            if is_wsb:
                row["mentions"] = round(float(row.get("mentions") or 0) * 0.35, 2)
                if "weighted_mentions" in row:
                    row["weighted_mentions"] = round(float(row.get("weighted_mentions") or 0) * 0.35, 2)
                row["degraded"] = True
        tickers.sort(
            key=lambda r: (
                -float(r.get("weighted_mentions") or r.get("mentions") or 0),
                r.get("ticker") or "",
            )
        )
        top = tickers[:40]
        watchlist_hits = [r for r in tickers if r["ticker"] in wl_upper][:20]
        if _reddit_degraded_reason:
            errors = list(errors or []) + [f"reddit_degraded: {_reddit_degraded_reason}"]
    return {
        "ok": True,
        "cached_at": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
        "cached_at_epoch": epoch,
        "ttl_sec": CACHE_TTL_SEC,
        "stale": False,
        "tickers": top,
        "top": top[:15],
        "watchlist_hits": watchlist_hits,
        "megathreads": megathreads,
        "sources_ok": sources_ok,
        "errors": errors,
        "focus_liquid": focus_liquid,
        "watchlist_size": len(wl),
        "live_chat": live_chat,
        "auth_mode": auth_mode,
        "reddit_auth": reddit_auth_status(),
        "reddit_degraded": degraded,
    }




# ---------------------------------------------------------------------------
# Live Chat (wsb_live_chat) — paste bridge only until Reddit login exists
# ---------------------------------------------------------------------------
_live_chat_lock = threading.Lock()
_live_chat_paste: dict[str, Any] = {
    "text": "",
    "tickers": [],
    "parsed_at": None,
    "line_count": 0,
}
LIVE_CHAT_STATUS_NOTE = (
    "Live Chat needs Reddit login — connect later. "
    "Matrix at matrix.redditspace.com requires a session token; "
    "no public anonymous chat API. Paste a chat export below to bridge."
)


def probe_live_chat_availability() -> dict[str, Any]:
    """Best-effort probe: Matrix versions only. Never authenticates."""
    status: dict[str, Any] = {
        "available": False,
        "requires_reddit_login": True,
        "note": LIVE_CHAT_STATUS_NOTE,
        "matrix_versions_ok": False,
        "matrix_error": None,
    }
    try:
        r = requests.get(
            "https://matrix.redditspace.com/_matrix/client/versions",
            headers=HEADERS,
            timeout=min(8, HTTP_TIMEOUT),
        )
        if r.status_code == 200:
            status["matrix_versions_ok"] = True
            status["matrix_error"] = None
        else:
            status["matrix_error"] = f"HTTP {r.status_code}"
    except Exception as e:  # noqa: BLE001
        status["matrix_error"] = f"{type(e).__name__}: {e}"
    return status


def ingest_live_chat_paste(
    text: str,
    *,
    watchlist: list[str] | None = None,
    focus_liquid: bool = False,
) -> dict[str, Any]:
    """Parse pasted WSB Live Chat text into tickers tagged wsb_live_chat.

    Does not call Reddit. Stores paste in memory for merge on next buzz build.
    """
    global _live_chat_paste
    raw = (text or "").strip()
    wl = {t.strip().upper() for t in (watchlist or []) if t}
    allow = ({t.upper() for t in CURATED_LIQUID_US} | wl) if focus_liquid else None
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    counts: dict[str, int] = {}
    for ln in lines:
        for t in extract_tickers(ln, watchlist=wl, allowlist=allow):
            counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    rows = [{"ticker": t, "mentions": n, "weighted_mentions": round(n * SOURCE_WEIGHT.get("wsb_live_chat", 1.35), 2)} for t, n in ranked]
    parsed_at = _now_iso()
    with _live_chat_lock:
        _live_chat_paste = {
            "text": raw[:50000],
            "tickers": rows,
            "parsed_at": parsed_at,
            "line_count": len(lines),
        }
    return {
        "ok": True,
        "source": "wsb_live_chat",
        "tickers": rows[:40],
        "line_count": len(lines),
        "parsed_at": parsed_at,
        "note": LIVE_CHAT_STATUS_NOTE,
    }


def get_live_chat_paste() -> dict[str, Any]:
    with _live_chat_lock:
        return dict(_live_chat_paste)


def _merge_live_chat_paste(
    agg: dict[str, dict],
    *,
    watchlist: set[str],
    allowlist: set[str] | None,
) -> dict[str, Any]:
    """Merge stored paste mentions into agg. Returns live_chat section meta."""
    paste = get_live_chat_paste()
    live_status = probe_live_chat_availability()
    # Paste TTL — expire stale paste
    rows = paste.get("tickers") or []
    parsed_at = paste.get("parsed_at")
    expired = False
    if parsed_at:
        try:
            ts = datetime.fromisoformat(str(parsed_at).replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > PASTE_TTL_SEC:
                rows = []
                expired = True
        except Exception:
            pass
    weight = SOURCE_WEIGHT.get("wsb_live_chat", 1.35)
    thread_tickers: dict[str, float] = {}
    for row in rows:
        t = str(row.get("ticker") or "").upper()
        if not t:
            continue
        if t not in watchlist and allowlist is not None and t not in allowlist:
            continue
        n = int(row.get("mentions") or 1)
        w = weight * n
        thread_tickers[t] = thread_tickers.get(t, 0) + w
        _add_mention(
            agg,
            t,
            weight=w,
            subreddit="wallstreetbets",
            source="wsb_live_chat",
            sample={
                "title": f"WSB Live Chat paste ({n}×)",
                "url": "https://www.reddit.com/r/wallstreetbets/",
                "subreddit": "wallstreetbets",
                "score": n,
                "source": "wsb_live_chat",
            },
        )
    top = sorted(thread_tickers.items(), key=lambda x: -x[1])[:12]
    live_status.update(
        {
            "paste_tickers": [
                {"ticker": t, "weighted_mentions": round(w, 2)} for t, w in top
            ],
            "paste_line_count": paste.get("line_count") or 0,
            "paste_parsed_at": paste.get("parsed_at"),
            "has_paste": bool(rows),
        }
    )
    return live_status




def _buzz_cache_key(watchlist: list[str] | None, focus_liquid: bool) -> str:
    wl = ",".join(sorted({str(t).upper() for t in (watchlist or []) if t}))
    return f"{'liquid' if focus_liquid else 'all'}|{wl}"

def get_cached_buzz() -> dict[str, Any] | None:
    """Return in-memory or disk cache without network. May be stale."""
    global _mem_cache, _mem_cached_at
    with _cache_lock:
        if _mem_cache is not None:
            age = time.time() - _mem_cached_at
            out = dict(_mem_cache)
            out["cache_age_sec"] = round(age, 1)
            out["stale"] = age > CACHE_TTL_SEC
            return out
    disk = _load_disk_cache()
    if disk:
        age = _cache_age_sec(disk) or 99999
        with _cache_lock:
            _mem_cache = disk
            _mem_cached_at = float(disk.get("cached_at_epoch") or (time.time() - age))
        out = dict(disk)
        out["cache_age_sec"] = round(age, 1)
        out["stale"] = age > CACHE_TTL_SEC
        return out
    return None



def invalidate_buzz_cache() -> None:
    """Drop in-memory buzz cache (call on focus/watchlist change)."""
    global _mem_cache, _mem_cached_at, _mem_cache_key
    with _cache_lock:
        _mem_cache = None
        _mem_cached_at = 0.0
        _mem_cache_key = None

def fetch_ticker_buzz(
    watchlist: list[str] | None = None,
    force: bool = False,
    *,
    focus_liquid: bool = False,
) -> dict[str, Any]:
    """Main entry: return buzz dict, using cache unless force or expired.

    Cache is keyed by focus_liquid + watchlist; focus/watchlist changes invalidate.
    """
    global _mem_cache, _mem_cached_at, _mem_cache_key

    key = _buzz_cache_key(watchlist, focus_liquid)
    if not force:
        cached = get_cached_buzz()
        if (
            cached is not None
            and not cached.get("stale")
            and _mem_cache_key == key
            and cached.get("focus_liquid") == focus_liquid
        ):
            cached = dict(cached)
            cached["from_cache"] = True
            return cached

    payload = _build_payload(watchlist=watchlist, focus_liquid=focus_liquid)
    payload["cache_key"] = key
    with _cache_lock:
        _mem_cache = payload
        _mem_cached_at = float(payload["cached_at_epoch"])
        _mem_cache_key = key
    _save_disk_cache(payload)
    out = dict(payload)
    out["from_cache"] = False
    out["cache_age_sec"] = 0
    return out


def kick_background_refresh(
    watchlist: list[str] | None = None,
    *,
    focus_liquid: bool = False,
) -> bool:
    """Start one background refresh if none in flight. Returns True if started."""
    global _refresh_inflight
    with _refresh_lock:
        if _refresh_inflight:
            return False
        _refresh_inflight = True

    def worker() -> None:
        global _refresh_inflight
        try:
            fetch_ticker_buzz(watchlist, force=True, focus_liquid=focus_liquid)
        except Exception:
            pass
        finally:
            with _refresh_lock:
                _refresh_inflight = False

    threading.Thread(target=worker, name="buzz-refresh", daemon=True).start()
    return True



# Prior heat scores for spike delta (module process memory; resets on restart)
_prev_heat_scores: dict[str, float] = {}
# Absolute + relative thresholds so spikes mean a real jump, not every tick
_SPIKE_ABS_DELTA = 3.0
_SPIKE_REL_RATIO = 0.5  # +50% vs prior
_SPIKE_MIN_SCORE = 2.0


def build_heat_lane(
    cached: dict[str, Any] | None,
    watchlist: list[str] | None = None,
    *,
    focus_liquid: bool = True,
    cap: int = 12,
) -> list[dict[str, Any]]:
    """Buzz tickers that are liquid/watchlist-relevant, ranked by score. Cap ~12.

    Marks is_spike when mentions jump meaningfully vs prior snapshot (abs + rel).
    Spikes sort to the front; stale (no score change / low) fade via is_stale.
    """
    global _prev_heat_scores
    if not cached:
        return []
    wl = {str(t).upper() for t in (watchlist or []) if t}
    liquid = {t.upper() for t in CURATED_LIQUID_US}
    rows = list(cached.get("tickers") or cached.get("top") or [])
    heat: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        t = str(r.get("ticker") or "").upper().strip()
        if not t or t in seen:
            continue
        in_wl = t in wl
        is_liq = t in liquid
        if focus_liquid:
            if not (in_wl or is_liq):
                continue
        else:
            # Still prefer relevance: watchlist or liquid; skip obscure noise
            if not (in_wl or is_liq) and len(heat) >= max(4, cap // 2):
                continue
        score = float(r.get("weighted_mentions") or r.get("mentions") or r.get("score") or 0)
        mentions = int(r.get("raw_mentions") or r.get("mentions") or round(score) or 0)
        if score <= 0 and not in_wl:
            continue
        seen.add(t)
        prev = float(_prev_heat_scores.get(t) or 0)
        delta = score - prev
        is_spike = False
        if score >= _SPIKE_MIN_SCORE and prev > 0:
            is_spike = delta >= _SPIKE_ABS_DELTA or (delta > 0 and delta / max(prev, 0.01) >= _SPIKE_REL_RATIO)
        elif score >= _SPIKE_MIN_SCORE and prev <= 0 and score >= _SPIKE_ABS_DELTA:
            # New name appearing with meaningful volume
            is_spike = True
        sources = list(r.get("sources") or r.get("subreddits") or [])[:6]
        # Prefer a short human source label
        src_label = ""
        src_join = " ".join(str(s).lower() for s in sources)
        if "stocktwits" in src_join or "st" == src_join:
            src_label = "Stocktwits"
        elif "wsb" in src_join or "wallstreetbets" in src_join or "live_chat" in src_join:
            src_label = "WSB"
        elif sources:
            src_label = str(sources[0])[:12]
        else:
            src_label = "Reddit"
        heat.append(
            {
                "ticker": t,
                "score": round(score, 2),
                "mentions": mentions,
                "delta": round(delta, 2),
                "is_spike": is_spike,
                "is_stale": (not is_spike) and delta <= 0 and score < max(_SPIKE_MIN_SCORE, prev * 0.8),
                "sources": sources,
                "source_label": src_label,
                "in_watchlist": in_wl,
            }
        )
        if len(heat) >= cap * 2:  # gather extra then sort/cap
            break
    # Spikes first, then by score
    heat.sort(
        key=lambda x: (
            0 if x.get("is_spike") else 1,
            -float(x.get("score") or 0),
            x.get("ticker") or "",
        )
    )
    out = heat[:cap]
    # Update prev scores from this snapshot (full candidate set before cap)
    for h in heat:
        _prev_heat_scores[h["ticker"]] = float(h.get("score") or 0)
    # Cap memory
    if len(_prev_heat_scores) > 200:
        keep = {h["ticker"] for h in out}
        _prev_heat_scores = {k: v for k, v in _prev_heat_scores.items() if k in keep or v >= _SPIKE_MIN_SCORE}
    return out


def buzz_summary_for_state(
    watchlist: list[str] | None = None,
    *,
    focus_liquid: bool = False,
) -> dict[str, Any]:
    """Non-blocking summary for /api/state — cache only; kick refresh if cold."""
    cached = get_cached_buzz()
    if cached is None:
        kick_background_refresh(watchlist, focus_liquid=focus_liquid)
        return {
            "top": [],
            "watchlist_hits": [],
            "heat": [],
            "megathreads": [],
            "stale": True,
            "cached_at": None,
            "errors": ["cache_cold"],
            "sources_ok": [],
            "auth_mode": _resolve_auth_mode(),
            "reddit_auth": reddit_auth_status(),
            "live_chat": {
                "available": False,
                "requires_reddit_login": True,
                "note": LIVE_CHAT_STATUS_NOTE,
                "has_paste": False,
            },
        }
    if cached.get("stale"):
        kick_background_refresh(watchlist, focus_liquid=focus_liquid)
    wl = {t.upper() for t in (watchlist or [])}
    tickers = cached.get("tickers") or cached.get("top") or []
    # Recompute watchlist hits against current watchlist
    hits = [r for r in tickers if r.get("ticker") in wl][:15]
    heat = build_heat_lane(cached, watchlist, focus_liquid=focus_liquid, cap=12)
    return {
        "top": (cached.get("top") or tickers)[:15],
        "watchlist_hits": hits,
        "heat": heat,
        "megathreads": cached.get("megathreads") or [],
        "stale": bool(cached.get("stale")),
        "cached_at": cached.get("cached_at"),
        "cache_age_sec": cached.get("cache_age_sec"),
        "errors": cached.get("errors") or [],
        "sources_ok": cached.get("sources_ok") or [],
        "auth_mode": cached.get("auth_mode") or _resolve_auth_mode(),
        "reddit_auth": cached.get("reddit_auth") or reddit_auth_status(),
        "live_chat": cached.get("live_chat") or {
            "available": False,
            "requires_reddit_login": True,
            "note": LIVE_CHAT_STATUS_NOTE,
            "has_paste": False,
        },
    }


def buzz_mentions_for_ticker(ticker: str, buzz: dict[str, Any] | None = None) -> int | None:
    """Return mention count for a ticker from buzz payload, or None."""
    if not ticker:
        return None
    t = ticker.upper().strip()
    src = buzz or get_cached_buzz() or {}
    for row in src.get("tickers") or src.get("top") or []:
        if row.get("ticker") == t:
            return int(row.get("mentions") or row.get("raw_mentions") or 0)
    for row in src.get("watchlist_hits") or []:
        if row.get("ticker") == t:
            return int(row.get("mentions") or row.get("raw_mentions") or 0)
    return None
