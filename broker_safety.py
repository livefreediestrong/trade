"""Read-only protection/expiry evidence. Never submits or certifies an order."""
from datetime import date, datetime, timezone
import math
import re
import threading
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify


def finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and abs(value) < 1e100)


def commitments(snapshot):
    """Describe one already-validated read; never calculate spendable cash or margin."""
    unavailable = {"ok": False, "buy_limits": [], "holdings": [], "groups": [], "orders": [],
                   "summary": "Holdings or order identities conflict. Account commitments are unverified."}
    holdings, orders, groups, totals = [], [], {}, {}
    short_options = unknown_buys = contingent_buys = sells = 0
    seen_positions, seen_orders = {}, {}
    for p in snapshot["positions"]:
        if not p["qty"]:
            continue
        con_id = p.get("con_id")
        if type(con_id) is not int or con_id <= 0:
            return unavailable
        record = {key: p.get(key) for key in ("symbol", "local_symbol", "asset_type", "currency", "qty", "right", "expiry")}
        if con_id in seen_positions:
            if seen_positions[con_id] != record:
                return unavailable
            continue
        seen_positions[con_id] = record
        currency = p.get("currency") if re.fullmatch(r"[A-Z]{3}", str(p.get("currency") or "")) else "Unknown"
        asset = p.get("asset_type") or "Unknown"
        holdings.append({"instrument": p.get("local_symbol") or p.get("symbol") or "Unknown instrument",
                         "asset_type": asset, "currency": currency, "qty": p["qty"]})
        key = (currency, asset)
        group = groups.setdefault(key, {"currency": currency, "asset_type": asset, "long_count": 0, "short_count": 0})
        group["long_count" if p["qty"] > 0 else "short_count"] += 1
        short_options += int(asset in ("OPT", "FOP") and p["qty"] < 0)

    for o in snapshot["orders"]:
        order_id = o.get("id")
        # An absent identifier can hide duplicates; do not publish a subtotal.
        if not isinstance(order_id, str) or not order_id:
            return unavailable
        if order_id in seen_orders:
            if seen_orders[order_id] != o:
                return unavailable
            continue
        seen_orders[order_id] = o
        remaining = o["qty"] - o["filled_qty"]
        if o.get("status") in ("Filled", "Cancelled", "ApiCancelled") or remaining == 0:
            continue
        currency = o.get("currency") if re.fullmatch(r"[A-Z]{3}", str(o.get("currency") or "")) else None
        row = {"instrument": o.get("local_symbol") or o.get("symbol") or "Unknown instrument",
               "asset_type": o.get("asset_type"), "currency": currency or "Unknown", "side": o["side"],
               "remaining_qty": remaining, "status": o.get("status"), "limit_amount": None,
               "note": "Cost unknown: no supported, confirmed standalone limit price."}
        if o["side"] == "sell":
            sells += 1
            row["note"] = "Unfilled sale: no proceeds counted as available cash. A sale can also create a short position."
        elif (o.get("transmit") is not True or o.get("parent_id") != 0 or o.get("oca_group") != ""
                or type(o.get("conditions_count")) is not int or o["conditions_count"] != 0
                or any(o.get(k) != "" for k in ("good_after_time", "good_till_date", "active_start_time",
                                               "active_stop_time", "algo_strategy", "why_held"))):
            contingent_buys += 1
            row["note"] = "Linked, conditional or held order: excluded from the standalone buy subtotal; review it with related orders."
        else:
            price, multiplier = o.get("limit_price"), o.get("multiplier")
            supported = o.get("asset_type") in ("STK", "OPT")
            # The desk qualifies dollar stock/option prices. Other markets can
            # quote in subunits (for example pence); currency alone is not enough.
            priced = (supported and currency == "USD" and o.get("order_type") == "LMT"
                      and o.get("status") in ("Submitted", "PreSubmitted", "PendingCancel")
                      and finite(price) and price > 0 and finite(multiplier) and multiplier > 0
                      and (o.get("asset_type") != "STK" or multiplier == 1))
            amount = remaining * price * multiplier if priced else None
            if amount is not None and finite(amount):
                row["limit_amount"] = amount
                row["note"] = "Remaining quantity × limit price × contract multiplier, before fees. Not the broker's cash reservation."
                if o.get("status") == "PendingCancel":
                    row["note"] += " Cancellation is pending, so the remaining quantity is still counted."
                total = totals.setdefault(currency, {"currency": currency, "amount": 0.0, "order_count": 0})
                total["amount"] += amount
                total["order_count"] += 1
                if not finite(total["amount"]):
                    return unavailable
            else:
                unknown_buys += 1
                if currency != "USD":
                    row["note"] = "Cost unknown: this market's price units and currency conversion are not verified. No dollar estimate is made."
        orders.append(row)
    return {"ok": True, "holdings": holdings, "groups": [groups[k] for k in sorted(groups)],
            "orders": orders, "buy_limits": [totals[k] for k in sorted(totals)],
            "short_option_count": short_options, "unknown_buy_count": unknown_buys,
            "contingent_buy_count": contingent_buys, "sell_order_count": sells,
            "summary": f"{len(holdings)} held instruments and {len(orders)} unfinished orders in this broker read.",
            "note": "Covers all asset types and currencies returned for this account, including holdings opened outside Fox. "
                    "Only API-visible orders are included; visibility of every manually entered order is not verified. "
                    "Currencies stay separate; cost estimates currently cover supported USD stock and option limit buys only. "
                    "Option premiums do not measure assignment obligations or the risk of the combined position. "
                    "This is not a market valuation, margin calculation, settlement check or permission to trade. "
                    "Do not subtract these subtotals from buying power: the broker may already account for some orders."}


def assess(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    if (not isinstance(snapshot, dict) or snapshot.get("ok") is not True
            or not isinstance(snapshot.get("positions"), list) or not isinstance(snapshot.get("orders"), list)):
        return {"ok": False, "summary": "Broker protection could not be checked. Holdings are unknown.",
                "error": (snapshot or {}).get("error") if isinstance(snapshot, dict) else None, "rows": []}
    try:
        stamp = datetime.fromisoformat(str(snapshot.get("checked_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None or not 0 <= (now - stamp).total_seconds() <= 45:
            raise ValueError
    except (ValueError, TypeError):
        return {"ok": False, "summary": "Protection records are old or undated. Waiting for a fresh broker check.", "rows": []}
    today = now.astimezone(ZoneInfo("America/New_York")).date()
    if any(not isinstance(o, dict) or not o.get("asset_type") or type(o.get("con_id")) is not int
           or o.get("side") not in ("buy", "sell") or not finite(o.get("qty"))
           or not finite(o.get("filled_qty")) or not 0 <= o["filled_qty"] <= o["qty"]
           for o in snapshot["orders"]):
        return {"ok": False, "summary": "Some working orders are unreadable. Protection is unverified.", "rows": []}
    rows = []
    for p in snapshot["positions"]:
        if not isinstance(p, dict) or not finite(p.get("qty")) or not p.get("con_id"):
            return {"ok": False, "summary": "Some holdings are unreadable. Protection is unverified.", "rows": []}
        qty = p["qty"]
        if not qty:
            continue
        row = {"symbol": p.get("symbol") or "Unknown symbol", "instrument": p.get("local_symbol") or p.get("symbol"),
               "quantity": qty, "asset_type": p.get("asset_type"), "state": "review", "notes": []}
        if p.get("asset_type") == "OPT":
            expiry = str(p.get("expiry") or "")
            try:
                expires = date.fromisoformat(expiry)
                days = (expires - today).days
                row.update(expiry=expires.isoformat(), calendar_days_to_expiry=days)
                if days < 0:
                    row["notes"].append("Past its listed expiry: reconcile exercise, assignment or settlement with the broker. Do not assume it disappeared safely.")
                elif days == 0:
                    row["notes"].append("Expires today. Review the exact contract and broker cutoff; an automatic close has not been verified.")
                else:
                    row["notes"].append(f"Expires {expires.isoformat()} ({days} calendar days). Automatic closing is not yet qualified.")
            except (TypeError, ValueError):
                row["notes"].append("Expiry could not be verified. Review the contract at the broker.")
            if qty < 0:
                row["notes"].append("Short option: assignment can occur before expiry, including one leg of a spread. Delivered stock needs reconciliation.")
        elif p.get("asset_type") == "STK" and p.get("currency") == "USD" and qty > 0:
            sells = [o for o in snapshot["orders"] if o.get("con_id") == p["con_id"]
                     and o.get("asset_type") == "STK" and o.get("side") == "sell"]
            stops = [o for o in sells if o.get("order_type") in ("STP", "STP LMT", "TRAIL", "TRAIL LIMIT")]
            short_calls = any(isinstance(other, dict) and other.get("asset_type") == "OPT"
                and other.get("symbol") == p.get("symbol") and other.get("right") == "C"
                and finite(other.get("qty")) and other["qty"] < 0 for other in snapshot["positions"])
            working_calls = any(o.get("asset_type") == "OPT" and o.get("symbol") == p.get("symbol")
                and o.get("right") == "C" and o.get("side") == "sell" and o["qty"] > o["filled_qty"]
                for o in snapshot["orders"])
            confirmed = []
            for o in stops:
                if (not finite(o.get("qty")) or not finite(o.get("filled_qty"))
                        or not 0 <= o["filled_qty"] <= o["qty"]):
                    continue
                if (o.get("order_type") == "STP" and o.get("transmit") is True
                        and type(o.get("permanent_id")) is int and o["permanent_id"] > 0
                        and o.get("status") in ("Submitted", "PreSubmitted")
                        and not o.get("parent_id") and not o.get("why_held")
                        and type(o.get("conditions_count")) is int and o["conditions_count"] == 0
                        and all(o.get(key) == "" for key in ("good_after_time", "good_till_date",
                                "active_start_time", "active_stop_time", "algo_strategy"))
                        and not o.get("oca_group") and finite(o.get("stop_price")) and o["stop_price"] > 0
                        and o["qty"] - o["filled_qty"] == qty):
                    confirmed.append(o)
            # Do not add sibling/OCA quantities or count contingent orders as
            # active. Multiple stops could oversell; classify that for review.
            if len(sells) == 1 and len(confirmed) == 1 and not short_calls and not working_calls:
                stop = confirmed[0]
                row.update(state="stop_reported", stop_price=stop["stop_price"])
                row["notes"].append(f"Broker reports a working stop for all {qty:g} shares at ${stop['stop_price']:g}. Fill price is not guaranteed.")
                row["notes"].append("Regular hours only." if not stop.get("outside_regular_hours") else "Outside-hours setting is present; contract and broker rules still apply.")
                if stop.get("time_in_force") != "GTC":
                    row["notes"].append("This stop is not good-till-cancelled; check when it expires.")
            elif stops:
                row["notes"].append("Stop orders were found, but full active coverage is unverified. Check quantities, activation times, conditions, attached orders and broker status.")
            else:
                row["notes"].append("No broker stop found for these shares. App-managed exits need the desk and Gateway running.")
            if len(sells) > len(stops) and stops:
                row["notes"].append("Another sell order also claims these shares. Review the orders together to avoid selling more shares than you own.")
            if short_calls or working_calls:
                row["notes"].append("A short call or working call sale also uses this stock. A stock-only exit could remove its cover; review the stock and options together at the broker.")
        else:
            row["notes"].append("This holding needs a broker review; automatic protection is not verified for this asset or currency.")
        rows.append(row)
    return {"ok": True, "checked_at": snapshot["checked_at"], "paper_mode": snapshot.get("paper_mode"),
            "commitments": commitments(snapshot),
            "position_count": len(rows), "working_order_count": len(snapshot["orders"]), "rows": rows,
            "broker_native_qualification": False,
            "summary": (f"Checked {len(rows)} held instruments and {len(snapshot['orders'])} working orders."
                        if rows else f"No holdings reported at this check; {len(snapshot['orders'])} working orders remain."),
            "note": "A broker record is evidence of an order, not a guarantee it will fill. These checks never place, replace or cancel orders."}


def register(app, desk):
    bp = Blueprint("broker_safety", __name__)
    lock = threading.Lock()

    @bp.get("/api/broker-safety")
    def safety():
        import broker_router as broker
        status = broker.public_status()
        if status.get("broker") != "ibkr":
            return jsonify(ok=False, summary="Protection checks for this broker are not yet available.", rows=[])
        if not status.get("connected"):
            return jsonify(ok=False, summary="Gateway is disconnected. Holdings and protection are unknown.", rows=[])
        if not lock.acquire(blocking=False):
            return jsonify(ok=False, summary="A broker check is already running. Waiting for its result.", rows=[]), 503
        try:
            try:
                result = assess(broker.get_safety_snapshot())
            except Exception:
                result = {"ok": False, "summary": "The broker did not answer. Holdings and protection remain unknown.", "rows": []}
            return jsonify(result)
        finally:
            lock.release()

    app.register_blueprint(bp)
