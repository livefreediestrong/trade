"""User-directed stock tickets, reviewed through the existing broker pipeline."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import math
import re
import uuid

from flask import Blueprint, jsonify, request

import order_terms


def position_error(intent: str, held: float, shares: int, reserved: dict) -> str | None:
    if intent == "buy":
        return "This position is short; choose Buy to cover instead" if held < 0 else None
    if intent == "sell" and held <= 0:
        return "No long holding to sell; opening a short position is not supported"
    if intent == "cover" and held >= 0:
        return "No short holding to cover; this ticket cannot open a long position"
    side = "sell" if intent == "sell" else "buy"
    available = max(0.0, abs(held) - reserved[side + "_qty"])
    if shares > math.floor(available):
        return f"Only {math.floor(available)} whole shares remain available after working orders; review a smaller quantity"
    return None


def register(app, desk):
    bp = Blueprint("live_ticket", __name__)

    @bp.post("/api/live/ticket/review")
    def review():
        """Persists a user-authored draft and creates a review, never an order."""
        import broker_router as broker
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - {"ticker", "intent", "shares", "order"}:
            return jsonify(ok=False, error="Provide ticker, intent, shares and order terms only"), 400
        ticker = str(body.get("ticker") or "").strip().upper()
        intent = body.get("intent")
        if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?", ticker) or len(ticker) > 15:
            return jsonify(ok=False, error="Enter one US stock or ETF symbol"), 400
        if intent not in ("buy", "sell", "cover"):
            return jsonify(ok=False, error="Choose Buy shares, Sell shares I own, or Buy to cover"), 400
        if not isinstance(body.get("order"), dict) or body["order"].get("type") not in ("limit", "market"):
            return jsonify(ok=False, error="Choose Limit or Market explicitly"), 400
        side = "sell" if intent == "sell" else "buy"
        try:
            ticket = order_terms.canonical_order({"side": side, "suggested_shares": body.get("shares")}, body.get("order"))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        with desk._lock:
            cfg = copy.deepcopy(desk.load_config())
            if cfg.get("mode") != "live_manual":
                return jsonify(ok=False, error="Direct tickets require Approve each — live broker mode"), 409
            if desk._CORRUPT_PATHS or desk.load_ledger().get("pending_broker_orders"):
                return jsonify(ok=False, error="Resolve corrupt state or pending broker reconciliation first"), 409
        context = broker.verify_execution_context()
        identity = context.get("identity")
        if not context.get("ok") or not identity or identity != cfg.get("broker_identity"):
            return jsonify(ok=False, error="Verify the current broker account and manual mode before reviewing"), 409
        if identity.get("broker") != "ibkr":
            return jsonify(ok=False, error="Direct stock tickets are implemented for the IBKR adapter"), 400
        px = desk.fetch_last_price(ticker)
        with desk._MARKS_LOCK:
            quote = copy.deepcopy(desk._QUOTE_SNAPSHOTS.get(ticker) or {})
        if desk._finite_float(px) is None or px <= 0 or not quote:
            return jsonify(ok=False, error="A fresh verified market price is required; no order was sent"), 409
        sig = {"id": uuid.uuid4().hex, "ticker": ticker, "side": side, "status": "pending",
               "workspace": "live", "source": "manual_ticket", "origin": "user_directed",
               "mode_at_create": "live_manual", "suggested_shares": ticket["shares"],
               "signal_price": max(px, ticket.get("limit", px)), "quote": quote,
               "manual_order": {"intent": intent, "order": ticket},
               "reason": "User-directed stock ticket; not an AI recommendation",
               "created_at": datetime.now(timezone.utc).isoformat(),
               "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()}
        restriction = desk.signal_execution_block(sig, broker=True)
        if restriction:
            return jsonify(ok=False, error=restriction), 409
        day_pnl, equity, account_error = desk._broker_day_pnl()
        held, error = desk._broker_position_qty(ticker)
        if error or desk._finite_float(held) is None:
            return jsonify(ok=False, error=error or "Broker holding is unavailable"), 409
        reserved, error = desk._broker_working_reservations(ticker, px)
        if error:
            return jsonify(ok=False, error=error), 409
        error = position_error(intent, held, ticket["shares"], reserved)
        reducing = intent in ("sell", "cover")
        if error:
            return jsonify(ok=False, error=error), 409
        if account_error and not (reducing and day_pnl is None and equity is not None and equity > 0):
            return jsonify(ok=False, error=account_error), 409
        notional = ticket["shares"] * sig["signal_price"]
        with desk._lock:
            current = desk.load_config()
            error = desk._broker_authorization_error(cfg, identity, current)
            ledger = desk.load_ledger()
            if error or ledger.get("pending_broker_orders"):
                return jsonify(ok=False, error=error or "A broker order now requires reconciliation"), 409
            if not reducing:
                shares, notional, error = desk._cap_shares_for_broker(copy.deepcopy(sig), current, ledger,
                    equity_override=equity, allow_fetch=False,
                    existing_notional=max(held, 0) * px + reserved["buy_exposure"])
                if error or shares != ticket["shares"]:
                    return jsonify(ok=False, error=error or f"Current risk limits allow at most {shares} shares; edit and review again"), 409
            ok, error = desk._broker_session_gate(current, notional, reducing=reducing)
            if ok and not reducing:
                ok, error = desk._broker_risk_gate(current, ledger, day_pnl, equity)
            if not ok:
                return jsonify(ok=False, error=error), 409
            signals = desk.load_signals()
            if sum(s.get("source") == "manual_ticket" and s.get("status") == "pending" for s in signals) >= 25:
                return jsonify(ok=False, error="Too many open manual tickets; discard them or wait for expiry"), 429
            desk.save_signals([sig, *signals])
        try:
            response = desk._create_broker_review(sig["id"], {"order": body.get("order")})
        except Exception:
            response = (jsonify(ok=False, error="Broker review could not complete; create a fresh ticket"), 503)
        if isinstance(response, tuple):
            # A failed preview must not become a queue item that can be submitted later.
            with desk._lock:
                signals = desk.load_signals()
                for item in signals:
                    if item.get("id") == sig["id"] and item.get("status") == "pending":
                        item.update(status="rejected", reject_reason="Manual preview could not complete; create a new ticket")
                desk.save_signals(signals)
            return response
        payload = response.get_json()
        estimate = payload.get("costs") or {}
        fee = desk._finite_float(estimate.get("commission_estimate")) if estimate.get("currency") == "USD" else None
        cash = ticket["shares"] * (ticket.get("limit") or px)
        payload.update(signal_id=sig["id"], ticker=ticker, side=side, intent=intent, quote=quote,
            held_shares=held, available_whole_shares=math.floor(max(0, abs(held)-reserved[("sell" if held >= 0 else "buy")+"_qty"])),
            estimated_notional=round(cash, 2), estimated_cash_change=(round(cash-fee,2) if side=="sell" else -round(cash+fee,2)) if fee is not None else None,
            purpose="User-directed stock ticket", next_step="Confirm the exact ticker to submit this reviewed order")
        return jsonify(payload)

    app.register_blueprint(bp)
