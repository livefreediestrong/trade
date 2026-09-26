"""Scheduled market-moving events: the Fed, BLS releases, the President's public schedule
and events the owner adds.

Sources (all public, no key):
- Federal Reserve calendar JSON: FOMC statements, press conferences and minutes, speeches
  and testimony with times (https://www.federalreserve.gov/newsevents/calendar.htm).
- BLS release calendar (iCalendar): CPI, Employment Situation, PPI, JOLTS... at their
  Eastern release times (https://www.bls.gov/schedule/news_release/). BLS blocks readers
  whose User-Agent has no owner contact (https://www.bls.gov/bls/pss.htm), so the feed
  needs BLS_USER_AGENT with a contact email; without it the dated schedule is used.
- The President's public schedule as iCalendar. Default: Roll Call Factba.se's public
  Google Calendar of the White House schedule; set PRESIDENT_SCHEDULE_ICS to another feed,
  or "off". MARKET_EVENTS_ICS adds more feeds (comma-separated).
- Owner-added events (config "custom_market_events"), e.g. an announced address.

Owner decision: events never create trades. A high-impact event opens a short window in
which the automated broker agent opens no new positions (exits continue); everything else
is shown to the owner and to Changing Woman's reasoning notes. Missing feeds never block
trading; the static FOMC dates in macro_calendar still apply offline.
"""
from __future__ import annotations

import json
import math
import html
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
FED_CALENDAR_URL = "https://www.federalreserve.gov/json/calendar.json"
BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
PRESIDENT_ICS_URL = ("https://calendar.google.com/calendar/ical/"
                     "cantymedia.com_62fqfmv1eejqs9hntbr6hof5kc%40group.calendar.google.com/public/basic.ics")
UA = "TomahawkDesk/1.0 (local research desk; calendar reader)"
MAX_BYTES = 4 * 1024 * 1024
# Public Google calendars (the default White House feed) return their whole history: about
# 11 MB after roughly 15 seconds of server-side generation (measured 2026-09-25).
LARGE_ICS_MAX_BYTES = 24 * 1024 * 1024
LARGE_ICS_READ_TIMEOUT = 45
REFRESH_SEC = 3 * 3600
RETRY_SEC = 15 * 60
KEEP_PAST_HOURS = 6
KEEP_AHEAD_DAYS = 21
IMPACTS = ("high", "medium", "low")

_lock = threading.RLock()
_refresh_lock = threading.Lock()
_state: dict[str, Any] = {"events": [], "sources": {}, "at": 0.0, "loaded": False}
_data_dir: Path | None = None


# --- parsing ------------------------------------------------------------------

def _clean(text: Any, limit: int = 200) -> str:
    raw = re.sub(r"<[^>]+>", " ", html.unescape(str(text or "")))
    return " ".join(raw.split())[:limit]


def _et(day: date, clock: dtime) -> datetime:
    return datetime.combine(day, clock, tzinfo=ET)


def _event(source: str, title: str, start: datetime, *, impact: str, kind: str, all_day: bool = False,
           detail: str = "", url: str | None = None) -> dict[str, Any]:
    start_utc = start.astimezone(timezone.utc)
    ident = re.sub(r"[^a-z0-9]+", "-", f"{source}-{kind}-{start_utc:%Y%m%d%H%M}-{title}".lower())[:120]
    safe_url = url if isinstance(url, str) and url.startswith("https://") else None
    return {"id": ident, "source": source, "title": _clean(title, 160), "kind": kind, "impact": impact,
            "start": start_utc.isoformat(), "all_day": all_day, "detail": _clean(detail, 240), "url": safe_url}


_FED_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?\s*m\.?", re.I)
# "Chair" or "Chairman", but not "Vice Chair".
_FED_CHAIR = re.compile(r"(?<!Vice )\bChair(?:man|woman)?\b")


def _fed_clock(text: str) -> dtime | None:
    match = _FED_TIME.search(text or "")
    if not match:
        return None
    if not 1 <= int(match.group(1)) <= 12 or not 0 <= int(match.group(2)) <= 59:
        return None
    hour, minute = int(match.group(1)) % 12, int(match.group(2))
    if match.group(3).lower() == "p":
        hour += 12
    return dtime(hour, minute)


def _fed_days(month: str, days: str) -> list[date]:
    try:
        year, mon = (int(x) for x in str(month).split("-")[:2])
    except (TypeError, ValueError):
        return []
    out = []
    for part in re.split(r"[,\s]+", str(days or "")):
        bounds = [p for p in part.split("-") if p.strip()]
        try:
            span = range(int(bounds[0]), int(bounds[-1]) + 1) if bounds else []
        except ValueError:
            continue
        for day in span:
            try:
                out.append(date(year, mon, day))
            except ValueError:
                continue
    return out


def classify_fed(row: dict[str, Any]) -> tuple[str, str] | None:
    """(impact, kind) for a Fed calendar row, or None to skip it."""
    kind_raw = str(row.get("type") or "")
    title = str(row.get("title") or "")
    if kind_raw == "FOMC":
        if "Press Conference" in title:
            return "high", "fomc_press_conference"
        if "Minutes" in title:
            return "medium", "fomc_minutes"
        return "high", "fomc_statement"
    if kind_raw in ("Speeches", "Testimony"):
        chair = bool(_FED_CHAIR.search(title))
        kind = "fed_testimony" if kind_raw == "Testimony" else "fed_speech"
        return ("high" if chair else "medium"), ("fed_chair" if chair else kind)
    if kind_raw == "Beige":
        return "medium", "beige_book"
    if kind_raw in ("Stat", "Board", "Conferences"):
        return "low", "fed_other"
    return None  # holidays and notices


def parse_fed_calendar(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("events") if isinstance(payload, dict) else None
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        cls = classify_fed(row)
        if not cls:
            continue
        impact, kind = cls
        clock = _fed_clock(str(row.get("time") or ""))
        days = _fed_days(row.get("month"), row.get("days"))
        if kind == "fomc_statement" and days:
            days = days[-1:]  # a two-day meeting ends with the 2:00 p.m. statement
            clock = clock if clock and clock >= dtime(12, 0) else dtime(14, 0)
        for day in days:
            start = _et(day, clock or dtime(0, 0))
            title = "FOMC statement" if kind == "fomc_statement" else str(row.get("title") or "")
            out.append(_event("fed", title, start, impact=impact, kind=kind, all_day=clock is None,
                              detail=f"{_clean(row.get('description'), 120)} {_clean(row.get('location'), 120)}".strip(),
                              url=row.get("link") or row.get("live") or "https://www.federalreserve.gov/newsevents/calendar.htm"))
    return out


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_unescape(value: str) -> str:
    return value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _ics_time(params: str, value: str, default_tz: ZoneInfo) -> tuple[datetime, bool] | None:
    value = value.strip()
    try:
        if "VALUE=DATE" in params.upper() and len(value) == 8:
            return _et(datetime.strptime(value, "%Y%m%d").date(), dtime(0, 0)), True
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
        naive = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    zone = default_tz
    match = re.search(r"TZID=([^;:]+)", params)
    if match:
        name = match.group(1).strip().strip('"')
        if "eastern" in name.lower() or name in ("US-Eastern", "EST5EDT"):
            zone = ET
        else:
            try:
                zone = ZoneInfo(name)
            except Exception:  # An explicit but unknown timezone cannot be guessed.
                return None
    return naive.replace(tzinfo=zone), False


def parse_ics(content: bytes | str, *, default_tz: ZoneInfo = ET) -> list[dict[str, Any]]:
    """Minimal iCalendar reader: SUMMARY, DESCRIPTION, LOCATION, URL and DTSTART per VEVENT."""
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)
    events, current = [], None
    for line in _unfold(text):
        if line.startswith("BEGIN:VEVENT"):
            current = {}
        elif line.startswith("END:VEVENT"):
            if current and current.get("start"):
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name, _, params = head.partition(";")
            name = name.upper()
            if name == "DTSTART":
                parsed = _ics_time(params, value, default_tz)
                if parsed:
                    current["start"], current["all_day"] = parsed
            elif name in ("SUMMARY", "DESCRIPTION", "LOCATION", "URL", "STATUS"):
                current[name.lower()] = _ics_unescape(value)
    return [e for e in events if str(e.get("status") or "").upper() != "CANCELLED"]


_BLS_HIGH = {"consumer price index": "cpi", "employment situation": "jobs_report"}
_BLS_MEDIUM = {"producer price index": "ppi", "job openings and labor turnover": "jolts",
               "employment cost index": "eci", "productivity and costs": "productivity",
               "u.s. import and export price": "import_prices"}


def classify_bls(title: str) -> tuple[str, str]:
    low = (title or "").lower()
    if low.startswith(("state ", "metropolitan", "county")):
        return "low", "bls_other"
    for key, kind in _BLS_HIGH.items():
        if low.startswith(key):
            return "high", kind
    for key, kind in _BLS_MEDIUM.items():
        if low.startswith(key):
            return "medium", kind
    return "low", "bls_other"


_PRESIDENT_HIGH = re.compile(r"address(es)? (to )?the nation|oval office address|state of the union|joint session|"
                             r"prime[- ]time address|press conference|news conference|major announcement|"
                             r"announce(s|ment)?\b.*\b(tariff|trade|econom|rate|tax|sanction|war|emergency)", re.I)
_PRESIDENT_MEDIUM = re.compile(r"delivers? (remarks|an? address|a speech)|\bspeech\b|\bremarks on\b|"
                               r"signs? (an? )?executive order|executive order|tariff|economy|economic|"
                               r"federal reserve|trade (deal|agreement)|announce", re.I)
_PRESIDENT_LOW = re.compile(r"closed press|intelligence briefing|lunch|dinner|departs|arrives|"
                            r"no public events|travel|pool call time", re.I)


def classify_president(title: str, detail: str = "") -> str:
    text = f"{title} {detail}"
    if _PRESIDENT_HIGH.search(text):
        return "high"
    if _PRESIDENT_LOW.search(text) and not _PRESIDENT_MEDIUM.search(title):
        return "low"
    return "medium" if _PRESIDENT_MEDIUM.search(text) else "low"


def parse_custom(rows: Any) -> list[dict[str, Any]]:
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            start = parse_local_time(str(row.get("start") or ""))
        except ValueError:
            continue
        impact = row.get("impact") if row.get("impact") in IMPACTS else "high"
        out.append(_event("custom", str(row.get("title") or "Owner event"), start, impact=impact, kind="custom",
                          detail="Added by you in Settings"))
    return out


def parse_local_time(text: str) -> datetime:
    """'2026-09-25 14:00' (Eastern) or an ISO time with an offset."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Event time is required")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M")
    return parsed.astimezone(ET) if parsed.tzinfo else parsed.replace(tzinfo=ET)


def validate_custom_events(rows: Any) -> list[dict[str, Any]]:
    """Config input: up to 50 {title, start, impact}; raises ValueError on bad rows."""
    if not isinstance(rows, list) or len(rows) > 50:
        raise ValueError("custom_market_events must be a list of at most 50 events")
    out = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Each custom event needs a title, a start time and an impact")
        title = _clean(row.get("title"), 120)
        if not title:
            raise ValueError("Each custom event needs a title")
        start = parse_local_time(str(row.get("start") or ""))
        impact = row.get("impact") or "high"
        if impact not in IMPACTS:
            raise ValueError("Event impact must be high, medium or low")
        out.append({"title": title, "start": start.strftime("%Y-%m-%d %H:%M"), "impact": impact})
    return out


def static_fomc(today: date) -> list[dict[str, Any]]:
    """Offline fallback: macro_calendar's FOMC decision days (2:00 statement, 2:30 press conference)."""
    try:
        from macro_calendar import FOMC_DECISION_DAYS
    except Exception:  # noqa: BLE001
        return []
    out = []
    for day in sorted(FOMC_DECISION_DAYS):
        if today - timedelta(days=1) <= day <= today + timedelta(days=KEEP_AHEAD_DAYS):
            out.append(_event("fomc_calendar", "FOMC statement", _et(day, dtime(14, 0)), impact="high", kind="fomc_statement",
                              url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"))
            out.append(_event("fomc_calendar", "FOMC Press Conference", _et(day, dtime(14, 30)), impact="high",
                              kind="fomc_press_conference", url="https://www.federalreserve.gov/live-broadcast.htm"))
    return out


# --- fetching and caching -------------------------------------------------------

def bls_user_agent() -> str:
    """Owner-set BLS identity; BLS only serves bots that say how to contact their owner."""
    value = " ".join((os.environ.get("BLS_USER_AGENT") or "").split())[:200]
    return value if "@" in value else ""


def _fetch(url: str, user_agent: str = UA, *, max_bytes: int = MAX_BYTES, read_timeout: float = 12) -> bytes:
    with requests.get(url, timeout=(10, read_timeout), stream=True,
                      headers={"User-Agent": user_agent, "Accept": "*/*"}) as response:
        if response.status_code != 200:
            raise ValueError(f"HTTP {response.status_code}")
        body = bytearray()
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ValueError("feed too large")
    return bytes(body)


def _short_error(exc: BaseException) -> str:
    try:
        from news_stream import short_error
        return short_error(exc)
    except Exception:  # noqa: BLE001
        return type(exc).__name__


def _president_urls() -> list[str]:
    value = (os.environ.get("PRESIDENT_SCHEDULE_ICS") or PRESIDENT_ICS_URL).strip()
    return [] if value.lower() in ("", "off", "0", "none") else [value]


def _extra_urls() -> list[str]:
    raw = os.environ.get("MARKET_EVENTS_ICS") or ""
    return [u.strip() for u in raw.split(",") if u.strip().startswith("https://")][:6]


def _source_fed() -> list[dict[str, Any]]:
    return parse_fed_calendar(json.loads(_fetch(FED_CALENDAR_URL).decode("utf-8-sig")))


def _source_bls() -> list[dict[str, Any]]:
    contact = bls_user_agent()
    try:
        body = _fetch(BLS_ICS_URL, contact or UA)
    except ValueError as exc:
        if str(exc) == "HTTP 403" and not contact:
            raise ValueError("BLS blocks readers without a contact email; set BLS_USER_AGENT in .env") from exc
        raise
    out = []
    for row in parse_ics(body):
        impact, kind = classify_bls(row.get("summary") or "")
        out.append(_event("bls", row.get("summary") or "BLS release", row["start"], impact=impact, kind=kind,
                          all_day=row.get("all_day", False), url="https://www.bls.gov/schedule/news_release/"))
    if not out:
        raise ValueError("BLS returned no calendar entries")
    return out


def bls_offline_schedule(now):
    """Dated official schedule snapshot, used only when BLS and its cache are unavailable.

    Verified 2026-09-25 at https://www.bls.gov/schedule/2026/.
    Partial coverage: selected releases through October; never label this a live feed.
    """
    if now.astimezone(ET).date() > date(2026, 10, 31):
        return []
    schedule = [
        ("2026-09-25 10:00", "Employee Benefits in the United States"),
        ("2026-09-29 10:00", "Job Openings and Labor Turnover Survey"),
        ("2026-09-30 10:00", "Metropolitan Area Employment and Unemployment"),
        ("2026-10-02 08:30", "Employment Situation"),
        ("2026-10-14 08:30", "Consumer Price Index"),
        ("2026-10-14 08:30", "Real Earnings"),
        ("2026-10-15 08:30", "Producer Price Index"),
        ("2026-10-16 08:30", "U.S. Import and Export Price Indexes"),
        ("2026-10-20 10:00", "State Employment and Unemployment"),
        ("2026-10-21 10:00", "Usual Weekly Earnings of Wage and Salary Workers"),
        ("2026-10-28 10:00", "Metropolitan Area Employment and Unemployment"),
        ("2026-10-28 10:00", "Quarterly Data Series on Business Employment Dynamics"),
        ("2026-10-29 10:00", "Consumer Expenditures"),
        ("2026-10-30 08:30", "Employment Cost Index"),
    ]
    return [dict(_event("bls", title, datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=ET),
                        impact=classify_bls(title)[0], kind=classify_bls(title)[1],
                        detail="Official schedule saved Sep 25; feed unavailable. Dates may change.",
                        url="https://www.bls.gov/schedule/2026/"), schedule_snapshot=True,
                 verified_on="2026-09-25") for stamp, title in schedule]


def _source_ics(url: str, source: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Rows inside the kept window only; full-history calendars would bloat the cache."""
    now = now or datetime.now(timezone.utc)
    lo, hi = now - timedelta(hours=KEEP_PAST_HOURS + 24), now + timedelta(days=KEEP_AHEAD_DAYS)
    out = []
    for row in parse_ics(_fetch(url, max_bytes=LARGE_ICS_MAX_BYTES, read_timeout=LARGE_ICS_READ_TIMEOUT)):
        if not lo <= row["start"] <= hi:
            continue
        title = row.get("summary") or "Scheduled event"
        detail = f"{row.get('description') or ''} {row.get('location') or ''}"
        if source == "president":
            impact, kind = classify_president(title, detail), "president"
        else:
            impact, kind = ("medium", "calendar")
        out.append(_event(source, title, row["start"], impact=impact, kind=kind, all_day=row.get("all_day", False),
                          detail=detail, url=row.get("url")))
    return out


def configure(data_dir: Path | None) -> None:
    global _data_dir
    _data_dir = Path(data_dir) if data_dir else None


def _cache_path() -> Path | None:
    return (_data_dir / "market_events.json") if _data_dir else None


def _window_filter(rows: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    lo, hi = now - timedelta(hours=KEEP_PAST_HOURS + 24), now + timedelta(days=KEEP_AHEAD_DAYS)
    out, seen = [], set()
    for row in rows:
        try:
            start = datetime.fromisoformat(row["start"])
            if start.tzinfo is None or row.get("impact") not in IMPACTS or not row.get("id"):
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        key = (row.get("kind"), row["start"][:16]) if str(row.get("kind") or "").startswith("fomc") else row.get("id")
        if lo <= start <= hi and key not in seen:
            seen.add(key)
            out.append(row)
    out.sort(key=lambda r: (r["start"], IMPACTS.index(r.get("impact", "low"))))
    return out


def refresh(now: datetime | None = None) -> dict[str, Any]:
    """Fetch every source in parallel. One failing source keeps its last good rows."""
    now = now or datetime.now(timezone.utc)
    if not _refresh_lock.acquire(blocking=False):
        return status()
    try:
        jobs: dict[str, Any] = {"fed": _source_fed, "bls": _source_bls}
        for i, url in enumerate(_president_urls()):
            jobs["president" if i == 0 else f"president_{i}"] = (lambda u=url: _source_ics(u, "president", now))
        for i, url in enumerate(_extra_urls()):
            jobs[f"calendar_{i + 1}"] = (lambda u=url: _source_ics(u, "calendar", now))
        with _lock:
            previous = {k: list(v) for k, v in (_state.get("by_source") or {}).items()}
            sources = {}  # Disabled sources must not survive as permanent error badges.
        by_source: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="market-events") as pool:
            futures = {name: pool.submit(fn) for name, fn in jobs.items()}
            for name, future in futures.items():
                try:
                    rows = future.result()
                    by_source[name] = rows
                    sources[name] = {"ok": True, "error": None, "count": len(rows), "checked_at": now.isoformat()}
                except Exception as exc:  # noqa: BLE001 - keep the last good rows for this source
                    by_source[name] = previous.get(name, [])
                    if name == "bls" and not _window_filter(by_source[name], now):
                        by_source[name] = bls_offline_schedule(now)
                    sources[name] = {"ok": False, "error": _short_error(exc), "count": len(by_source[name]),
                                     "fallback": "dated schedule" if any(r.get("schedule_snapshot") for r in by_source[name]) else "last good feed" if by_source[name] else None,
                                     "checked_at": now.isoformat()}
        with _lock:
            _state.update(by_source=by_source, sources=sources, at=time.time(), loaded=True)
        _save()
        return status()
    finally:
        _refresh_lock.release()


def _save() -> None:
    path = _cache_path()
    if not path:
        return
    with _lock:
        data = {"at": _state["at"], "by_source": _state.get("by_source") or {}, "sources": _state.get("sources") or {}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # optional cache; the next refresh rebuilds it


def _load_cache() -> None:
    with _lock:
        if _state["loaded"]:
            return
        _state["loaded"] = True
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else None
    except (OSError, ValueError):
        data = None  # a bad optional cache is ignored, never a trading-state error
    if isinstance(data, dict) and isinstance(data.get("by_source"), dict):
        try:
            at = float(data.get("at") or 0)
            if not math.isfinite(at) or at < 0 or at > time.time() + 60:
                at = 0
            groups = {k: [r for r in v if isinstance(r, dict)] for k, v in data["by_source"].items() if isinstance(v, list)}
            sources = data.get("sources") if isinstance(data.get("sources"), dict) else {}
            sources = {k: v for k, v in sources.items() if isinstance(v, dict)}
        except (TypeError, ValueError):
            return
        with _lock:
            _state.update(by_source=groups, sources=sources, at=at)


def _kick_refresh() -> None:
    if (os.environ.get("TOMAHAWK_NO_BG") or "").strip().lower() in ("1", "true", "yes", "on"):
        return
    if _refresh_lock.locked():
        return
    threading.Thread(target=refresh, name="market-events-refresh", daemon=True).start()


def events(cfg: dict[str, Any] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    """Cached events plus static FOMC dates and owner events. Never waits on the network."""
    now = now or datetime.now(timezone.utc)
    _load_cache()
    with _lock:
        stale = time.time() - float(_state.get("at") or 0) > (RETRY_SEC if any(not s.get("ok") for s in (_state.get("sources") or {}).values()) else REFRESH_SEC)
        rows = [r for group in (_state.get("by_source") or {}).values() for r in group]
    if stale:
        _kick_refresh()
    rows += static_fomc(now.astimezone(ET).date())
    rows += parse_custom((cfg or {}).get("custom_market_events"))
    return _window_filter(rows, now)


def upcoming(cfg: dict[str, Any] | None = None, now: datetime | None = None, *, hours: float = 36,
             min_impact: str = "medium") -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    floor = IMPACTS.index(min_impact)
    out = []
    for row in events(cfg, now):
        start = datetime.fromisoformat(row["start"])
        if IMPACTS.index(row.get("impact", "low")) > floor:
            continue
        if now - timedelta(minutes=30) <= start <= now + timedelta(hours=hours):
            out.append(dict(row, minutes_away=round((start - now).total_seconds() / 60)))
    return out


def guard_window(row: dict[str, Any], cfg: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """No-new-entry window for a high-impact event; releases before the open move to the open."""
    if row.get("impact") != "high" or row.get("all_day"):
        return None
    before = timedelta(minutes=float(cfg.get("event_guard_before_min", 15) or 0))
    after = timedelta(minutes=float(cfg.get("event_guard_after_min", 15) or 0))
    start = datetime.fromisoformat(row["start"]).astimezone(ET)
    opening = datetime.combine(start.date(), dtime(9, 30), tzinfo=ET)
    if opening - timedelta(hours=2) <= start < opening:
        # A pre-market release (CPI and jobs at 8:30) hits at the open: let the open settle.
        return opening, opening + after
    return start - before, start + after


def active_window(cfg: dict[str, Any] | None, now: datetime | None = None) -> dict[str, Any] | None:
    """The high-impact event whose guard window contains now, if the guard is on."""
    cfg = cfg or {}
    if not cfg.get("event_guard_enabled", True):
        return None
    now = now or datetime.now(timezone.utc)
    for row in events(cfg, now):
        window = guard_window(row, cfg)
        if window and window[0] <= now <= window[1]:
            return dict(row, window_start=window[0].isoformat(), window_end=window[1].isoformat())
    return None


def clock_et(value: datetime | str) -> str:
    """'2:30 PM ET' (portable: Windows strftime has no %-I)."""
    moment = (datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone(ET)
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'} ET"


def day_et(value: datetime | str) -> str:
    moment = (datetime.fromisoformat(value) if isinstance(value, str) else value).astimezone(ET)
    return f"{moment:%a %b} {moment.day}"


def describe(row: dict[str, Any], now: datetime | None = None) -> str:
    start = datetime.fromisoformat(row["start"]).astimezone(ET)
    today = (now or datetime.now(timezone.utc)).astimezone(ET).date()
    if row.get("all_day"):
        when = day_et(start)
    elif start.date() == today:
        when = clock_et(start)
    else:
        when = f"{day_et(start)}, {clock_et(start)}"
    return f"{row.get('title')} ({when})"


def window_message(row: dict[str, Any], now: datetime | None = None) -> str:
    end = clock_et(row["window_end"])
    return f"Scheduled event: {describe(row, now)}. New automated entries wait until {end}; exits continue."


def status() -> dict[str, Any]:
    _load_cache()
    with _lock:
        at = float(_state.get("at") or 0)
        return {"sources": dict(_state.get("sources") or {}),
                "refreshed_at": datetime.fromtimestamp(at, timezone.utc).isoformat() if at else None,
                "president_feed": bool(_president_urls()), "extra_feeds": len(_extra_urls())}


def start_background() -> None:
    if (os.environ.get("TOMAHAWK_NO_BG") or "").strip().lower() in ("1", "true", "yes", "on"):
        return

    def loop() -> None:
        while True:
            try:
                refresh()
            except Exception:  # noqa: BLE001 - calendar problems never stop the desk
                pass
            failed = any(not s.get("ok") for s in status()["sources"].values())
            time.sleep(RETRY_SEC if failed else REFRESH_SEC)

    threading.Thread(target=loop, name="market-events", daemon=True).start()
