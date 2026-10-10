"""Durable broker-held stock-stop lifecycle, restricted to IBKR paper qualification."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_UP

WORKING = {"Submitted", "PreSubmitted"}
CANCELLED = {"Cancelled", "ApiCancelled"}
TRIGGER_EXITS = {"stop_loss", "breakeven_stop"}


def position_key(ticker, row):
    if not row.get("opened_at") or not row.get("account_scope"):
        raise ValueError("The original position ownership is missing")
    return hashlib.sha256(json.dumps([ticker, row["opened_at"], row["account_scope"]], sort_keys=True).encode()).hexdigest()


def stop_price(value, tick):
    if isinstance(value, bool) or isinstance(tick, bool):
        raise ValueError("Stop price and price step must be numbers")
    value, tick = Decimal(str(value)), Decimal(str(tick))
    if not value.is_finite() or not tick.is_finite() or value <= 0 or tick <= 0:
        raise ValueError("Stop price and broker price step are unavailable")
    return float((value / tick).to_integral_value(rounding=ROUND_UP) * tick)


def state_valid(records):
    if not isinstance(records, dict):
        return False
    for record in records.values():
        if (not isinstance(record, dict) or not re.fullmatch(r"tstop-[0-9a-f]{32}", str(record.get("ref")))
                or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("position_key")))
                or record.get("state") not in {"pending", "protected", "waiting", "review", "released"}):
            return False
        for flag in ("accounting_settled", "review_required"):
            if flag in record and type(record[flag]) is not bool:
                return False
        for key in ("accounted_qty", "verified_quantity"):
            if key in record and (type(record[key]) is not int or record[key] < 0):
                return False
        for key in ("held_anchor", "tracked_anchor"):
            if key in record and record[key] is not None:
                value = record[key]
                if (type(value) not in (float, int) or not Decimal(str(value)).is_finite()
                        or value < 0 or value != int(value)):
                    return False
        for key in ("accounting", "settlement_pending"):
            if key not in record:
                continue
            receipt = record[key]
            if (not isinstance(receipt, dict) or type(receipt.get("filled_qty")) is not int
                    or receipt["filled_qty"] < 0 or type(receipt.get("fees_reported")) is not bool
                    or not re.fullmatch(r"[0-9a-f]{64}", str(receipt.get("receipt")))
                    or not isinstance(receipt.get("execution_terms"), dict)
                    or not isinstance(receipt.get("execution_versions"), dict)
                    or not receipt["execution_versions"]
                    or any(not isinstance(k, str) or type(v) is not int or v <= 0
                           for k, v in receipt["execution_versions"].items())):
                return False
            content = {k: v for k, v in receipt.items() if k != "receipt"}
            if hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest() != receipt["receipt"]:
                return False
        if "accounted_qty" in record and record["accounted_qty"] != (record.get("accounting") or {}).get("filled_qty"):
            return False
        if record.get("accounting_settled") is True and (
                "accounted_qty" not in record or not record["accounting"].get("fees_reported")):
            return False
        if "reviewed_accounting_key" in record:
            reviews = record.get("correction_reviews")
            key = record["reviewed_accounting_key"]
            if (not re.fullmatch(r"[0-9a-f]{64}", str(key)) or not isinstance(reviews, list) or not reviews
                    or any(not isinstance(r, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(r.get("proof")))
                           or not re.fullmatch(r"[0-9a-f]{64}", str(r.get("accounting_key")))
                           or not r.get("at") for r in reviews)
                    or reviews[-1]["accounting_key"] != key):
                return False
        if record.get("accounting_settled") and (record.get("accounting") or {}).get("execution_correction"):
            import stop_fills
            if record.get("reviewed_accounting_key") != stop_fills.accounting_key(record["accounting"]):
                return False
        if "intent" in record:
            intent = record["intent"]
            if (not isinstance(intent, dict) or intent.get("action") not in {"create", "modify", "cancel"}
                    or not isinstance(intent.get("expected"), dict)
                    or type(intent.get("total_quantity")) is not int or "stop_price" not in intent):
                return False
    return True


def decision(record, view, quantity, price, reason=None):
    """Choose one transition from explicit broker observations; never infer absence as cancellation."""
    if type(quantity) is not int or quantity < 0:
        raise ValueError("A verified whole-share quantity is required")
    observed = view.get("order")
    result = lambda state, message, **extra: {"state": state, "message": message, **extra}
    if view["held"] < quantity:
        # Only an unaccounted stop execution explains the shortfall through its
        # own settlement. Otherwise the shares left outside this stop (manual or
        # external sale): track the smaller broker quantity and resize next check.
        accounted = (record or {}).get("accounted_qty", 0)
        if (not (observed and observed["filled"] > accounted)
                and not (record or {}).get("settlement_pending") and view["held"] >= 0):
            return result("shrink", "Broker holdings fell outside a recorded stop fill; tracking the smaller position",
                          held=int(view["held"]))
        return result("waiting", "Holdings changed; read the position again before acting")
    if record and not observed:
        return result("review", "The earlier stop is not visible; its outcome is unknown. No duplicate will be sent")
    if record and (record.get("review_required") or record.get("settlement_pending") or record.get("accounting_error")):
        return result("review", record.get("accounting_error") or "Stop accounting needs reconciliation or correction review")
    if observed and observed["filled"] and not (
            record and record.get("accounting_settled") is True and record.get("accounted_qty") == observed["filled"]):
        return result("review", "Stop fill detected; waiting for execution, fee and holdings reconciliation")
    if observed and observed["status"] == "Inactive":
        return result("review", "The broker rejected or deactivated the stop; review the broker message")
    if observed and observed["status"] == "Filled":
        if quantity == 0 and record and record.get("accounting_settled"):
            return result("released", "Stop fill, fees and holdings reconciled", allow_exit=True)
        return result("review", "The broker stop filled. Paper qualification pauses here for fill and trade-history review")
    if observed and observed["status"] in CANCELLED:
        intent = (record or {}).get("intent") or {}
        if intent.get("action") != "cancel":
            return result("review", "The stop was cancelled outside this exit request; review before replacing it")
        delta = max(0, observed["filled"] - intent["expected"]["order"]["filled"])
        if view["held"] > intent["expected"]["held"] - delta:
            return result("waiting", "A fill arrived during cancellation; waiting for holdings to catch up")
        if reason or quantity == 0:
            return result("released", "Broker confirmed cancellation; the normal exit gates may now run", allow_exit=True)
        return result("create", "The exit is no longer due; restore protection with a new stop")
    if observed and observed["status"] not in WORKING:
        return result("waiting", "Waiting for broker acceptance or confirmed cancellation")
    intent = (record or {}).get("intent") or {}
    if observed and intent.get("action") == "cancel":
        return result("waiting", "Cancellation is not confirmed; a second sell is blocked")
    if observed and intent.get("action") in ("create", "modify"):
        if observed["quantity"] != intent["total_quantity"] or observed["stop_price"] != intent["stop_price"]:
            return result("review", "The broker has not confirmed the requested stop terms; no automatic retry")
    if observed and (quantity == 0 or (reason and reason not in TRIGGER_EXITS)):
        return result("cancel", "Cancel this stop before a separate exit or ending its monitoring")
    if observed and reason in TRIGGER_EXITS:
        return result("waiting", "The stop price was reached; waiting for broker execution, with a second sell blocked")
    if quantity == 0 or (not observed and reason):
        return result("released", "No broker stop is outstanding for this exit", allow_exit=True)
    if quantity > view["available"]:
        return result("review", "Other sells or short calls already need these shares; combined-position review required")
    if not observed:
        return result("create", "Place the first broker-held stop for confirmed shares")
    target = max(price, observed["stop_price"])
    if observed["quantity"] - observed["filled"] != quantity or observed["stop_price"] != target:
        return result("modify", "Resize confirmed shares or tighten the existing stop; never loosen it", stop_price=target)
    return result("protected", "Broker reports the stop working; no second stop or app sell is needed")


def entry_error(raw, cfg, now=None):
    pending = pending_error(raw, cfg.get("broker_identity") or {})
    if pending:
        return pending

    if not (cfg.get("live_agent", {}).get("policy") or {}).get("broker_stops"):
        return None
    now = now or datetime.now(timezone.utc)
    for ticker, row in (raw.get("managed") or {}).items():
        record = (raw.get("native_stops") or {}).get(ticker) or {}
        try:
            age = (now - datetime.fromisoformat(record["checked_at"])).total_seconds()
            safe = (record["position_key"] == position_key(ticker, row)
                    and record["state"] == "protected" and 0 <= age <= 30
                    and record["verified_quantity"] >= row["shares"])
        except (KeyError, TypeError, ValueError):
            safe = False
        if not safe:
            return "Broker-held protection is unconfirmed; new entries wait while the stop is checked"
    return None


def pending_error(raw, identity):
    """All desk broker paths must respect an uncertain stop, including manual tickets."""
    scope = {k: identity.get(k) for k in ("broker", "account_id", "paper_mode")}
    records = list((raw.get("native_stops") or {}).values())
    records += [r for r in raw.get("native_stop_history") or [] if r.get("review_required") or r.get("settlement_pending")]
    for record in records:
        if record.get("account_scope") and record["account_scope"] != scope:
            continue
        if (record.get("state") not in {"released", "protected"} or record.get("review_required")
                or record.get("settlement_pending") or record.get("accounting_error")
                or ((record.get("last_observed") or {}).get("filled", 0) > 0 and not record.get("accounting_settled"))):
            return "Broker-held stop requests or fills need reconciliation before another desk order"
    return None


def sync(service, cfg, ticker, row, quantity, reason=None):
    """Persist intent before I/O under the same account submission lock as normal orders."""
    import broker_router as broker
    desk = service.desk
    identity = cfg.get("broker_identity") or {}
    if identity.get("broker") != "ibkr" or identity.get("paper_mode") is not True:
        return {"state": "review", "message": "Broker-held stop qualification requires an IBKR paper account"}
    expected_row = copy.deepcopy(row)

    def authorized():
        import paper_loop
        with desk._lock:
            current = desk.load_config()
            return (current == cfg and current.get("mode") == "auto_live"
                    and current.get("session_active") is True
                    and current.get("live_agent", {}).get("enabled") is True
                    and paper_loop.is_rth(datetime.now(timezone.utc))
                    and (service.load().get("managed") or {}).get(ticker) == expected_row)

    with desk._BROKER_SUBMIT_LOCK:
        if not authorized():
            return {"state": "waiting", "message": "Settings or the tracked position changed; check again"}
        with desk._lock:
            raw = service.load()
            record = copy.deepcopy((raw.get("native_stops") or {}).get(ticker))
        key = position_key(ticker, row)
        if record and record.get("position_key") != key:
            return {"state": "review", "message": "An earlier position still has a stop record; reconcile it first"}
        ref = record["ref"] if record else "tstop-" + uuid.uuid4().hex
        view_result = broker.observe_stock_stop(ticker, ref, identity)
        if not view_result.get("ok"):
            return {"state": "waiting", "message": view_result.get("error") or "Broker stop information unavailable"}
        view = view_result["view"]
        price = stop_price(row["stop"], view["min_tick"])
        plan = decision(record, view, quantity, price, reason)
        action = plan["state"]
        if action == "shrink":
            return plan  # The caller shrinks the tracked row; the next check resizes the stop.
        if action != "create" and record is None and view.get("order") is None:
            return plan  # No submission ever existed; do not create a phantom intent.
        stamp = datetime.now(timezone.utc)
        if action == "create" and record:
            ref = "tstop-" + uuid.uuid4().hex  # Only after broker-confirmed cancellation above.
        replacing = bool(action == "create" and record)
        saved = copy.deepcopy(record) if record and not replacing else {
                  "ref": ref, "position_key": key, "ticker": ticker,
                  "position_snapshot": copy.deepcopy(row), "held_anchor": view["held"], "tracked_anchor": row["shares"],
                  "account_scope": {k: identity[k] for k in ("broker", "account_id", "paper_mode")}}
        saved.update(state=action, message=plan["message"], checked_at=stamp.isoformat(),
                     verified_quantity=quantity if action == "protected" else 0,
                     last_observed=copy.deepcopy(view.get("order")))
        command = None
        if action in {"create", "modify", "cancel"}:
            total = quantity + ((view.get("order") or {}).get("filled", 0) if action == "modify" else 0)
            command = {"action": action, "ref": ref, "ticker": ticker,
                       "broker_identity": identity, "quantity": quantity, "total_quantity": total,
                       "stop_price": plan.get("stop_price", price), "expected": view,
                       "previous_ref": record["ref"] if record else ref,
                       "valid_until": (stamp + timedelta(seconds=20)).isoformat()}
            saved.update(ref=ref, intent=copy.deepcopy(command), state="pending", verified_quantity=0)
        elif action == "protected":
            saved.pop("intent", None)
        with desk._lock:
            if not authorized():
                return {"state": "waiting", "message": "Authorization changed before the stop request"}
            raw = service.load()
            if replacing:
                raw.setdefault("native_stop_history", []).insert(0, {"ticker": ticker, **copy.deepcopy(record)})
            raw.setdefault("native_stops", {})[ticker] = saved
            if command:
                day = raw["days"].setdefault(service.key(cfg), {"research": 0, "orders": 0})
                # Counted separately: protecting shares must not use up the
                # account's small daily entry allowance (effective_limits).
                day["protective_orders"] = int(day.get("protective_orders") or 0) + 1
                raw["attempts"]["native-" + uuid.uuid4().hex] = {"key": service.key(cfg), "revision": cfg["live_agent"]["revision"]}
            service.save(raw)  # Failure here must prevent every broker write.
        if command:
            # A throw or uncertain reply leaves the durable intent in place. Never resend it.
            try:
                result = broker.change_stock_stop(command, authorized)
            except Exception:
                result = {"ok": False, "error": "Stop request outcome is uncertain; reconciliation is required"}
            with desk._lock:
                raw = service.load()
                current = (raw.get("native_stops") or {}).get(ticker)
                if current and current.get("intent") == command:
                    current["last_request"] = {k: result.get(k) for k in ("ok", "submission_attempted", "error")}
                    if not result.get("ok"):
                        current.update(state="review", message=result.get("error") or "Stop request needs review")
                    service.save(raw)
            return {"state": "pending", "message": result.get("error") or "Stop request sent; waiting for independent broker confirmation"}
        return plan
