"""Read-only maintenance plan and dated local evidence; never a trading gate."""
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify
import code_version

EASTERN = ZoneInfo("America/New_York")
SOURCE = "https://www.interactivebrokers.com/en/software/systemStatus.php"
VERIFIED_ON = "2026-09-26"


def broker_window(now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Maintenance time must include a timezone")
    local = now.astimezone(EASTERN)
    def bounds(day):
        start, end = ((time(0), time(2)) if day.weekday() == 5 else (time(0, 15), time(1, 45)))
        # At the autumn repeated hour use the later end for this planning hint.
        return datetime.combine(day, start, EASTERN), datetime.combine(day, end, EASTERN).replace(fold=1)
    start, end = bounds(local.date())
    active = start.astimezone(timezone.utc) <= now.astimezone(timezone.utc) < end.astimezone(timezone.utc)
    if now.astimezone(timezone.utc) >= end.astimezone(timezone.utc):
        start, end = bounds(local.date() + timedelta(days=1))
    return {"within_published_window": active, "starts_at": start.isoformat(), "ends_at": end.isoformat(),
            "source": SOURCE, "verified_on": VERIFIED_ON,
            "schedule_needs_review": (local.date() - datetime.fromisoformat(VERIFIED_ON).date()).days > 90,
            "scope": "North American schedule, used as a planning assumption. Your assigned IBKR server region has not been verified.",
            "meaning": "This is a published maintenance window, not a live outage report or a reason to ignore a failed connection."}


def read_record(path):
    try:
        path = Path(path)
        if path.stat().st_size > 2_000_000:
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def recent(stamp, hours, now):
    try:
        parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return parsed.tzinfo is not None and 0 <= (now - parsed).total_seconds() <= hours * 3600
    except (TypeError, ValueError):
        return False


def snapshot(desk, now=None):
    import desk_backups
    now = now or datetime.now(timezone.utc)
    root = Path(desk.DATA_DIR) / "upkeep"
    watchdog = read_record(root / "watchdog.json").get("last") or {}
    daily = read_record(root / "last_daily.json")
    daily_tests = daily.get("tests") if isinstance(daily.get("tests"), dict) else {}
    tests = read_record(root / "last_tests.json")
    code = code_version.status()
    current = (bool(tests.get("code")) and tests.get("code") == code.get("serving", code.get("started"))
               and code.get("stale") is False and tests.get("test_code") == code_version.test_fingerprint())
    dated = recent(tests.get("at"), 24 * 365 * 10, now)
    test_status = ("passed" if tests["ok"] else "failed") if dated and isinstance(tests.get("ok"), bool) else "unknown"
    if test_status != "unknown" and (not current or tests.get("changed_during_run")):
        test_status = "older_" + test_status
    if not isinstance(watchdog, dict):
        watchdog = {}
    backup = desk_backups.status(desk)
    return {"checked_at": now.isoformat(), "broker_window": broker_window(now),
            "watchdog": {"at": watchdog.get("at"), "recent": recent(watchdog.get("at"), .25, now),
                         "action": watchdog.get("action")},
            "daily": {"at": daily.get("at"), "recent": recent(daily.get("at"), 36, now),
                      "problem_count": len(daily["problems"]) if isinstance(daily.get("problems"), list) else None,
                      "tests": daily_tests},
            "tests": {"status": test_status, "at": tests.get("at"), "code": tests.get("code"),
                      "summary": str(tests.get("summary") or "")[:500],
                      "details_file": str(tests.get("details_file") or "")[:200],
                      "last_run_note": str(daily_tests.get("skipped") or "")[:500],
                      "update_waiting": code.get("stale") is True,
                      "scope": "Local automated checks. A pass does not qualify live trading or broker recovery."},
            "backup": {**{k: backup.get(k) for k in ("ok", "verified_at", "stale", "files", "last_error")},
                       "recent": recent(backup.get("verified_at"), 36, now)},
            "boundary": "No trading, account, session or restart settings are changed by this view."}


def register(app, desk):
    bp = Blueprint("desk_maintenance", __name__)
    @bp.get("/api/maintenance")
    def status():
        return jsonify(snapshot(desk))
    app.register_blueprint(bp)
