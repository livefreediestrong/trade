"""Explicit, account-bound cancellation of orders tracked by this desk."""
import copy
import time
import uuid

from flask import Blueprint, jsonify, request


def register(app, desk):
    bp = Blueprint("broker_controls", __name__)
    reviews = {}

    def pending(signal_id):
        return next((copy.deepcopy(p) for p in desk.load_ledger().get("pending_broker_orders", [])
                     if p.get("signal", {}).get("id") == signal_id), None)

    @bp.post("/api/signals/<signal_id>/cancel-review")
    def review_cancel(signal_id):
        import broker_router as broker
        with desk._lock:
            item = pending(signal_id)
        if not item or str(item["order_id"]).startswith("intent:"):
            return jsonify(ok=False, error="A known working desk order is required; ambiguous submissions must reconcile first"), 409
        context = broker.verify_execution_context()
        expected = (item.get("broker", {}).get("order") or {}).get("broker_identity")
        if not expected or not context.get("ok") or context.get("identity") != expected:
            return jsonify(ok=False, error="Broker account differs from the order account"), 409
        status = broker.wait_for_fill(item["order_id"], timeout=0)
        if desk._broker_order_terminal(status):
            return jsonify(ok=False, error="Order is already terminal; refresh the desk"), 409
        with desk._lock:
            current = pending(signal_id)
            if not current or current["order_id"] != item["order_id"]:
                return jsonify(ok=False, error="Order changed during review"), 409
            for token in list(reviews):
                if reviews[token]["expires"] <= time.time():
                    reviews.pop(token)
            if len(reviews) >= 100:
                return jsonify(ok=False, error="Too many cancellation reviews"), 429
            token = uuid.uuid4().hex + uuid.uuid4().hex
            reviews[token] = {"order_id": item["order_id"], "identity": expected,
                              "signal_id": signal_id, "expires": time.time() + 60}
        return jsonify(ok=True, review_token=token, order_id=item["order_id"], identity=expected,
                       ticker=item["signal"].get("ticker"), filled_qty=status.get("filled_qty"),
                       note="Cancels only the unfilled remainder. Fills can occur while cancellation is in flight.")

    @bp.post("/api/signals/<signal_id>/cancel")
    def cancel(signal_id):
        import broker_router as broker
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or not isinstance(body.get("review_token"), str):
            return jsonify(ok=False, error="Cancellation review required"), 400
        # Same lock order as submission. The final account check occurs inside
        # the adapter owner thread as well as here, immediately before cancel.
        with desk._BROKER_SUBMIT_LOCK, desk._BROKER_EXEC_LOCK:
            with desk._lock:
                review = reviews.get(body["review_token"])
                item = pending(signal_id)
                if not review or review["expires"] <= time.time() or review["signal_id"] != signal_id or not item or item["order_id"] != review["order_id"]:
                    return jsonify(ok=False, error="Cancellation review expired or changed"), 409
                if str(body.get("ack_ticker") or "").upper() != str(item["signal"].get("ticker") or "").upper():
                    return jsonify(ok=False, error="Confirm the exact ticker"), 400
                reviews.pop(body["review_token"])
            result = broker.cancel_reviewed_order(item["order_id"], review["identity"], review["expires"])
            _, terminal = desk._apply_broker_update(item["signal"], item["broker"], result, "manual_cancel")
            desk.append_journal("broker_cancel_requested", {"order_id": item["order_id"], "result": result})
            return jsonify(ok=True, terminal=terminal, status=result,
                           message="Broker terminal state verified" if terminal else "Cancellation not yet terminal; order remains tracked")

    app.register_blueprint(bp)
