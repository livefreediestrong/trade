"""
Aggregate which optional API providers are configured (no secrets).

Used by /api/health and /api/state for the API pack status strip.
"""
from __future__ import annotations

import os
from typing import Any


def _has(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def configured_map() -> dict[str, bool]:
    """Boolean map — safe for health/state."""
    try:
        import data_sources as ds

        ds._load_env()
    except Exception:
        pass

    quiver = _has("QUIVER_API_KEY")
    uw = _has("UNUSUAL_WHALES_API_KEY")
    finnhub = _has("FINNHUB_API_KEY")
    edgar_ua = (_has("EDGAR_USER_AGENT") and "@" in (os.environ.get("EDGAR_USER_AGENT") or ""))
    twilio_keys = all(
        _has(k)
        for k in (
            "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN",
            "TWILIO_FROM_NUMBER",
            "TWILIO_TO_NUMBER",
        )
    )
    twilio_flag = (os.environ.get("ALERT_TWILIO") or "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return {
        "polygon": _has("POLYGON_API_KEY"),
        "fred": _has("FRED_API_KEY"),
        "finnhub": finnhub,
        "benzinga": _has("BENZINGA_API_KEY"),
        "edgar": edgar_ua,
        "quiver": quiver,
        "unusual_whales": uw,
        "options_flow": quiver or uw or finnhub,
        "alert_webhook": _has("ALERT_WEBHOOK_URL"),
        "twilio": twilio_keys and twilio_flag,
        "twilio_keys_present": twilio_keys,
        "alpaca": _has("ALPACA_API_KEY") and (_has("ALPACA_API_SECRET") or _has("ALPACA_SECRET_KEY")),
        "gemini": _has("GEMINI_API_KEY") or _has("GOOGLE_API_KEY"),
        "reddit": _has("REDDIT_CLIENT_ID") and _has("REDDIT_CLIENT_SECRET"),
        "stocktwits": True,  # public trending — no key
        "yahoo": True,
    }


def public_pack_status() -> dict[str, Any]:
    """Rich status with per-provider notes (still no secrets)."""
    cfg = configured_map()
    details: dict[str, Any] = {}
    try:
        import polygon_client as pc

        details["polygon"] = pc.public_status()
    except Exception as exc:  # noqa: BLE001
        details["polygon"] = {"configured": cfg["polygon"], "error": str(exc)[:80]}
    try:
        import macro_calendar as mc

        details["fred"] = mc.public_status()
    except Exception as exc:  # noqa: BLE001
        details["fred"] = {"configured": cfg["fred"], "error": str(exc)[:80]}
    try:
        import edgar_client as ec

        details["edgar"] = ec.public_status()
    except Exception as exc:  # noqa: BLE001
        details["edgar"] = {"configured": cfg["edgar"], "error": str(exc)[:80]}
    try:
        import options_flow as of

        details["options_flow"] = of.public_status()
    except Exception as exc:  # noqa: BLE001
        details["options_flow"] = {"configured": cfg["options_flow"], "error": str(exc)[:80]}
    try:
        import news_stream as ns

        details["news"] = ns.public_status()
    except Exception as exc:  # noqa: BLE001
        details["news"] = {"configured": True, "error": str(exc)[:80]}
    try:
        import desk_alerts as da

        details["alerts"] = da.public_status()
    except Exception as exc:  # noqa: BLE001
        details["alerts"] = {"configured": True, "error": str(exc)[:80]}

    details["stocktwits"] = {
        "provider": "stocktwits",
        "configured": True,
        "key_required": False,
        "note": "Public trending symbols JSON — no API key. Best-effort; may 403 from some IPs.",
        "docs": "https://api.stocktwits.com/developers/docs",
    }
    return {
        "configured": cfg,
        "details": details,
        "pack": "api_pack_v1",
    }
