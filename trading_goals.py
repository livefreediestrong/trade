"""Explicit, separate live/paper plans. Saving a goal cannot start trading or raise caps."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request
from fox_workspace import broker_view
from narrative_checks import number

PRESETS = {
    "live": [{"id": "observe", "label": "Readiness and review", "target": None},
             {"id": "one", "label": "$1 daily target + review", "target": 1},
             {"id": "five", "label": "$5 daily target + review", "target": 5},
             {"id": "ten", "label": "$10 daily target + review", "target": 10}],
    "paper": [{"id": "observe", "label": "Build an evidence sample", "target": None},
              {"id": "ten", "label": "$10 simulated target + evidence", "target": 10},
              {"id": "twenty_five", "label": "$25 simulated target + evidence", "target": 25},
              {"id": "fifty", "label": "$50 simulated target + evidence", "target": 50}],
}
FIELDS = {"live": "daily_profit_target_usd", "paper": "paper_profit_target_usd"}


def revision(cfg):
    return hashlib.sha256(json.dumps({k: cfg.get(k) for k in (*FIELDS.values(), "trading_goals")}, sort_keys=True).encode()).hexdigest()[:20]


def apply(cfg, body):
    if not isinstance(body, dict) or body.get("scope") not in FIELDS:
        raise ValueError("Choose a live or paper goal")
    scope = body["scope"]
    if body.get("preset") == "custom":
        raw = body.get("target")
        try:
            target = Decimal(str(raw))
        except (ValueError, InvalidOperation):
            raise ValueError("Target must be a finite dollar amount") from None
        if isinstance(raw, bool) or not target.is_finite() or target < 0 or target > 1000000000 or target != target.quantize(Decimal(".01")):
            raise ValueError("Use a nonnegative dollar target with at most two decimal places")
        target = float(target) or None
    else:
        preset = next((p for p in PRESETS[scope] if p["id"] == body.get("preset")), None)
        if preset is None:
            raise ValueError("Unknown goal preset")
        target = preset["target"]
    return {**cfg, FIELDS[scope]: target,
            "trading_goals": {**(cfg.get("trading_goals") or {}), scope: {"preset": body["preset"], "target": target,
                                "saved_at": datetime.now(timezone.utc).isoformat()}}}


def progress(target, pnl, source):
    target, pnl = number(target), number(pnl)
    target = target if target and target > 0 else None
    return {"target": target, "pnl": pnl, "source": source,
            "remaining": max(0, round(target-pnl, 2)) if target is not None and pnl is not None else None,
            "percent": max(0, min(100, round(pnl/target*100, 1))) if target is not None and pnl is not None else None,
            "hit": pnl >= target if target is not None and pnl is not None else None}


def snapshot(desk):
    cfg = desk.load_config()
    broker = broker_view(desk, cfg)
    ledger = desk.load_ledger()
    ledger_ok = not getattr(desk, "_CORRUPT_PATHS", set())
    paper_pnl = (desk.daily_stats(ledger) or {}).get("pnl") if ledger_ok else None
    companion = getattr(desk, "_research_companion", None)
    try:
        night = companion.after_close.status().get("latest") or {} if companion else {}
    except Exception:
        night = {}
    outcomes = (night.get("evidence") or {}).get("outcomes") or {}
    observations = number((outcomes.get("today") or {}).get("outcomes"))
    current = night.get("day") == desk._today_str()
    review_done = current and night.get("status") == "complete"
    live_steps = [{"label": "Current account and daily P&L verified", "complete": bool(broker["risk_ready"]), "detail": broker.get("error")},
                  {"label": "No unresolved broker submissions", "complete": not bool(ledger.get("pending_broker_orders")) if ledger_ok else None},
                  {"label": "Today's two-part after-close review completed", "complete": review_done}]
    paper_steps = [{"label": "20 qualified paper observations in a reviewed session", "complete": observations >= 20 if current and observations is not None else None,
                    "detail": f"{int(observations)} / 20 · {night.get('day')}" if current and observations is not None else "Awaiting today's qualified review; this is not a trade quota."},
                   {"label": "After-cost result evaluated", "complete": current and bool(outcomes.get("verdict")),
                    "detail": (outcomes.get("verdict") or {}).get("title") if current else "Awaiting today's review"},
                   {"label": "Today's two-part after-close review completed", "complete": review_done}]
    scopes = {}
    for scope, steps, pnl, source in (("live", live_steps, broker["day_pnl"], "Broker daily P&L, including open positions"),
                                      ("paper", paper_steps, paper_pnl, "Local simulation: realized daily P&L after recorded costs")):
        saved = (cfg.get("trading_goals") or {}).get(scope) or {}
        target = cfg.get(FIELDS[scope])
        scopes[scope] = {"presets": PRESETS[scope], "selected": saved.get("preset") if saved.get("target") == target else "custom",
                         "progress": progress(target, pnl, source), "milestones": steps}
    return {"ok": True, "as_of": datetime.now(timezone.utc).isoformat(), "day": desk._today_str(), "revision": revision(cfg), **scopes}


def register(app, desk):
    bp = Blueprint("trading_goals", __name__)

    @bp.get("/api/trading-goals")
    def get_goals():
        return jsonify(snapshot(desk))

    @bp.post("/api/trading-goals")
    def save_goal():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(ok=False, error="Expected a goal object"), 400
        with desk._lock:
            cfg = desk.load_config()
            if getattr(desk, "_CORRUPT_PATHS", set()):
                return jsonify(ok=False, error="Repair the unreadable desk data before saving a goal"), 409
            if body.get("revision") != revision(cfg):
                return jsonify(ok=False, error="Goals changed elsewhere. Refresh before saving."), 409
            try:
                updated = apply(cfg, body)
            except ValueError as exc:
                return jsonify(ok=False, error=str(exc)), 400
            desk.save_config(updated)
            desk.append_journal("trading_goal_set", {"scope": body["scope"], "preset": body["preset"], "target": updated[FIELDS[body["scope"]]]})
        return jsonify(snapshot(desk))

    app.register_blueprint(bp)
