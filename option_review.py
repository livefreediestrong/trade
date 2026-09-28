"""Record an owner's external resolution only after fresh, flat broker evidence.

No guessed assignment accounting, exercise, liquidation or trading activation.
"""
import copy
import hashlib
import json
import time
import uuid
from datetime import timedelta

from flask import jsonify, request

import option_lifecycle as life


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def guard(service):
    cfg = copy.deepcopy(service.desk.load_config())
    if (cfg.get("broker_identity") or {}).get("paper_mode") is not True:
        raise ValueError("This option test review requires the confirmed paper account")
    if cfg.get("session_active") is not False or (cfg.get("live_agent") or {}).get("enabled") is not False:
        raise ValueError("For this option review, first pause Fox and end the session yourself")
    if service.cycle_lock.locked() or service.desk._CORRUPT_PATHS or service.desk.load_ledger().get("pending_broker_orders"):
        raise ValueError("A decision, unreadable file or unresolved order still needs attention")
    return cfg


def evidence(service, key):
    with service.desk._lock:
        cfg = guard(service)
    life.sync_ledger(service)
    with service.desk._lock:
        row = copy.deepcopy(service.load().get("option_positions", {}).get(key))
        if not row or row["account_scope"] != life.scope(cfg["broker_identity"]) or not row.get("hold"):
            raise ValueError("This option review is no longer pending for the displayed account")
    snap = life.snapshot(cfg["broker_identity"])
    # Full underlying must be flat. Mixed/new positions cannot be waved through.
    if any(p["symbol"] == row["ticker"] and p["qty"] for p in snap["positions"]):
        raise ValueError("Options or stock are still held on this underlying. Resolve them at the broker before recording closure")
    if any(o["symbol"] == row["ticker"] for o in snap["orders"]):
        raise ValueError("A working order still affects this underlying")
    with service.desk._lock:
        if guard(service) != cfg or service.load().get("option_positions", {}).get(key) != row:
            raise ValueError("Account, policy or option evidence changed during review")
    return cfg, row, dict(positions=snap["positions"], orders=snap["orders"])


def register(bp, desk):
    tokens = {}

    @bp.post("/api/live-agent/options/review-closed")
    def preview():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"id"} or not isinstance(body["id"], str):
            raise ValueError("Select one option discrepancy to review")
        service = desk._live_agent
        with desk._BROKER_SUBMIT_LOCK, desk._BROKER_EXEC_LOCK:
            cfg, row, snap = evidence(service, body["id"])
            token = str(uuid.uuid4())
            now = time.monotonic()
            for key in list(tokens):
                if tokens[key]["until"] <= now:
                    tokens.pop(key)
            while len(tokens) >= 20:
                tokens.pop(next(iter(tokens)))
            tokens[token] = dict(id=row["id"], digest=digest([cfg, row, snap]), until=now+90)
        return jsonify(ok=True, token=token, ticker=row["ticker"], recorded_contracts=row["remaining"],
                       expires_at=(life.now_utc()+timedelta(seconds=90)).isoformat(),
                       message="Broker now reports no options, stock or working orders for this underlying. Review its activity statement to identify the assignment, expiry or external trade. Saving records your review; it does not invent fills or realized P&L")

    @bp.post("/api/live-agent/options/accept-closed")
    def accept():
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or set(body) != {"token", "reference", "acknowledge"}
                or body.get("acknowledge") is not True or not isinstance(body.get("token"), str)
                or not isinstance(body.get("reference"), str) or not 3 <= len(body["reference"].strip()) <= 200):
            raise ValueError("Acknowledge the review and enter the broker activity date or reference (3–200 characters)")
        service = desk._live_agent
        with desk._BROKER_SUBMIT_LOCK, desk._BROKER_EXEC_LOCK:
            saved = tokens.pop(body["token"], None)
            if not saved or time.monotonic() >= saved["until"]:
                raise ValueError("Review expired or was already used; read the broker evidence again")
            cfg, row, snap = evidence(service, saved["id"])
            if saved["digest"] != digest([cfg, row, snap]) or time.monotonic() >= saved["until"]:
                raise ValueError("Broker evidence changed; review again")
            with desk._lock:
                if guard(service) != cfg:
                    raise ValueError("Configuration changed; review again")
                raw = service.load()
                if raw["option_positions"].get(row["id"]) != row:
                    raise ValueError("Option fills changed; review again")
                record = raw["option_positions"][row["id"]]
                record["external_resolution"] = dict(at=life.now_utc().isoformat(),
                    reference=body["reference"].strip(), receipt_key=life.receipt_key(row),
                    broker_flat_verified=True, realized_pnl=None, cause="Owner reviewed broker activity; cause not independently verified")
                record["hold"] = None
                life._event(record, "external_resolution", "Owner reviewed the discrepancy; fresh broker evidence is flat. Accounting remains unreconciled", reference=body["reference"].strip())
                service.save(raw)
        return jsonify(ok=True, message="Review recorded. Fox and the session remain paused. Broker fills and realized P&L were not altered")
