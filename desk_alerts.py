"""
Desk push alerts — Waiting enqueue, goal hit, kill / session loss.

Channels:
  1. In-process queue → state.alerts / Advanced log + Simple toast (frontend)
  2. Browser Notification API (frontend; this module only flags events)
  3. Optional webhook ALERT_WEBHOOK_URL (Discord/Slack incoming) — POST JSON
  4. Twilio stub (TWILIO_*) behind ALERT_TWILIO=1 — never sends without keys

No secrets invented. Missing keys → channel skipped cleanly.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

_lock = threading.RLock()
_queue: deque[dict[str, Any]] = deque(maxlen=40)
_seen: dict[str, float] = {}
_DEDUP_SEC = 90.0


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


def _truthy(name: str, default: str = "0") -> bool:
    v = (os.environ.get(name, default) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


def webhook_url() -> str:
    return (os.environ.get("ALERT_WEBHOOK_URL") or "").strip()


def twilio_configured() -> bool:
    return bool(
        (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
        and (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
        and (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
        and (os.environ.get("TWILIO_TO_NUMBER") or "").strip()
    )


def public_status() -> dict[str, Any]:
    return {
        "provider": "desk_alerts",
        "browser_notification": True,  # frontend capability
        "webhook": bool(webhook_url()),
        "twilio": twilio_configured() and _truthy("ALERT_TWILIO", "0"),
        "twilio_keys_present": twilio_configured(),
        "twilio_enabled_flag": _truthy("ALERT_TWILIO", "0"),
        "note": "Twilio never sends unless ALERT_TWILIO=1 and TWILIO_* keys set.",
    }


def _dedupe(key: str) -> bool:
    now = time.time()
    with _lock:
        stale = [k for k, ts in _seen.items() if now - ts > _DEDUP_SEC * 2]
        for k in stale:
            _seen.pop(k, None)
        if key in _seen and (now - _seen[key]) < _DEDUP_SEC:
            return False
        _seen[key] = now
        return True


def _post_webhook(event: dict[str, Any]) -> dict[str, Any]:
    url = webhook_url()
    if not url:
        return {"ok": False, "status": "not_configured"}
    # Discord-friendly + generic Slack incoming
    content = event.get("message") or event.get("kind")
    body = {
        "content": content,
        "text": content,
        "username": "TomahawkDesk",
        "embeds": [
            {
                "title": event.get("kind"),
                "description": content,
                "timestamp": event.get("ts"),
            }
        ],
        "event": event,
    }
    try:
        r = requests.post(url, json=body, timeout=8)
        return {"ok": r.status_code < 300, "http": r.status_code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120]}


def _twilio_stub_send(event: dict[str, Any]) -> dict[str, Any]:
    """Optional SMS — only when ALERT_TWILIO=1 and keys present. No send otherwise."""
    if not _truthy("ALERT_TWILIO", "0"):
        return {"ok": False, "status": "flag_off"}
    if not twilio_configured():
        return {"ok": False, "status": "not_configured"}
    sid = (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
    token = (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
    from_n = (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    to_n = (os.environ.get("TWILIO_TO_NUMBER") or "").strip()
    body = f"[Tomahawk] {event.get('kind')}: {event.get('message')}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    try:
        r = requests.post(
            url,
            data={"From": from_n, "To": to_n, "Body": body[:500]},
            auth=(sid, token),
            timeout=12,
        )
        return {"ok": r.status_code < 300, "http": r.status_code, "status": "sent_or_accepted"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)[:120]}


def emit(
    kind: str,
    message: str,
    *,
    detail: dict[str, Any] | None = None,
    level: str = "info",
    dedupe_key: str | None = None,
) -> dict[str, Any]:
    """Enqueue alert for UI + optional outbound channels."""
    kind = str(kind or "info")
    message = str(message or kind)[:240]
    key = dedupe_key or f"{kind}|{message}"
    if not _dedupe(key):
        return {"ok": True, "deduped": True, "kind": kind}

    event = {
        "id": f"{int(time.time() * 1000)}-{kind}",
        "kind": kind,
        "message": message,
        "level": level,
        "detail": detail or {},
        "ts": datetime.now(timezone.utc).isoformat(),
        "browser_notification": kind in ("waiting_enqueue", "goal_hit", "kill", "session_loss", "max_loss", "bleed"),
    }
    channels: dict[str, Any] = {"queue": True}
    channels["webhook"] = _post_webhook(event)
    channels["twilio"] = _twilio_stub_send(event)

    with _lock:
        _queue.appendleft(event)
    return {"ok": True, "event": event, "channels": channels}


def recent(limit: int = 20) -> list[dict[str, Any]]:
    with _lock:
        return list(_queue)[: max(1, min(40, int(limit)))]


def drain_for_state(limit: int = 12) -> dict[str, Any]:
    return {
        "items": recent(limit),
        "providers": public_status(),
    }


# Convenience emitters used by app / paper_loop hooks
def alert_waiting_enqueue(ticker: str, side: str | None = None) -> dict[str, Any]:
    t = (ticker or "").upper()
    msg = f"Waiting: {t}" + (f" {side}" if side else "")
    return emit("waiting_enqueue", msg, detail={"ticker": t, "side": side}, level="info")


def alert_goal_hit(pnl: float | None = None) -> dict[str, Any]:
    extra = f" (PnL ${pnl:.0f})" if isinstance(pnl, (int, float)) else ""
    return emit("goal_hit", f"Goal hit — Auto paper paused{extra}", level="success")


def alert_kill_or_loss(kind: str = "kill", detail: dict | None = None) -> dict[str, Any]:
    k = "session_loss" if kind in ("max_loss", "session_loss") else "kill"
    msg = "Max loss hit — loop paused" if k == "session_loss" else "Kill switch — loop paused"
    return emit(k, msg, detail=detail or {}, level="error", dedupe_key=k)
