"""Reconcile paper broker-stop executions and fees without sending any orders."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def number(value, *, whole=False, positive=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("A broker number is missing")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("A broker number is invalid") from None
    if (not result.is_finite() or abs(result) >= Decimal("1e100")
            or (whole and (result < 0 or result != result.to_integral_value()))
            or (positive and result <= 0)):
        raise ValueError("A broker number is unverified")
    return result


def summarize(record, rows, observed):
    """Fold unique execution revisions, never sum both an original and correction."""
    scope = record["account_scope"]
    families, by_id = {}, {}
    now = datetime.now(timezone.utc)
    for row in rows:
        if (row.get("account_id") != scope["account_id"] or row.get("paper_mode") is not True
                or row.get("order_ref") != record["ref"]):
            continue
        match = re.fullmatch(r"(.+)\.(\d+)", str(row.get("execution_id") or ""))
        if (not match or int(match[2]) < 1 or row.get("verified") is not True
                or row.get("side") != "sell" or row.get("asset_type") != "STK" or row.get("currency") != "USD"
                or row.get("ticker") != record["ticker"] or row.get("con_id") != observed["con_id"]
                or not observed.get("permanent_id") or row.get("permanent_order_id") != observed["permanent_id"]):
            raise ValueError("Stop execution identity or contract is unverified")
        stamp = datetime.fromisoformat(str(row.get("ts") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp > now:
            raise ValueError("Stop execution time is unverified")
        qty = number(row["shares"], whole=True)
        if not qty and (int(match[2]) <= 1 or row.get("voided") is not True):
            raise ValueError("Zero shares require an explicit broker bust correction")
        number(row["price"], positive=bool(qty))
        number(row.get("cumulative_shares"), whole=True)
        exid = row["execution_id"]
        core = {k: row.get(k) for k in ("shares", "price", "cumulative_shares", "ts", "con_id", "permanent_order_id")}
        if exid in by_id and by_id[exid] != core:
            raise ValueError("Conflicting reports share an execution ID")
        prior_core = ((record.get("accounting") or {}).get("execution_terms") or {}).get(exid)
        if prior_core is not None and core != prior_core:
            raise ValueError("An execution changed without a new correction ID")
        by_id[exid] = core
        families.setdefault(match[1], {})[int(match[2])] = row
    if not families:
        raise ValueError("The broker has not supplied the stop execution details yet")
    latest = [family[max(family)] for family in families.values()]
    earliest = [family[min(family)] for family in families.values()]
    qty = sum((number(r["shares"], whole=True) for r in latest), Decimal(0))
    original_qty = sum((number(r["shares"], whole=True) for r in earliest), Decimal(0))
    reported = number(observed["filled"], whole=True)
    versions = {family: max(revisions) for family, revisions in families.items()}
    correction = any(v > 1 for v in versions.values())
    max_cumulative = max(number(r["cumulative_shares"], whole=True) for r in latest)
    original_cumulative = max(number(r["cumulative_shares"], whole=True) for r in earliest)
    if (qty != reported or qty != max_cumulative) and not (
            correction and original_qty == original_cumulative and reported in (original_qty, qty)):
        raise ValueError("Stop execution history is incomplete or does not match the broker quantity")
    if observed["status"] == "Filled" and not correction and qty != number(observed["quantity"], whole=True):
        raise ValueError("Filled stop is missing some executions")
    if any(r.get("pending_price_revision") for r in latest):
        raise ValueError("The broker marks a stop price as provisional")
    prior_versions = (record.get("accounting") or {}).get("execution_versions") or {}
    if any(versions.get(k, 0) < v for k, v in prior_versions.items()):
        raise ValueError("An older execution snapshot cannot replace a correction")
    fees, complete_fees = {}, True
    for row in latest:
        fee, unit = row.get("commission"), row.get("commission_currency")
        if fee is None or not isinstance(unit, str) or not re.fullmatch(r"[A-Z]{3}", unit):
            complete_fees = False
            continue
        fees[unit] = fees.get(unit, Decimal(0)) + number(fee)
    price = sum((number(r["shares"], whole=True) * number(r["price"]) for r in latest), Decimal(0)) / qty if qty else Decimal(0)
    usd = float(fees.get("USD", Decimal(0))) if complete_fees and set(fees) <= {"USD"} else None
    terminal = observed["status"] in {"Filled", "Cancelled", "ApiCancelled", "Inactive"}
    first = min(latest, key=lambda r: datetime.fromisoformat(r["ts"].replace("Z", "+00:00")))
    result = {"filled_qty": int(qty), "filled_avg_price": float(price), "filled_at": first["ts"],
              "fee_usd": usd, "fees_reported": complete_fees and usd is not None,
              "fees_by_currency": {k: float(v) for k, v in fees.items()},
              "execution_versions": versions, "execution_correction": correction,
              "execution_ids": sorted(r["execution_id"] for r in latest), "execution_terms": by_id,
              "execution_details_verified": True, "terminal": terminal,
              "state": "filled" if terminal else "partially_filled", "broker_status": observed["status"].lower()}
    result["receipt"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def _find(raw, ref):
    for record in (raw.get("native_stops") or {}).values():
        if record.get("ref") == ref:
            return record, False
    for record in raw.get("native_stop_history") or []:
        if record.get("ref") == ref:
            return record, True
    return None, False


TRACKING_ERROR = "Broker corrected a stop execution; review the position before automatic exits resume"


def accounting_key(accounting):
    """Bind approval to economic facts, not changing order-status timestamps."""
    keys = ("filled_qty", "filled_avg_price", "fee_usd", "fees_reported", "fees_by_currency",
            "execution_versions", "execution_ids", "execution_terms")
    return hashlib.sha256(json.dumps({k: accounting.get(k) for k in keys}, sort_keys=True).encode()).hexdigest()


def gate_error(service, raw, identity):
    """A new process must read execution history before trusting saved protection."""
    scope = {k: identity.get(k) for k in ("broker", "account_id", "paper_mode")}
    records = list((raw.get("native_stops") or {}).values()) + list(raw.get("native_stop_history") or [])
    if not any(r.get("account_scope") == scope for r in records):
        return None
    if getattr(service, "stop_accounting_error", None):
        return service.stop_accounting_error
    if getattr(service, "stop_accounting_identity", None) != identity:
        return "Broker stop history needs a fresh check after restart or account change; desk orders wait"
    return None


def _hold(service, ref, message):
    with service.desk._lock:
        raw = service.load()
        current, historical = _find(raw, ref)
        if current:
            current.update(state="review", message=message, accounting_error=message)
            if historical:
                current["review_required"] = True
            service.save(raw)


def _settle(service, identity, record, view, accounting):
    """Write ahead, project the idempotent ledger row, then advance managed shares."""
    import stock_protection
    desk, ref, ticker = service.desk, record["ref"], record["ticker"]
    with desk._lock:
        if desk.load_config().get("broker_identity") != identity:
            return
        raw = service.load()
        current, historical = _find(raw, ref)
        if current != record:
            return  # A fresher observation owns the next reconciliation pass.
        current["settlement_pending"] = copy.deepcopy(accounting)
        service.save(raw)  # No ledger mutation if this durable write fails.
        signal = {"id": ref, "ticker": ticker, "side": "sell", "source": "broker_stop",
                  "signal_price": record.get("position_snapshot", {}).get("stop", 0),
                  "suggested_shares": accounting["filled_qty"], "review_identity": identity}
        response = {"broker": "ibkr", "order_id": identity["account_id"]+":stop:"+ref,
                    "permanent_order_id": identity["account_id"]+":perm:"+str(view["order"]["permanent_id"]),
                    "paper_mode": True, "status": "paper_submitted", "endpoint": identity.get("endpoint"),
                    "order": {"broker_identity": identity}}
        fill, _ = desk._apply_broker_update(signal, response, accounting, "broker_stop", resting_stop=True)
        if accounting["filled_qty"] and (not fill or fill.get("shares") != accounting["filled_qty"]):
            raise ValueError("Stop fill could not be projected into the broker ledger")
        # This save commits the applied cumulative quantity and managed position
        # together. A crash before it simply repeats the same ledger upsert.
        raw = service.load()
        current, historical = _find(raw, ref)
        if not current or current.get("settlement_pending") != accounting:
            raise ValueError("Stop settlement changed while saving the ledger")
        previous = current.get("accounted_qty", 0)
        delta = accounting["filled_qty"] - previous
        managed = raw.setdefault("managed", {})
        row = managed.get(ticker)
        owns_row = row is not None and stock_protection.position_key(ticker, row) == record["position_key"]
        approved = current.get("reviewed_accounting_key") == accounting_key(accounting)
        correction = (accounting["execution_correction"] and not approved) or delta < 0
        review = bool(current.get("review_required") or correction)
        if correction and not current.get("review_required"):
            current["correction_before"] = copy.deepcopy(current.get("accounting"))
        if row is None and delta < 0 and record.get("position_snapshot"):
            row = copy.deepcopy(record["position_snapshot"])
            row["shares"] = 0
            managed[ticker] = row
            owns_row = True
        if not owns_row and (delta or not historical):
            review = True  # Never merge a corrected old position into a new one.
        old_shares = float(row["shares"]) if owns_row else 0
        remaining = old_shares - delta if owns_row else 0
        if remaining < 0:
            review = True
            remaining = old_shares  # No invented negative position.
        anchor_held, anchor_shares = current.get("held_anchor"), current.get("tracked_anchor")
        if anchor_held is None or anchor_shares is None:
            review, expected_held = True, None
        else:
            expected_held = anchor_held + old_shares - anchor_shares - delta
            if expected_held < 0:
                review, expected_held = True, None
        holdings_match = expected_held is not None and view["held"] == expected_held and view["held"] >= remaining
        if owns_row:
            row["shares"] = remaining
            if review and (not row.get("tracking_error") or row.get("tracking_error") == TRACKING_ERROR):
                row["tracking_error"] = TRACKING_ERROR
                row["tracking_error_ref"] = ref
        safe = holdings_match and accounting["fees_reported"] and not review
        known_end = view["order"]["status"] == "Filled" or (
            view["order"]["status"] in {"Cancelled", "ApiCancelled"}
            and (record.get("intent") or {}).get("action") == "cancel")
        terminal_safe = safe and known_end
        current.update(accounting=copy.deepcopy(accounting), accounted_qty=accounting["filled_qty"],
                       accounting_settled=safe, review_required=review,
                       last_observed=copy.deepcopy(view["order"]), checked_at=datetime.now(timezone.utc).isoformat(),
                       verified_quantity=int(remaining) if safe else 0,
                       held_anchor=expected_held, tracked_anchor=remaining)
        current.pop("settlement_pending", None)
        current.pop("accounting_error", None)
        if review:
            current.update(state="review", message="Broker execution correction recorded; holdings need review before trading resumes")
        elif not accounting["fees_reported"]:
            current.update(state="review", message="Stop fill recorded; waiting for complete fees in USD")
        elif not holdings_match:
            current.update(state="waiting", message="Stop fill recorded; waiting for broker holdings to match")
        elif terminal_safe:
            current.update(state="released", message="Stop execution, fees and holdings reconciled")
        else:
            # Accounting success does not prove that the remaining order is
            # accepted, correctly sized/priced or safe to replace after a cancel.
            desired = stock_protection.stop_price((row or record["position_snapshot"])["stop"], view["min_tick"])
            protection = stock_protection.decision(current, view, int(remaining), desired)
            current.update(state=protection["state"] if protection["state"] in {"protected", "review"} else "waiting",
                           message=protection["message"])
            if current["state"] != "protected":
                current["verified_quantity"] = 0
        if current.get("last_receipt") != accounting["receipt"]:
            current["last_receipt"] = accounting["receipt"]
            raw["events"].insert(0, {"at": datetime.now(timezone.utc).isoformat(), "ticker": ticker,
                                   "status": "broker_stop_reconciled", "message": current["message"]})
            raw["events"] = raw["events"][:200]
        if terminal_safe and remaining == 0 and owns_row:
            managed.pop(ticker, None)
            service._archive_released_stop(raw, ticker)
        service.save(raw)


def reconcile(service, *, force=False, lock_held=False):
    try:
        _reconcile(service, force=force, lock_held=lock_held)
    except Exception:
        service.stop_accounting_error = "Stop accounting could not be saved or verified; desk orders wait for recovery"
        raise


def _reconcile(service, *, force=False, lock_held=False):
    """Read/record only, including while Fox is paused. Never enable a policy."""
    import broker_router as broker
    desk = service.desk
    with desk._lock:
        cfg, raw = desk.load_config(), service.load()
        identity = cfg.get("broker_identity") or {}
        scope = {k: identity.get(k) for k in ("broker", "account_id", "paper_mode")}
        records = [copy.deepcopy(r) for r in list((raw.get("native_stops") or {}).values())
                   + list(raw.get("native_stop_history") or []) if r.get("account_scope") == scope]
    if identity.get("broker") != "ibkr" or identity.get("paper_mode") is not True or not records:
        return
    if not force and time.monotonic() < getattr(service, "_stop_reconcile_after", 0):
        return
    service._stop_reconcile_after = time.monotonic() + 5
    with nullcontext() if lock_held else desk._BROKER_SUBMIT_LOCK:
        response = broker.execution_history(expected_identity=identity)
        if response.get("ok") is not True or response.get("identity") != identity:
            service.stop_accounting_error = "Broker stop execution history is unavailable; desk orders wait for a verified read"
            return  # Existing unknown fills remain blocked; no invented evidence.
        journal = desk._research_companion.trades
        journal.merge(response)
        with journal.lock:
            rows = copy.deepcopy(journal.load()["executions"])
        for record in records:
            candidates = [r for r in rows if r.get("order_ref") == record["ref"]
                          and r.get("account_id") == identity["account_id"] and r.get("paper_mode") is True]
            if not candidates:
                continue
            # A historical broker order may no longer be returned after its
            # session/history window. Only a changed receipt needs another read.
            with desk._lock:
                current, historical = _find(service.load(), record["ref"])
            if historical and not record.get("review_required"):
                try:
                    same = summarize(record, candidates, record["last_observed"])
                    if same["receipt"] == record.get("last_receipt"):
                        continue
                except (ValueError, TypeError, KeyError):
                    pass
            try:
                observation = broker.observe_stock_stop(record["ticker"], record["ref"], identity)
                if not observation.get("ok") or not observation["view"].get("order"):
                    raise ValueError("Stop executions arrived but broker order evidence is unavailable")
                view = observation["view"]
                accounting = summarize(record, candidates, view["order"])
                _settle(service, identity, record, view, accounting)
            except (ValueError, TypeError, KeyError) as exc:
                _hold(service, record["ref"], str(exc)[:240])
        service.stop_accounting_error = None
        service.stop_accounting_identity = copy.deepcopy(identity)
