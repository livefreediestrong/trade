"""Explicit paper-stop correction review. No order, cancellation or activation path."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

import stock_protection
import stop_fills

TTL = 90


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def pending(raw, identity):
    scope = {k: (identity or {}).get(k) for k in ("broker", "account_id", "paper_mode")}
    records = list((raw.get("native_stops") or {}).values()) + list(raw.get("native_stop_history") or [])
    return [{"ref": r["ref"], "ticker": r.get("ticker", "Stock"),
             "key": digest([r.get("accounting"), r.get("accounting_error"), r.get("last_observed")]),
             "message": r.get("message") or "A broker correction needs review"}
            for r in records if r.get("account_scope") == scope and r.get("review_required")]


def _guard(service):
    desk = service.desk
    cfg = copy.deepcopy(desk.load_config())
    identity = cfg.get("broker_identity") or {}
    if identity.get("broker") != "ibkr" or identity.get("paper_mode") is not True:
        raise ValueError("Correction review is available only for the verified IBKR paper account")
    if cfg.get("session_active") is not False or (cfg.get("live_agent") or {}).get("enabled") is not False:
        raise ValueError("Pause Fox and end the trading session before reviewing a correction")
    if service.cycle_lock.locked():
        raise ValueError("Fox is finishing a decision; wait before reviewing this correction")
    if desk._CORRUPT_PATHS or desk.load_ledger().get("pending_broker_orders"):
        raise ValueError("Resolve unreadable data or outstanding broker submissions first")
    return cfg, identity


def _snapshot(service, ref):
    """Caller owns submission/config locks. Recheck actual evidence on every action."""
    import broker_router as broker
    desk = service.desk
    with desk._lock:
        cfg, identity = _guard(service)
    stop_fills.reconcile(service, force=True, lock_held=True)
    with desk._lock:
        raw = service.load()
        error = stop_fills.gate_error(service, raw, identity)
        if error:
            raise ValueError(error)
        record, historical = stop_fills._find(raw, ref)
        scope = {k: identity[k] for k in ("broker", "account_id", "paper_mode")}
        if not record or record.get("account_scope") != scope or not record.get("review_required"):
            raise ValueError("This correction is no longer pending in the selected paper account; refresh the list")
        record = copy.deepcopy(record)
    observed = broker.observe_stock_stop(record["ticker"], ref, identity)
    if observed.get("ok") is not True or not (observed.get("view") or {}).get("order"):
        raise ValueError("The broker order is unavailable; the correction stays blocked")
    view = observed["view"]
    with desk._lock:
        current_cfg, _ = _guard(service)
        raw = service.load()
        current, current_historical = stop_fills._find(raw, ref)
        if current_cfg != cfg or current != record or current_historical != historical:
            raise ValueError("The account, policy or correction changed during the check; review again")
        accounting = record.get("accounting") or {}
        row = copy.deepcopy((raw.get("managed") or {}).get(record["ticker"]))
        owns = row is not None and stock_protection.position_key(record["ticker"], row) == record["position_key"]
        remaining = row["shares"] if owns else record.get("tracked_anchor")
        issues = []
        if record.get("settlement_pending") or record.get("accounting_error"):
            issues.append(record.get("accounting_error") or "The interrupted accounting update has not finished")
        if not accounting.get("execution_correction"):
            issues.append("This hold is not a verified broker execution correction")
        if accounting.get("fees_reported") is not True:
            issues.append("Complete fees in USD are still missing")
        if row is not None and not owns:
            issues.append("A newer or different position uses this stock; its shares cannot be changed by this review")
        if not owns and remaining != 0:
            issues.append("The original remaining position cannot be verified")
        if owns and row.get("tracking_error") and (row["tracking_error"] != stop_fills.TRACKING_ERROR
                or row.get("tracking_error_ref", ref) != ref):
            issues.append("The position has another tracking problem that this review cannot clear")
        if (remaining is None or view["held"] != record.get("held_anchor")
                or view["held"] < remaining or remaining != record.get("tracked_anchor")):
            issues.append("Broker holdings and Fox's remaining shares do not agree")
        if record.get("accounted_qty") != view["order"]["filled"]:
            issues.append("The broker order's filled-share count has not caught up with the correction")
        if accounting.get("receipt") != record.get("last_receipt"):
            issues.append("The latest accounting update is not committed")
        ledger = desk.load_ledger()
        fill = next((f for f in ledger.get("broker_fills", []) if f.get("native_stop_ref") == ref
                     and f.get("order_id") == identity["account_id"]+":stop:"+ref), None)
        if not fill or any(fill.get(k) != accounting.get(v) for k, v in (
                ("shares", "filled_qty"), ("price", "filled_avg_price"), ("fee_usd", "fee_usd"),
                ("execution_versions", "execution_versions"), ("fees_reported", "fees_reported"))):
            issues.append("The saved trade record does not match the corrected executions")
        probe = copy.deepcopy(record)
        probe.update(review_required=False, accounting_settled=True)
        probe.pop("settlement_pending", None)
        probe.pop("accounting_error", None)
        plan = {"state": "review", "message": "Position needs review"}
        if remaining is not None and type(remaining) in (int, float) and int(remaining) == remaining and remaining >= 0:
            price = stock_protection.stop_price((row or record["position_snapshot"])["stop"], view["min_tick"])
            plan = stock_protection.decision(probe, view, int(remaining), price)
        if plan["state"] not in {"protected", "released"} or (plan["state"] == "released" and remaining != 0):
            issues.append(plan["message"])
        evidence = {"identity": identity, "config": cfg, "ref": ref, "historical": historical,
                    "accounting_key": stop_fills.accounting_key(accounting), "before": record.get("correction_before"),
                    "view": view, "position": row, "position_key": record["position_key"],
                    "anchors": [record.get("held_anchor"), record.get("tracked_anchor")],
                    "ledger": fill, "plan": plan, "issues": issues}
        summary = lambda a: {"shares": (a or {}).get("filled_qty"), "price": (a or {}).get("filled_avg_price"),
                             "fees": (a or {}).get("fee_usd"), "executions": (a or {}).get("execution_ids", [])}
        public = {"ref": ref, "ticker": record["ticker"], "account": "IBKR paper …"+str(identity["account_id"])[-4:],
                  "before": summary(record.get("correction_before")), "after": summary(accounting),
                  "remaining": remaining, "held": view["held"], "stop_status": view["order"]["status"],
                  "checked_at": datetime.now(timezone.utc).isoformat(), "issues": issues,
                  "can_accept": not issues, "outcome": "Correction reviewed; trading stays paused. Other trading checks still apply."}
        return evidence, public


def prepare(service, ref):
    if not isinstance(ref, str) or not re.fullmatch(r"tstop-[0-9a-f]{32}", ref):
        raise ValueError("Choose a saved broker correction")
    desk = service.desk
    with desk._BROKER_SUBMIT_LOCK, desk._BROKER_EXEC_LOCK:
        evidence, public = _snapshot(service, ref)
        with desk._lock:
            now = time.monotonic()
            tokens = getattr(service, "stop_review_tokens", {})
            service.stop_review_tokens = {k: v for k, v in tokens.items() if v["expires"] > now and v["ref"] != ref}
            if public["can_accept"]:
                if len(service.stop_review_tokens) >= 20:
                    raise ValueError("Too many open correction reviews; let an earlier review expire")
                token = uuid.uuid4().hex
                service.stop_review_tokens[token] = {"ref": ref, "proof": digest(evidence), "expires": now+TTL}
                public.update(token=token, expires_at=(datetime.now(timezone.utc)+timedelta(seconds=TTL)).isoformat())
        return {"ok": True, "review": public}


def accept(service, token, acknowledge):
    if acknowledge is not True or not isinstance(token, str):
        raise ValueError("Read the changed shares, price and fees, then confirm this exact review")
    desk = service.desk
    with desk._BROKER_SUBMIT_LOCK, desk._BROKER_EXEC_LOCK:
        with desk._lock:
            ticket = getattr(service, "stop_review_tokens", {}).pop(token, None)
            if not ticket or ticket["expires"] <= time.monotonic():
                raise ValueError("This review expired, was already used, or the desk restarted; check the correction again")
        evidence, public = _snapshot(service, ticket["ref"])
        if not public["can_accept"] or digest(evidence) != ticket["proof"]:
            raise ValueError("The correction, holdings, policy or stop changed; nothing was acknowledged. Check again")
        with desk._lock:
            current_cfg, _ = _guard(service)
            raw = service.load()
            record, historical = stop_fills._find(raw, ticket["ref"])
            if (current_cfg != evidence["config"] or not record
                    or stop_fills.accounting_key(record.get("accounting") or {}) != evidence["accounting_key"]
                    or (raw.get("managed") or {}).get(record["ticker"]) != evidence["position"]):
                raise ValueError("The saved account or position changed before acknowledgement; check again")
            # All writers use the same locks; the final atomic save contains the
            # acknowledgement and the narrowly owned tracking-error removal.
            stamp = datetime.now(timezone.utc).isoformat()
            record.update(reviewed_accounting_key=evidence["accounting_key"], review_required=False,
                          accounting_settled=True, state=evidence["plan"]["state"],
                          verified_quantity=int(public["remaining"]), message=public["outcome"], checked_at=stamp)
            record.setdefault("correction_reviews", []).append({"at": stamp, "proof": ticket["proof"],
                "accounting_key": evidence["accounting_key"], "before": public["before"], "after": public["after"],
                "remaining": public["remaining"], "held": public["held"], "state": evidence["plan"]["state"]})
            row = (raw.get("managed") or {}).get(record["ticker"])
            if row and stock_protection.position_key(record["ticker"], row) == record["position_key"]:
                if row.get("tracking_error") == stop_fills.TRACKING_ERROR and row.get("tracking_error_ref", record["ref"]) == record["ref"]:
                    row.pop("tracking_error", None)
                    row.pop("tracking_error_ref", None)
                if public["remaining"] == 0 and record["state"] == "released":
                    raw["managed"].pop(record["ticker"])
            if not historical and public["remaining"] == 0 and record["state"] == "released":
                service._archive_released_stop(raw, record["ticker"])
            raw["events"].insert(0, {"at": stamp, "ticker": record["ticker"], "status": "stop_correction_reviewed", "message": public["outcome"]})
            raw["events"] = raw["events"][:200]
            service.save(raw)
        return {"ok": True, "message": public["outcome"], "ref": ticket["ref"]}


def register(bp, desk):
    def guarded(action):
        try:
            return jsonify(action())
        except (OSError, RuntimeError):
            return jsonify(ok=False, error="The review could not be completed or saved. Trading stays paused; check the correction again."), 503

    @bp.post("/api/live-agent/corrections/review")
    def review():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"ref"}:
            raise ValueError("Choose one correction to check")
        return guarded(lambda: prepare(desk._live_agent, body["ref"]))

    @bp.post("/api/live-agent/corrections/accept")
    def confirm():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"token", "acknowledge"}:
            raise ValueError("Confirm only the correction shown in the review")
        return guarded(lambda: accept(desk._live_agent, body["token"], body["acknowledge"]))
