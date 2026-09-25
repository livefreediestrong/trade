"""Fox's read-only view of real research and operating readiness."""
from datetime import datetime, timedelta, timezone
import time
import json
import hashlib
from flask import Blueprint, jsonify
import desk_backups
import market_events


def broker_view(desk, cfg):
    """Cached, identity-bound evidence only. Reading a page never connects to IBKR."""
    cached = getattr(desk, "_BROKER_BOOK_CACHE", {}) or {}
    book = cached.get("val") or {}
    age = max(0, time.monotonic()-float(cached.get("at") or 0))
    cache_key = cached.get("key") or ()
    fresh = age <= 30 and bool(cache_key) and cache_key[-1] == json.dumps(cfg.get("broker_identity"), sort_keys=True)
    return {"connected": fresh and bool(book.get("ok")),
            "risk_ready": fresh and bool(book.get("ok")) and bool(book.get("risk_ready")),
            "error": (book.get("risk_error") or book.get("error")) if fresh else "Current broker snapshot unavailable or older than 30 seconds. New orders require fresh account checks.",
            "day_pnl": book.get("day_pnl_usd") if fresh and book.get("risk_ready") else None,
            "age_sec": round(age)}


def register(app, desk):
    bp = Blueprint("fox_workspace", __name__)

    @bp.get("/api/fox-workspace")
    def snapshot():
        cfg = desk.load_config()
        agent = desk._live_agent.status()
        now = datetime.now(timezone.utc)
        today = now.astimezone(market_events.ET).date()
        events = market_events.events(cfg, now)
        days = []
        for offset in range(7):
            day = today+timedelta(days=offset)
            rows = [r for r in events if datetime.fromisoformat(r["start"]).astimezone(market_events.ET).date() == day]
            days.append({"date": day.isoformat(), "label": day.strftime("%a %b %d"),
                         "events": [{**r, "clock": "All day" if r.get("all_day") else market_events.clock_et(r["start"])} for r in rows]})
        return jsonify(ok=True, as_of=now.isoformat(), agent=agent,
                       review_scope=hashlib.sha256(json.dumps([cfg.get("broker_identity"), today.isoformat()], sort_keys=True).encode()).hexdigest()[:16],
                       brain=desk.llm_trader.status_public_extended(cfg), backups=desk_backups.status(desk),
                       calendar={"days": days, "status": market_events.status()},
                       broker=broker_view(desk, cfg))

    app.register_blueprint(bp)
