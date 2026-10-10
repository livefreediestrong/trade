"""Optional crash reporting to Sentry. Off unless SENTRY_DSN is set and sentry-sdk is installed.

Owner decision: reports carry exception types, messages and stack frames only.
Request bodies, headers, cookies, query strings, local variables and breadcrumbs
are dropped, and broker account numbers and long secrets are redacted, because
this desk handles a real brokerage account.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

log = logging.getLogger(__name__)

# IBKR (U1234567, DU1234567, F…/I…) and Alpaca-style account IDs, bearer tokens and long keys.
_ACCOUNT_RE = re.compile(r"\b(?:DU|DF|U|F|I)\d{5,10}\b")
_SECRET_RE = re.compile(r"\b(?:Bearer\s+)?[A-Za-z0-9_\-]{32,}\b")
_state: dict[str, Any] = {"enabled": False, "error": None}


def scrub_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _SECRET_RE.sub("[redacted]", _ACCOUNT_RE.sub("[account]", value))


def scrub_event(event: dict[str, Any], hint: Any = None) -> dict[str, Any]:
    """Sentry before_send: keep only what is needed to find the failing code."""
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {"method": request.get("method"),
                            "url": str(request.get("url") or "").split("?", 1)[0]}
    event.pop("breadcrumbs", None)
    event.pop("user", None)
    event.pop("extra", None)
    if "message" in event:
        event["message"] = scrub_text(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        logentry["message"] = scrub_text(logentry.get("message"))
        logentry.pop("params", None)
    for exc in ((event.get("exception") or {}).get("values") or []):
        exc["value"] = scrub_text(exc.get("value"))
        for frame in ((exc.get("stacktrace") or {}).get("frames") or []):
            frame.pop("vars", None)
    return event


def init() -> bool:
    """Start Sentry when configured. Never raises; the desk runs the same without it."""
    try:
        import data_sources
        data_sources._load_env()
    except Exception:  # noqa: BLE001
        pass
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except ImportError:
        _state["error"] = "SENTRY_DSN is set but sentry-sdk is not installed (pip install \"sentry-sdk[flask]\")"
        log.warning(_state["error"])
        return False
    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=(os.environ.get("SENTRY_ENVIRONMENT") or "desk").strip(),
            integrations=[FlaskIntegration()],
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            max_breadcrumbs=0,
            traces_sample_rate=0.0,
            before_send=scrub_event,
        )
    except Exception as exc:  # noqa: BLE001 - a bad DSN must not stop the desk
        _state["error"] = f"Sentry could not start: {type(exc).__name__}"
        log.warning(_state["error"])
        return False
    _state.update(enabled=True, error=None)
    return True


def status() -> dict[str, Any]:
    return {"enabled": _state["enabled"], "error": _state["error"]}
