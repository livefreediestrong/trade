"""
SEC EDGAR free filings client (no paid terminal).

Requires EDGAR_USER_AGENT with a contact email per SEC fair-access policy:
  https://www.sec.gov/os/accessing-edgar-data
Example: EDGAR_USER_AGENT="TomahawkDesk/1.0 (you@example.com)"

Endpoints used (public):
  - https://www.sec.gov/files/company_tickers.json
  - https://data.sec.gov/submissions/CIK##########.json
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_lock = threading.RLock()
_ticker_map: dict[str, str] = {}
_ticker_at = 0.0
_TICKER_TTL = 86400.0
_cache: dict[str, Any] = {}
_CACHE_TTL = 600.0


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


def user_agent() -> str:
    return (os.environ.get("EDGAR_USER_AGENT") or "").strip()


def is_configured() -> bool:
    ua = user_agent()
    # Require something that looks like it includes an email (SEC asks for contact)
    return bool(ua) and ("@" in ua or "mailto:" in ua.lower())


def public_status() -> dict[str, Any]:
    return {
        "provider": "sec_edgar",
        "configured": is_configured(),
        "docs": "https://www.sec.gov/os/accessing-edgar-data",
        "data": "https://data.sec.gov",
        "note": "Free. Set EDGAR_USER_AGENT with a real contact email. No paid terminal.",
    }


def _headers() -> dict[str, str]:
    ua = user_agent() or "TomahawkDesk/1.0 (research; set EDGAR_USER_AGENT)"
    return {
        "User-Agent": ua,
        "Accept-Encoding": "gzip, deflate",
        "Accept": "application/json",
    }


def _get_json(url: str, timeout: int = 15) -> Any | None:
    if not is_configured():
        return None
    try:
        r = requests.get(url, headers=_headers(), timeout=timeout)
        if r.status_code == 429:
            time.sleep(1.5)
            r = requests.get(url, headers=_headers(), timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _ensure_ticker_map() -> dict[str, str]:
    global _ticker_map, _ticker_at
    with _lock:
        if _ticker_map and (time.time() - _ticker_at) < _TICKER_TTL:
            return dict(_ticker_map)
    data = _get_json(TICKERS_URL)
    mapping: dict[str, str] = {}
    if isinstance(data, dict):
        for _k, row in data.items():
            if not isinstance(row, dict):
                continue
            tick = str(row.get("ticker") or "").upper().strip()
            cik = row.get("cik_str")
            if tick and cik is not None:
                mapping[tick] = str(int(cik)).zfill(10)
    with _lock:
        _ticker_map = mapping
        _ticker_at = time.time()
    return dict(mapping)


def cik_for_ticker(symbol: str) -> Optional[str]:
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    return _ensure_ticker_map().get(sym)


def recent_filings(symbol: str, *, limit: int = 8) -> dict[str, Any]:
    """Recent SEC filings for focus ticker → Advanced research chip payload."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {"ok": False, "configured": is_configured(), "filings": [], "error": "no_symbol"}
    if not is_configured():
        return {
            "ok": False,
            "configured": False,
            "ticker": sym,
            "filings": [],
            "status": "not_configured",
            "error": "Set EDGAR_USER_AGENT with contact email",
        }
    cache_key = f"{sym}:{limit}"
    with _lock:
        hit = _cache.get(cache_key)
        if hit and (time.time() - float(hit.get("at") or 0)) < _CACHE_TTL:
            return dict(hit["payload"])

    cik = cik_for_ticker(sym)
    if not cik:
        payload = {
            "ok": False,
            "configured": True,
            "ticker": sym,
            "filings": [],
            "error": "cik_not_found",
        }
        return payload

    data = _get_json(SUBMISSIONS_URL.format(cik=cik))
    if not isinstance(data, dict):
        return {
            "ok": False,
            "configured": True,
            "ticker": sym,
            "cik": cik,
            "filings": [],
            "error": "submissions_fetch_failed",
        }

    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    dates = recent.get("filingDate") or []
    accessions = recent.get("accessionNumber") or []
    primaries = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []

    filings = []
    n = min(len(forms), len(dates), len(accessions), limit)
    for i in range(n):
        acc = str(accessions[i] or "").replace("-", "")
        primary = primaries[i] if i < len(primaries) else ""
        doc_url = None
        if acc and primary:
            doc_url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{primary}"
        filings.append(
            {
                "form": forms[i],
                "filed": dates[i],
                "accession": accessions[i],
                "description": descriptions[i] if i < len(descriptions) else None,
                "url": doc_url,
            }
        )

    payload = {
        "ok": True,
        "configured": True,
        "ticker": sym,
        "cik": cik,
        "name": data.get("name"),
        "filings": filings,
        "source": "data.sec.gov",
        "chip_label": f"SEC {filings[0]['form']}" if filings else "SEC filings",
    }
    with _lock:
        _cache[cache_key] = {"at": time.time(), "payload": payload}
    return dict(payload)
