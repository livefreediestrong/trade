"""
Macro / calendar gates for the paper desk.

- FRED (free API key): https://fred.stlouisfed.org/docs/api/api_key.html
  Series probes + release-calendar heuristics for Fed / CPI high-impact days.
- Earnings: existing Finnhub calendar via data_sources (FINNHUB_API_KEY).
- Optional config flags tighten size or force Ask-me-first / butler note.

Never blocks the app when keys are missing — degrade to empty calendar.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
FRED_BASE = "https://api.stlouisfed.org/fred"
# High-impact series ids (observations) — free with FRED_API_KEY
FRED_SERIES = {
    "FEDFUNDS": "Fed funds rate",
    "CPIAUCSL": "CPI all items",
    "PCEPI": "PCE price index",
    "UNRATE": "Unemployment rate",
    "PAYEMS": "Nonfarm payrolls",
}
# FRED release ids for scheduled high-impact US data (release/dates API).
FRED_RELEASE_HINTS = {
    10: "Consumer Price Index",
    50: "Employment Situation (jobs report)",
}

# FOMC rate-decision (statement) days. FRED has no FOMC release calendar, so this is
# a static list from federalreserve.gov/monetarypolicy/fomccalendars.htm.
# EXTEND EACH YEAR — dates past the list are simply not flagged.
FOMC_DECISION_DAYS = {
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
}

_lock = threading.RLock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_TTL = 900.0  # 15 min


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


def fred_key() -> str:
    return (os.environ.get("FRED_API_KEY") or "").strip()


def is_fred_configured() -> bool:
    return bool(fred_key())


def public_status() -> dict[str, Any]:
    return {
        "provider": "fred",
        "configured": is_fred_configured(),
        "docs": "https://fred.stlouisfed.org/docs/api/api_key.html",
        "signup": "https://fred.stlouisfed.org/docs/api/api_key.html",
        "earnings_via": "finnhub" if (os.environ.get("FINNHUB_API_KEY") or "").strip() else "none",
        "note": "Free FRED key for series/releases; earnings use FINNHUB_API_KEY when set.",
    }


def _today_et() -> date:
    return datetime.now(ET).date()


def _fred_get(path: str, params: dict | None = None) -> Any | None:
    key = fred_key()
    if not key:
        return None
    p = dict(params or {})
    p["api_key"] = key
    p.setdefault("file_type", "json")
    try:
        r = requests.get(f"{FRED_BASE}/{path.lstrip('/')}", params=p, timeout=12)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def fred_latest_observations(series_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Latest observation per series (best-effort)."""
    if not is_fred_configured():
        return []
    out = []
    for sid in series_ids or list(FRED_SERIES.keys()):
        data = _fred_get(
            "series/observations",
            {"series_id": sid, "sort_order": "desc", "limit": 1},
        )
        if not isinstance(data, dict):
            continue
        obs = (data.get("observations") or [])
        if not obs:
            continue
        row = obs[0]
        out.append(
            {
                "series_id": sid,
                "label": FRED_SERIES.get(sid, sid),
                "date": row.get("date"),
                "value": row.get("value"),
            }
        )
    return out


def _weekday_heuristic_high_impact(d: date) -> dict[str, Any]:
    """
    Soft heuristic when FRED release calendar is empty:
    - First Friday → often NFP week
    - Mid-month (~10–15) → often CPI window
    Never claims certainty; flags research_only.
    """
    flags = []
    if d.weekday() == 4 and 1 <= d.day <= 7:
        flags.append({"kind": "nfp_week_heuristic", "label": "Possible NFP Friday (heuristic)"})
    if 10 <= d.day <= 15 and d.weekday() < 5:
        flags.append({"kind": "cpi_window_heuristic", "label": "Possible CPI window (heuristic)"})
    return {"heuristics": flags, "research_only": True}


def fred_release_flags(d: date | None = None) -> dict[str, Any]:
    """Probe FRED releases/dates for today±1; soft-fail + heuristics."""
    d = d or _today_et()
    flags: list[dict[str, Any]] = []
    source = "none"
    fred_error = False
    if d in FOMC_DECISION_DAYS:
        flags.append({"kind": "fed", "label": "Fed rate decision (FOMC)", "date": d.isoformat(), "source": "fomc_calendar"})
        source = "fomc_calendar"
    if is_fred_configured():
        # Ask FRED for release dates falling ON day d (realtime window = d..d).
        # Sorting desc without a window returns far-future scheduled dates only.
        for rid, label in FRED_RELEASE_HINTS.items():
            data = _fred_get(
                "release/dates",
                {
                    "release_id": rid,
                    "include_release_dates_with_no_data": "true",
                    "realtime_start": d.isoformat(),
                    "realtime_end": d.isoformat(),
                    "sort_order": "asc",
                    "limit": 5,
                },
            )
            if not isinstance(data, dict):
                fred_error = True
                continue
            source = "fred_release_dates" if source == "none" else (source if "fred" in source else source + "+fred")
            for item in data.get("release_dates") or []:
                raw = str(item.get("date") or "")[:10]
                if raw != d.isoformat():
                    continue
                kind = "cpi" if rid == 10 else "nfp"
                flags.append({"kind": kind, "label": label, "date": raw, "release_id": rid})

    heur = _weekday_heuristic_high_impact(d)
    high = bool(flags) or bool(heur.get("heuristics"))
    # Only treat real FRED flags as hard high-impact; heuristics are soft notes
    hard = bool(flags)
    return {
        "date": d.isoformat(),
        "high_impact": hard,
        "soft_high_impact": high,
        "flags": flags,
        "heuristics": heur.get("heuristics") or [],
        "source": source,
        "fred_configured": is_fred_configured(),
        # FRED unreachable = unknown, NOT "calm day". Surfaced in the macro strip.
        "fred_error": fred_error,
    }


_EARN_CACHE: dict[tuple[str, int, str], tuple[float, Optional[dict[str, Any]]]] = {}
_EARN_TTL_HIT = 6 * 3600.0   # earnings dates don't move intraday
_EARN_TTL_MISS = 30 * 60.0   # "none found" / fetch failure: retry after 30 min


def earnings_for_ticker(symbol: str, *, soon_days: int = 1) -> Optional[dict[str, Any]]:
    """Earnings today / soon (cached per ticker+day — /api/state polls every few
    seconds and used to spend Finnhub's 60/min free quota on this alone)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    key = (sym, int(soon_days), _today_et().isoformat())
    now = time.time()
    with _lock:
        hit = _EARN_CACHE.get(key)
    if hit is not None:
        at, val = hit
        if now - at < (_EARN_TTL_HIT if val else _EARN_TTL_MISS):
            return dict(val) if val else None
    val = _earnings_for_ticker_uncached(sym, soon_days=soon_days)
    with _lock:
        if len(_EARN_CACHE) > 500:
            _EARN_CACHE.clear()
        _EARN_CACHE[key] = (now, dict(val) if val else None)
    return val


def _earnings_for_ticker_uncached(sym: str, *, soon_days: int = 1) -> Optional[dict[str, Any]]:
    try:
        import data_sources as ds

        fh = ds.finnhub_earnings(sym)
        if fh and fh.get("date"):
            try:
                ed = date.fromisoformat(str(fh["date"])[:10])
            except ValueError:
                ed = None
            if ed is not None:
                days = (ed - _today_et()).days
                return {
                    "ticker": sym,
                    "date": ed.isoformat(),
                    "days_away": days,
                    "is_today": days == 0,
                    "is_soon": 0 <= days <= soon_days,
                    "source": "finnhub",
                    "hour": fh.get("hour"),
                }
        y = ds.yahoo_next_earnings(sym)
        if y and y.get("date"):
            try:
                ed = date.fromisoformat(str(y["date"])[:10])
            except ValueError:
                return None
            days = (ed - _today_et()).days
            return {
                "ticker": sym,
                "date": ed.isoformat(),
                "days_away": days,
                "is_today": days == 0,
                "is_soon": 0 <= days <= soon_days,
                "source": "yahoo",
            }
    except Exception:
        return None
    return None


def calendar_snapshot(force: bool = False) -> dict[str, Any]:
    with _lock:
        age = time.time() - float(_cache.get("at") or 0)
        if not force and _cache.get("payload") and age < _CACHE_TTL:
            return dict(_cache["payload"])
    release = fred_release_flags()
    obs = fred_latest_observations() if is_fred_configured() else []
    payload = {
        "ok": True,
        "as_of": datetime.now(timezone.utc).isoformat(),
        "today_et": _today_et().isoformat(),
        "release": release,
        "fred_observations": obs,
        "providers": public_status(),
    }
    with _lock:
        _cache["at"] = time.time()
        _cache["payload"] = payload
    return dict(payload)


def risk_adjustment(
    ticker: str | None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Paper risk hint for Fed/CPI/earnings days.

    Config (DEFAULT_CONFIG / POST /api/config):
      macro_gates_enabled: bool (default True)
      macro_size_mult: float (default 0.5) — applied when hard high-impact or earnings today
      macro_force_ask_first: bool (default True) — auto_paper → enqueue Waiting instead
      macro_use_heuristics: bool (default False) — soft weekday heuristics also tighten
    """
    cfg = cfg or {}
    enabled = bool(cfg.get("macro_gates_enabled", True))
    size_mult = 1.0
    force_ask = False
    reasons: list[str] = []
    butler = None
    if not enabled:
        return {
            "enabled": False,
            "size_mult": 1.0,
            "force_ask_first": False,
            "reasons": [],
            "butler_note": None,
            "calendar": None,
        }

    cal = calendar_snapshot()
    release = cal.get("release") or {}
    use_heur = bool(cfg.get("macro_use_heuristics", False))
    hard = bool(release.get("high_impact"))
    soft = bool(release.get("soft_high_impact")) and use_heur

    earn = earnings_for_ticker(ticker) if ticker else None
    earn_today = bool(earn and earn.get("is_today"))

    cut = 0.5
    try:
        cut = float(cfg.get("macro_size_mult", 0.5))
    except (TypeError, ValueError):
        cut = 0.5
    cut = max(0.1, min(1.0, cut))
    force_cfg = bool(cfg.get("macro_force_ask_first", True))

    if hard:
        size_mult = min(size_mult, cut)
        force_ask = force_cfg
        kinds = sorted({f.get("kind") for f in (release.get("flags") or []) if f.get("kind")})
        label = "/".join(kinds) if kinds else "macro"
        reasons.append(f"high_impact_{label}")
        butler = f"Macro day ({label}) — size cut / Ask-me-first"
    elif soft:
        size_mult = min(size_mult, max(cut, 0.75))
        reasons.append("macro_heuristic")
        butler = "Possible macro window (heuristic) — trading smaller"
    if earn_today:
        size_mult = min(size_mult, cut)
        force_ask = force_ask or force_cfg
        reasons.append(f"earnings_today:{str(ticker or '').upper()}")
        butler = (butler + "; " if butler else "") + f"{str(ticker or '').upper()} earnings today — Ask-me-first"

    force_ask_out = bool(force_ask and (hard or earn_today or size_mult < 1.0))
    return {
        "enabled": True,
        "size_mult": round(size_mult, 4),
        "force_ask_first": force_ask_out,
        "reasons": reasons,
        "butler_note": butler,
        "earnings": earn,
        "calendar": {
            "high_impact": hard,
            "flags": release.get("flags") or [],
            "heuristics": release.get("heuristics") or [],
            "date": release.get("date"),
        },
    }
