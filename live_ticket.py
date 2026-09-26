"""User-directed stock tickets, reviewed through the existing broker pipeline."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import math
import re
import time
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
        created = datetime.now(timezone.utc)
        sig = {"id": uuid.uuid4().hex, "ticker": ticker, "side": side, "status": "pending",
               "workspace": "live", "source": "manual_ticket", "origin": "user_directed",
               "mode_at_create": "live_manual", "suggested_shares": ticket["shares"],
               "signal_price": max(px, ticket.get("limit", px)), "quote": quote,
               "manual_order": {"intent": intent, "order": ticket},
               "reason": "User-directed stock ticket; not an AI recommendation",
               "ts": created.isoformat(), "created_at": created.isoformat(),
               "expires_at": (created + timedelta(seconds=90)).isoformat()}
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


    @bp.post("/api/live/ticket/option/review")
    def option_review():
        """Preview live options (BTO/STC/STO/BTC). Never places an order."""
        import broker_router as broker
        import order_terms
        body = request.get_json(silent=True)
        allowed = {"ticker", "intent", "contracts", "right", "expiry", "strike", "order", "allow_naked", "covered"}
        if not isinstance(body, dict) or set(body) - allowed:
            return jsonify(ok=False, error="Provide ticker, intent, contracts, right, expiry, strike and order terms only"), 400
        ticker = str(body.get("ticker") or "").strip().upper()
        intent = str(body.get("intent") or "").upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?", ticker) or len(ticker) > 15:
            return jsonify(ok=False, error="Enter one US stock or ETF underlying symbol"), 400
        if intent not in ("BTO", "STC", "STO", "BTC"):
            return jsonify(ok=False, error="Live options support BTO, STC, STO and BTC"), 400
        allow_naked = bool(body.get("allow_naked") or (body.get("order") or {}).get("allow_naked"))
        covered = bool(body.get("covered") or (body.get("order") or {}).get("covered"))
        if intent == "STO" and not (allow_naked or covered):
            return jsonify(ok=False, error="STO requires covered=true (long shares for short calls) or allow_naked=true"), 400
        if not isinstance(body.get("order"), dict) or body["order"].get("type") not in ("limit", "market"):
            return jsonify(ok=False, error="Choose Limit or Market explicitly"), 400
        side = "buy" if intent in ("BTO", "BTC") else "sell"
        try:
            ticket = order_terms.canonical_option_order(
                {"side": side, "suggested_shares": body.get("contracts"), "contracts": body.get("contracts")},
                {**body["order"], "right": body.get("right"), "expiry": body.get("expiry"),
                 "strike": body.get("strike"), "intent": intent,
                 "allow_naked": allow_naked, "covered": covered},
            )
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
            return jsonify(ok=False, error="Live option tickets are implemented for the IBKR adapter only"), 400
        action = "BUY" if intent in ("BTO", "BTC") else "SELL"
        quotes = broker.option_quotes([{
            "symbol": ticker, "right": ticket["right"], "expiry": ticket["expiry"],
            "strike": ticket["strike"], "action": action,
        }])
        if not quotes.get("ok") or not (quotes.get("legs") or []):
            return jsonify(ok=False, error=(quotes.get("error") or "Option quote unavailable; no order was sent")), 409
        leg = quotes["legs"][0]
        if not leg.get("con_id") or int(leg.get("multiplier") or 0) != 100:
            return jsonify(ok=False, error="Only standard 100-share US equity options can be reviewed"), 409
        premium = desk._finite_float(ticket.get("limit") if ticket["type"] == "limit" else (leg.get("ask") if intent in ("BTO", "BTC") else leg.get("bid")))
        if premium is None or premium <= 0:
            return jsonify(ok=False, error="A verified option premium is required; no order was sent"), 409
        try:
            notional = order_terms.option_notional(ticket["contracts"], premium, 100)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400
        local_symbol = str(leg.get("local_symbol") or "")
        if not local_symbol:
            return jsonify(ok=False, error="Broker did not return a local option symbol for acknowledgement"), 409
        created = datetime.now(timezone.utc)
        quote = {
            "price": premium, "source": quotes.get("source") or "IBKR option quote",
            "market_time": quotes.get("received_at") or created.isoformat(),
            "bid": leg.get("bid"), "ask": leg.get("ask"), "local_symbol": local_symbol,
            "con_id": int(leg["con_id"]), "multiplier": 100,
        }
        contract = {
            "con_id": int(leg["con_id"]), "symbol": ticker, "local_symbol": local_symbol,
            "right": ticket["right"], "expiry": ticket["expiry"], "strike": float(ticket["strike"]),
            "multiplier": 100, "currency": "USD", "sec_type": "OPT",
        }
        reducing = intent in ("STC", "BTC")
        held_contracts = 0.0
        if intent in ("STC", "BTC"):
            pos = broker.get_option_positions()
            if not pos.get("ok"):
                return jsonify(ok=False, error=pos.get("error") or "Option holdings unavailable"), 409
            for row in pos.get("positions") or []:
                if int(row.get("con_id") or 0) == int(contract["con_id"]):
                    held_contracts = float(row.get("qty") or 0)
                    break
            if intent == "STC":
                if held_contracts <= 0:
                    return jsonify(ok=False, error="No long option holding for this contract; STC refused"), 409
                if ticket["contracts"] > math.floor(held_contracts):
                    return jsonify(ok=False, error=f"Only {math.floor(held_contracts)} contracts held for this OCC symbol"), 409
            else:
                if held_contracts >= 0:
                    return jsonify(ok=False, error="No short option holding for this contract; BTC refused"), 409
                if ticket["contracts"] > math.floor(abs(held_contracts)):
                    return jsonify(ok=False, error=f"Only {math.floor(abs(held_contracts))} short contracts to cover"), 409
        if intent == "STO" and covered and str(body.get("right") or ticket.get("right") or "").upper() in ("C", "CALL"):
            stock_pos = broker.get_positions()
            if not stock_pos.get("ok"):
                return jsonify(ok=False, error=stock_pos.get("error") or "Stock holdings unavailable for covered check"), 409
            shares = 0.0
            for row in stock_pos.get("positions") or []:
                if str(row.get("symbol") or "").upper() == ticker:
                    shares = float(row.get("qty") or 0)
                    break
            need = ticket["contracts"] * 100
            if shares < need:
                return jsonify(ok=False, error=f"Covered short call needs {need} long shares; held {shares}"), 409
        sig = {
            "id": uuid.uuid4().hex, "ticker": ticker, "side": side, "status": "pending",
            "workspace": "live", "source": "manual_ticket", "origin": "user_directed",
            "asset_type": "OPT", "ack_symbol": local_symbol,
            "mode_at_create": "live_manual", "suggested_shares": ticket["contracts"],
            "signal_price": premium, "quote": quote, "review_contract": contract,
            "manual_order": {"intent": intent, "order": ticket},
            "reason": "User-directed live option ticket; not an AI recommendation",
            "ts": created.isoformat(), "created_at": created.isoformat(),
            "expires_at": (created + timedelta(seconds=90)).isoformat(),
        }
        restriction = desk.signal_execution_block(sig, broker=True)
        if restriction:
            return jsonify(ok=False, error=restriction), 409
        day_pnl, equity, account_error = desk._broker_day_pnl()
        if account_error and not (reducing and day_pnl is None and equity is not None and equity > 0):
            return jsonify(ok=False, error=account_error), 409
        with desk._lock:
            current = desk.load_config()
            error = desk._broker_authorization_error(cfg, identity, current)
            ledger = desk.load_ledger()
            if error or ledger.get("pending_broker_orders"):
                return jsonify(ok=False, error=error or "A broker order now requires reconciliation"), 409
            max_pos = None
            ks = current.get("kill_switch") or {}
            try:
                max_pos = float(ks["max_position_size_usd"]) if ks.get("max_position_size_usd") is not None else None
            except (TypeError, ValueError):
                max_pos = None
            # Same measure as execution: worst-case loss (strike risk for shorts), not premium.
            risk = notional if reducing else order_terms.option_max_loss(
                {"contracts": ticket["contracts"], "option_intent": intent, "right": ticket["right"],
                 "strike": ticket["strike"], "covered": covered}, premium)
            if not reducing and max_pos is not None and risk > max_pos:
                return jsonify(ok=False, error=f"Option worst-case loss ${risk:,.2f} exceeds max position ${max_pos:,.2f}"), 409
            ok, error = desk._broker_session_gate(current, risk, reducing=reducing)
            if ok and not reducing:
                ok, error = desk._broker_risk_gate(current, ledger, day_pnl, equity)
            if not ok:
                return jsonify(ok=False, error=error), 409
            signals = desk.load_signals()
            if sum(s.get("source") == "manual_ticket" and s.get("status") == "pending" for s in signals) >= 25:
                return jsonify(ok=False, error="Too many open manual tickets; discard them or wait for expiry"), 429
            desk.save_signals([sig, *signals])
        # Build an options review ticket (no stock estimate_order / share-cap path).
        with desk._lock:
            current_cfg = desk.load_config()
            current = next((s for s in desk.load_signals() if s.get("id") == sig["id"]), None)
            if not current or current.get("status") != "pending":
                return jsonify(ok=False, error="Ticket disappeared during review"), 409
            if identity != current_cfg.get("broker_identity") or current_cfg.get("mode") != "live_manual":
                return jsonify(ok=False, error="Broker account or mode changed; request a fresh review"), 409
            expires = time.time() + 90
            try:
                if current.get("expires_at"):
                    expires = min(expires, datetime.fromisoformat(current["expires_at"].replace("Z", "+00:00")).timestamp())
            except (TypeError, ValueError):
                return jsonify(ok=False, error="Invalid signal expiry"), 400
            if expires <= time.time():
                return jsonify(ok=False, error="Signal expired"), 400
            if len(desk._BROKER_REVIEWS) >= 100:
                return jsonify(ok=False, error="Too many open order reviews; wait for old reviews to expire"), 429
            token = uuid.uuid4().hex + uuid.uuid4().hex
            terms = desk._review_terms(current, current_cfg)
            desk._BROKER_REVIEWS[token] = {
                "terms": terms, "identity": identity, "expires": expires, "order": ticket,
                "contract": contract,
            }
        return jsonify(
            ok=True, review_token=token, identity=identity, signal_id=sig["id"],
            ticker=ticker, side=side, intent=intent, quote=quote, order=ticket,
            asset_type="OPT", local_symbol=local_symbol, ack_symbol=local_symbol,
            contract=contract, held_contracts=held_contracts if reducing else None,
            estimated_notional=round(notional, 2),
            estimated_cash_change=(-round(notional, 2) if side == "buy" else round(notional, 2)),
            expires_at=datetime.fromtimestamp(expires, timezone.utc).isoformat(),
            costs={"notional_bound": round(notional, 2), "commission_estimate": None, "currency": "USD",
                   "guaranteed": False, "note": "Option notional = contracts × premium × 100; fees unknown"},
            purpose="User-directed live option ticket (long single-leg)",
            next_step=f"Type the OCC symbol {local_symbol} to submit this reviewed option order",
        )


    app.register_blueprint(bp)
