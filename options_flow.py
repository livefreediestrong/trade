"""
Options flow adapter — Advanced research context ONLY (never auto-trade).

Preference order:
  1. Quiver Quantitative if QUIVER_API_KEY
  2. Unusual Whales-style if UNUSUAL_WHALES_API_KEY (stub path; honest if HTTP fails)
  3. Finnhub option chain / chain snapshot if FINNHUB_API_KEY supports it
  4. not_configured

Paid flow terminals are optional stubs — desk runs without them.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests


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


def quiver_key() -> str:
    return (os.environ.get("QUIVER_API_KEY") or "").strip()


def uw_key() -> str:
    return (os.environ.get("UNUSUAL_WHALES_API_KEY") or "").strip()


def finnhub_key() -> str:
    return (os.environ.get("FINNHUB_API_KEY") or "").strip()


def is_configured() -> bool:
    return bool(quiver_key() or uw_key() or finnhub_key())


def public_status() -> dict[str, Any]:
    provider = None
    if quiver_key():
        provider = "quiver"
    elif uw_key():
        provider = "unusual_whales"
    elif finnhub_key():
        provider = "finnhub_options"
    return {
        "provider": provider or "none",
        "configured": is_configured(),
        "quiver": bool(quiver_key()),
        "unusual_whales": bool(uw_key()),
        "finnhub": bool(finnhub_key()),
        "auto_trade": False,
        "note": "Advanced research only — never drives paper/live fills.",
        "docs": {
            "quiver": "https://api.quiverquant.com/docs",
            "unusual_whales": "https://unusualwhales.com/information/api",
            "finnhub": "https://finnhub.io/docs/api/option-chain",
        },
    }


def _quiver_flow(symbol: str) -> dict[str, Any] | None:
    key = quiver_key()
    if not key:
        return None
    # Common Quiver path (may vary by plan); fail soft
    url = f"https://api.quiverquant.com/beta/live/options/{symbol.upper()}"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Token {key}", "Accept": "application/json"},
            timeout=12,
        )
        if r.status_code != 200:
            return {
                "ok": False,
                "provider": "quiver",
                "status": "http_error",
                "http": r.status_code,
                "items": [],
            }
        data = r.json()
        items = data if isinstance(data, list) else (data.get("data") or data.get("results") or [])
        if not isinstance(items, list):
            items = []
        norm = []
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            norm.append(
                {
                    "ticker": symbol.upper(),
                    "date": it.get("Date") or it.get("date"),
                    "sentiment": it.get("Sentiment") or it.get("sentiment"),
                    "summary": str(it.get("Description") or it.get("summary") or "")[:200],
                    "raw_keys": sorted(it.keys())[:8],
                }
            )
        return {"ok": True, "provider": "quiver", "items": norm, "status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "provider": "quiver", "status": "error", "error": str(exc)[:160], "items": []}


def _uw_flow(symbol: str) -> dict[str, Any] | None:
    key = uw_key()
    if not key:
        return None
    # Stub-shaped client — endpoint varies by UW plan; honest degrade
    url = "https://api.unusualwhales.com/api/stock/" + symbol.upper() + "/flow-recent"
    try:
        r = requests.get(
            url,
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            timeout=12,
        )
        if r.status_code != 200:
            return {
                "ok": False,
                "provider": "unusual_whales",
                "status": "http_error",
                "http": r.status_code,
                "items": [],
                "note": "UW endpoint/plan may differ — check their API docs.",
            }
        data = r.json()
        items = data.get("data") if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []
        norm = []
        for it in items[:12]:
            if not isinstance(it, dict):
                continue
            norm.append(
                {
                    "ticker": symbol.upper(),
                    "premium": it.get("total_premium") or it.get("premium"),
                    "sentiment": it.get("sentiment") or it.get("side"),
                    "summary": str(it.get("ticker_symbol") or it.get("option_chain") or "")[:200],
                }
            )
        return {"ok": True, "provider": "unusual_whales", "items": norm, "status": "ok"}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "provider": "unusual_whales",
            "status": "error",
            "error": str(exc)[:160],
            "items": [],
        }


_OPT_CACHE: dict[str, tuple[float, Any]] = {}
_OPT_TTL = 600.0
_OPT_UNSUPPORTED_TTL = 6 * 3600.0


def _finnhub_options(symbol: str) -> dict[str, Any] | None:
    """Cached: option chains are research-only, and an unsupported (free-tier) plan
    answers the same way every time — don't spend Finnhub quota re-asking."""
    import time as _t

    sym = (symbol or "").upper()
    hit = _OPT_CACHE.get(sym)
    now = _t.time()
    if hit is not None:
        at, val = hit
        ttl = _OPT_UNSUPPORTED_TTL if (isinstance(val, dict) and val.get("status") == "empty_or_unsupported") else _OPT_TTL
        if now - at < ttl:
            return val
    val = _finnhub_options_uncached(sym)
    if len(_OPT_CACHE) > 300:
        _OPT_CACHE.clear()
    _OPT_CACHE[sym] = (now, val)
    return val


def _finnhub_options_uncached(symbol: str) -> dict[str, Any] | None:
    key = finnhub_key()
    if not key:
        return None
    try:
        import data_sources as ds

        # stock/option-chain — free tier often limited / empty
        data = ds._finnhub_get("stock/option-chain", {"symbol": symbol.upper()})
        if not data:
            return {
                "ok": False,
                "provider": "finnhub_options",
                "status": "empty_or_unsupported",
                "items": [],
                "note": "Finnhub option-chain may require a paid plan.",
            }
        data_rows = data.get("data") if isinstance(data, dict) else None
        items = []
        if isinstance(data_rows, list):
            for exp in data_rows[:4]:
                if not isinstance(exp, dict):
                    continue
                items.append(
                    {
                        "expiration": exp.get("expirationDate"),
                        "calls": len(exp.get("options", {}).get("CALL") or [])
                        if isinstance(exp.get("options"), dict)
                        else None,
                        "puts": len(exp.get("options", {}).get("PUT") or [])
                        if isinstance(exp.get("options"), dict)
                        else None,
                    }
                )
        return {
            "ok": bool(items),
            "provider": "finnhub_options",
            "items": items,
            "status": "ok" if items else "empty",
            "note": "Chain metadata only — not unusual-flow prints.",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "provider": "finnhub_options",
            "status": "error",
            "error": str(exc)[:160],
            "items": [],
        }


def flow_for_ticker(symbol: str) -> dict[str, Any]:
    """Advanced-only context. Never signals auto-trade."""
    sym = (symbol or "").strip().upper()
    base = {
        "ticker": sym,
        "auto_trade": False,
        "advanced_only": True,
        "paper_research": True,
    }
    if not sym:
        return {**base, "ok": False, "status": "no_symbol", "items": [], "configured": is_configured()}
    if not is_configured():
        return {
            **base,
            "ok": False,
            "status": "not_configured",
            "configured": False,
            "items": [],
            "note": "Set QUIVER_API_KEY or UNUSUAL_WHALES_API_KEY (or Finnhub for chain).",
        }

    for fn in (_quiver_flow, _uw_flow, _finnhub_options):
        try:
            res = fn(sym)
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "status": "error", "error": str(exc)[:120], "items": []}
        if res is None:
            continue
        out = {**base, **res, "configured": True}
        if res.get("ok") or res.get("status") not in ("empty_or_unsupported",):
            # Prefer first provider that answered (even soft error) so UI shows which tried
            if res.get("ok") or res.get("provider") in ("quiver", "unusual_whales"):
                return out
            # keep finnhub as last resort return
            last = out
        else:
            last = out
    return locals().get("last") or {
        **base,
        "ok": False,
        "status": "not_configured",
        "configured": False,
        "items": [],
    }
