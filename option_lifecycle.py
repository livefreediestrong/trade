"""Paper-only option exits and persistent exercise/assignment discrepancy holds.

The existing desk owns submission, durable intents and fill reconciliation.
Position changes are evidence of exposure, never proof of an assignment or sale.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid
from datetime import date, datetime, timedelta, timezone

import paper_loop
from order_terms import canonical_bag_order, canonical_option_order, round_option_price


# Signal outcomes that prove no broker order remains for an entry.
ENDED_WITHOUT_ORDER = {"rejected", "blocked", "expired", "cancelled", "failed", "error", "discarded"}


def now_utc():
    return datetime.now(timezone.utc)


def discard_prepared(service, signal):
    """Undo prepare() when dispatch failed before any durable broker intent existed."""
    with service.desk._lock:
        pending = {(p.get("signal") or {}).get("id") for p in service.desk.load_ledger().get("pending_broker_orders", [])}
        if signal.get("id") in pending:
            return  # An intent exists: the row is evidence for its reconciliation.
        raw = service.load()
        rows = raw.get("option_positions", {})
        if signal.get("agent_option_position_id"):
            row = rows.get(signal["agent_option_position_id"])
            if not row or signal.get("id") not in row["exit_signals"]:
                return
            row["exit_signals"].pop(signal["id"])
        else:
            row = rows.get(signal.get("id"))
            if not row or not row.get("awaiting_entry") or row["fills"]:
                return
            rows.pop(signal["id"])
        service.save(raw)


def scope(identity):
    if not isinstance(identity, dict) or not identity.get("account_id") or type(identity.get("paper_mode")) is not bool:
        raise ValueError("Option account is unverified")
    return {k: identity.get(k) for k in ("broker", "account_id", "paper_mode")}


def amount(value, *, whole=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Option quantity or price is unverified")
    if whole and value != int(value):
        raise ValueError("Whole option contracts are required")
    return value


def state_valid(rows):
    try:
        if not isinstance(rows, dict):
            return False
        for key, row in rows.items():
            if key != row["id"] or scope(row["account_scope"]) != row["account_scope"]:
                return False
            if not isinstance(row["entry_signal"], dict) or not isinstance(row["exit_signals"], dict):
                return False
            records = row.get("broker_records", {})
            if not isinstance(records, dict) or any(not isinstance(k, str) or not isinstance(v, dict) for k, v in records.items()):
                return False
            if row["asset_type"] not in ("OPT", "BAG") or len(row["legs"]) != (1 if row["asset_type"] == "OPT" else 2):
                return False
            if len({leg["con_id"] for leg in row["legs"]}) != len(row["legs"]):
                return False
            for leg in row["legs"]:
                if type(leg["con_id"]) is not int or leg["con_id"] <= 0 or leg["sign"] not in (-1, 1):
                    return False
            if amount(row["remaining"], whole=True) < 0 or not isinstance(row["fills"], dict):
                return False
            amount(row["stock_baseline"])
            for fill in row["fills"].values():
                if amount(fill["qty"], whole=True) < 0 or amount(fill["notional"]) < 0:
                    return False
            if not isinstance(row.get("hold"), (str, type(None))) or not isinstance(row["events"], list):
                return False
            date.fromisoformat(row["expiry"])
            from auto_live_options import validate
            validate(row["exit_policy"])
            resolution = row.get("external_resolution")
            if resolution is not None and (not isinstance(resolution, dict)
                    or not isinstance(resolution.get("receipt_key"), str) or len(resolution["receipt_key"]) != 64
                    or resolution.get("broker_flat_verified") is not True or not isinstance(resolution.get("reference"), str)):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def rows_for(raw, identity):
    owner = scope(identity)
    return [r for r in raw.get("option_positions", {}).values() if r["account_scope"] == owner]


def gate_error(raw, identity, ticker=None):
    for row in rows_for(raw, identity):
        if row.get("hold") and (ticker is None or row["ticker"] == ticker):
            return f"{row['ticker']}: {row['hold']}"
    return None


def receipt_key(row):
    return hashlib.sha256(json.dumps(row["fills"], sort_keys=True).encode()).hexdigest()


def resolved(row):
    return bool(row.get("external_resolution") and row["external_resolution"].get("receipt_key") == receipt_key(row) and not row.get("hold"))


def snapshot(identity):
    import broker_router
    result = broker_router.option_lifecycle_snapshot(identity)
    if not isinstance(result, dict) or result.get("ok") is not True or result.get("identity") != identity:
        raise ValueError("Current option holdings and working orders could not be verified")
    stamp = datetime.fromisoformat(str(result.get("checked_at") or ""))
    if stamp.tzinfo is None or not 0 <= (now_utc() - stamp).total_seconds() <= 15:
        raise ValueError("Option holdings check is stale")
    if not isinstance(result.get("positions"), list) or not isinstance(result.get("orders"), list):
        raise ValueError("Option holdings check is incomplete")
    seen = set()
    for p in result["positions"]:
        if type(p.get("con_id")) is not int or p["con_id"] <= 0 or p["con_id"] in seen:
            raise ValueError("Broker holdings contain ambiguous contracts")
        seen.add(p["con_id"])
        amount(p["qty"])
        if not p.get("symbol") or not p.get("asset_type") or not p.get("currency"):
            raise ValueError("Broker holding identity is incomplete")
        if p["asset_type"] == "OPT":
            amount(p["qty"], whole=True)
    for order in result["orders"]:
        if not order.get("symbol") or not isinstance(order.get("con_ids"), list):
            raise ValueError("Working order identity is incomplete")
    return result


def holdings(snap, ticker):
    positions = [p for p in snap["positions"] if p["symbol"] == ticker]
    stocks = [p for p in positions if p["asset_type"] == "STK"]
    if any(p["currency"] != "USD" for p in positions) or len(stocks) > 1:
        raise ValueError("Underlying holdings require a currency or contract review")
    return {p["con_id"]: p for p in positions if p["asset_type"] == "OPT"}, sum(p["qty"] for p in stocks)


def _event(row, kind, message, **evidence):
    row["events"].append(dict(at=now_utc().isoformat(), kind=kind, message=message, **evidence))


def _hold(row, message, **evidence):
    if not row.get("hold"):
        row["hold"] = message
        _event(row, "review_required", message, **evidence)


def prepare(service, cfg, signal, snap):
    """Persist ownership and baseline BEFORE dispatch. Caller holds submission lock."""
    from auto_live_options import get_config
    identity = cfg.get("broker_identity")
    owner = scope(identity)
    opts = get_config(cfg)
    if owner["broker"] != "ibkr" or owner["paper_mode"] is not True or not opts["automatic_exits"]:
        raise ValueError("Automatic options require the enabled IBKR paper exit test")
    with service.desk._lock:
        raw = service.load()
        if signal.get("agent_option_position_id"):
            row = raw.get("option_positions", {}).get(signal["agent_option_position_id"])
            if not row or row != signal.get("agent_position") or row["account_scope"] != owner or row.get("hold"):
                raise ValueError("Tracked option position changed; wait for a fresh exit check")
            verify_row(row, snap)
            row["exit_signals"][signal["id"]] = copy.deepcopy(signal)
            # Avoid recursive copies of the whole historic position in stored signals.
            row["exit_signals"][signal["id"]].pop("agent_position", None)
            signal["agent_position"] = copy.deepcopy(row)
            service.save(raw)
            return
        error = gate_error(raw, identity)
        if error:
            raise ValueError(error)
        ticker = signal["ticker"]
        if any((r["remaining"] or r.get("awaiting_entry")) and not resolved(r) for r in rows_for(raw, identity)):
            raise ValueError("Paper options test allows one managed position at a time")
        if (raw.get("managed") or {}).get(ticker):
            raise ValueError("Paper option test cannot share its underlying with a managed stock position")
        held, stock_qty = holdings(snap, ticker)
        if held or any(o["symbol"] == ticker for o in snap["orders"]):
            raise ValueError("Existing options or working orders on this underlying need review first")
        contract = signal.get("review_contract") or {}
        if contract.get("multiplier") != 100:
            raise ValueError("Only standard 100-share option contracts are supported")
        if signal["asset_type"] == "OPT":
            specs = [dict(con_id=contract.get("con_id"), strike=signal["strike"],
                          sign=1 if signal["option_intent"] == "BTO" else -1)]
        else:
            by_strike = {float(p["strike"]): p["con_id"] for p in contract.get("legs", [])}
            specs = [dict(con_id=by_strike[float(signal[k])], strike=signal[k], sign=sign)
                     for k, sign in (("long_strike", 1), ("short_strike", -1))]
        key = signal["id"]
        row = dict(id=key, ticker=ticker, account_scope=owner, asset_type=signal["asset_type"],
                   strategy=signal["option_strategy"], expiry=signal["expiry"], right=signal["right"],
                   legs=specs, stock_baseline=stock_qty, remaining=0, entry_price=None,
                   entry_signal=copy.deepcopy(signal), exit_signals={}, fills={}, awaiting_entry=True,
                   opened_at=now_utc().isoformat(), exit_policy=copy.deepcopy(opts), hold=None, events=[])
        if not state_valid({key: row}):
            raise ValueError("Option ownership evidence is incomplete")
        raw.setdefault("option_positions", {})[key] = row
        service.save(raw)


def sync_ledger(service):
    """Cumulative fill receipts survive restarts; missing history never erases a position."""
    with service.desk._lock:
        raw = service.load()
        ledger = service.desk.load_ledger()
        fills = {f.get("signal_id"): f for f in ledger.get("broker_fills", [])}
        pending = {p.get("signal", {}).get("id") for p in ledger.get("pending_broker_orders", [])}
        signals = {s.get("id"): s for s in service.desk.load_signals()}
        changed = False
        for row in raw.get("option_positions", {}).values():
            before = copy.deepcopy(row)
            for sid in [row["id"], *row["exit_signals"]]:
                saved_signal = signals.get(sid)
                if saved_signal and saved_signal.get("live_response"):
                    saved_signal = copy.deepcopy(saved_signal)
                    saved_signal.pop("agent_position", None)
                    row.setdefault("broker_records", {})[sid] = saved_signal
                f = fills.get(sid)
                if not f:
                    continue
                if f.get("broker_identity") is not None and scope(f["broker_identity"]) != row["account_scope"]:
                    _hold(row, "Option fill belongs to a different account; review the broker activity")
                    continue
                if f.get("confirmed") is not True or f.get("price_estimated") is True:
                    _hold(row, "Option fill details changed or are incomplete; review the broker activity")
                    continue
                qty, price = amount(f["shares"], whole=True), amount(f["price"])
                if qty < 0 or price < 0 or (qty > 0 and price <= 0):
                    raise ValueError("Option fill is invalid")
                receipt = dict(qty=qty, notional=qty * price)
                prior = row["fills"].get(sid)
                if prior and (qty < prior["qty"] or (qty == prior["qty"] and receipt != prior)):
                    _hold(row, "Broker corrected an option fill; review the changed contracts and price")
                row["fills"][sid] = receipt
            entry = row["fills"].get(row["id"], dict(qty=0, notional=0))
            remaining = entry["qty"] - sum(f["qty"] for sid, f in row["fills"].items() if sid != row["id"])
            if remaining < 0:
                _hold(row, "Option exits exceed the recorded entry; review broker activity")
            row["remaining"] = max(0, remaining)
            row["entry_price"] = entry["notional"] / entry["qty"] if entry["qty"] else None
            if row.get("external_resolution") and row["external_resolution"]["receipt_key"] != receipt_key(row):
                _hold(row, "Option fill evidence changed after the external-resolution review; review again")
            entry_signal = signals.get(row["id"], {})
            if row["id"] not in pending and (entry["qty"] or entry_signal.get("status") in ENDED_WITHOUT_ORDER):
                row["awaiting_entry"] = False
            if row != before:
                changed = True
        if changed:
            service.save(raw)


def verify_row(row, snap):
    """Exact owned quantities only. Never turn absent legs into invented fills."""
    held, stocks = holdings(snap, row["ticker"])
    expected = {p["con_id"]: row["remaining"] * p["sign"] for p in row["legs"]}
    actual = {cid: p["qty"] for cid, p in held.items() if p["qty"]}
    if actual != {cid: q for cid, q in expected.items() if q} or stocks != row["stock_baseline"]:
        raise ValueError("Option or stock holdings changed outside the recorded fills. Possible exercise, assignment, expiry or external trade; review broker activity before resuming")
    for p in held.values():
        leg = next((x for x in row["legs"] if x["con_id"] == p["con_id"]), None)
        if (not leg or p.get("multiplier") != 100 or p.get("right") != row["right"]
                or str(p.get("expiry", "")).replace("-", "") != row["expiry"].replace("-", "")
                or p.get("strike") != leg["strike"]):
            raise ValueError("Option contract terms changed; review possible contract adjustment")
    if any(o["symbol"] == row["ticker"] for o in snap["orders"]):
        raise ValueError("A working order already affects this underlying; wait for reconciliation")


def monitor(service, cfg, *, force=False):
    """Read-only broker observation, also while paused or outside market hours."""
    sync_ledger(service)
    with service.desk._lock:
        rows = [copy.deepcopy(r) for r in rows_for(service.load(), cfg.get("broker_identity"))
                if (r["remaining"] or r.get("awaiting_entry") or r.get("hold")) and not resolved(r)]
        pending = bool(service.desk.load_ledger().get("pending_broker_orders"))
    if not rows:
        return None
    if pending:
        return "An option order is unresolved; waiting for broker fill evidence"
    if not force and (now_utc() - getattr(service, "option_checked_at", datetime.min.replace(tzinfo=timezone.utc))).total_seconds() < 30:
        return getattr(service, "option_check_error", None)
    service.option_checked_at = now_utc()
    try:
        snap = snapshot(cfg["broker_identity"])
        with service.desk._lock:
            if service.desk.load_config().get("broker_identity") != cfg["broker_identity"]:
                raise ValueError("Account changed during option check")
            raw = service.load()
            for old in rows:
                row = raw["option_positions"].get(old["id"])
                if row != old:
                    raise ValueError("Option fills changed during holdings check")
                try:
                    verify_row(row, snap)
                except ValueError as exc:
                    # A normal working order waits; an unexplained exposure change is sticky.
                    if "working order" in str(exc):
                        raise
                    held, stocks = holdings(snap, row["ticker"])
                    _hold(row, str(exc), expected_contracts=row["remaining"],
                          observed_options=list(held.values()), observed_stock_qty=stocks,
                          stock_change=stocks - row["stock_baseline"])
                row["checked_at"] = snap["checked_at"]
            service.save(raw)
            error = gate_error(raw, cfg["broker_identity"])
    except (ValueError, KeyError, TypeError) as exc:
        error = str(exc)
    service.option_check_error = error
    return error


def expiry_deadline(expiry, sessions_before):
    day = date.fromisoformat(expiry)
    # Last trading session on/before expiry; then count complete prior sessions.
    while paper_loop.session_close_time(day) is None:
        day -= timedelta(days=1)
    for _ in range(sessions_before):
        day -= timedelta(days=1)
        while paper_loop.session_close_time(day) is None:
            day -= timedelta(days=1)
    return datetime.combine(day, paper_loop.session_close_time(day), paper_loop.NY_TZ) - timedelta(minutes=30)


def exit_reason(row, premium, now):
    policy, entry = row["exit_policy"], row["entry_price"]
    if now >= expiry_deadline(row["expiry"], policy["exit_before_expiry_sessions"]):
        return "before_expiry"
    credit = row["strategy"].startswith("short_") or row["strategy"].endswith("credit")
    gain = (entry - premium if credit else premium - entry) / entry * 100
    if gain <= -policy["exit_loss_pct"]:
        return "loss_limit"
    if gain >= policy["exit_profit_pct"]:
        return "profit_target"
    return None


def closing_quote(row):
    import broker_router
    specs = [dict(symbol=row["ticker"], right=row["right"], expiry=row["expiry"],
                  strike=leg["strike"], action="SELL" if leg["sign"] > 0 else "BUY") for leg in row["legs"]]
    quote = broker_router.option_quotes(specs)
    stamp = datetime.fromisoformat(str(quote.get("received_at") or ""))
    policy = row["exit_policy"]
    if (quote.get("ok") is not True or quote.get("source") != "IBKR streaming bid/ask"
            or scope(quote.get("identity")) != row["account_scope"] or stamp.tzinfo is None
            or not 0 <= (now_utc() - stamp).total_seconds() <= policy["max_quote_age_sec"]
            or len(quote.get("legs", [])) != len(row["legs"])):
        raise ValueError("Fresh option quotes are unavailable")
    net = 0
    for leg, expected, q in zip(row["legs"], specs, quote["legs"]):
        if q.get("con_id") != leg["con_id"] or str(q.get("multiplier")) != "100" or q.get("market_data_type") != 1:
            raise ValueError("Option quote contract changed or market data is delayed")
        if any(q.get(k) != expected[k] for k in ("symbol", "right", "expiry", "strike", "action")):
            raise ValueError("Option quote terms do not match the owned contract")
        if q.get("halted") != 0:
            raise ValueError("Option trading halt status is unknown or halted")
        for key in ("bid_observed_at", "ask_observed_at"):
            received = datetime.fromisoformat(str(q.get(key) or ""))
            if received.tzinfo is None or not 0 <= (now_utc() - received).total_seconds() <= policy["max_quote_age_sec"]:
                raise ValueError("Option bid/ask receipts are stale or future-dated")
        bid, ask = amount(q.get("bid")), amount(q.get("ask"))
        if bid <= 0 or ask < bid or (ask - bid) / ((ask + bid) / 2) * 100 > policy["exit_max_spread_pct"]:
            raise ValueError("Option bid/ask is missing, crossed or wider than the saved limit")
        if amount(q.get("bid_size" if leg["sign"] > 0 else "ask_size")) < row["remaining"]:
            raise ValueError("Displayed option size cannot cover the remaining contracts")
        net += bid if leg["sign"] == 1 else -ask
    selling = row["strategy"].startswith("long_") or row["strategy"].endswith("debit")
    premium = net if selling else -net
    if premium <= 0:
        raise ValueError("Spread has no positive executable close price; manual review needed")
    # Bind expiry to the oldest actual bid/ask receipt, not request completion.
    quote = dict(quote, received_at=min(datetime.fromisoformat(q[k]) for q in quote["legs"]
                 for k in ("bid_observed_at", "ask_observed_at")).astimezone(timezone.utc).isoformat())
    return premium, quote


def close_terms(signal, cfg):
    """Exit remaining contracts, never shrink a close to an opening risk budget."""
    from auto_live_options import get_config
    if (cfg.get("broker_identity") or {}).get("paper_mode") is not True or not get_config(cfg)["automatic_exits"]:
        raise ValueError("Automatic option exits are restricted to the IBKR paper test")
    row = signal.get("agent_position") or {}
    qty = amount(row.get("remaining"), whole=True)
    if qty <= 0 or signal.get("contracts") != qty:
        raise ValueError("Option exit quantity differs from the tracked remaining contracts")
    q = signal.get("quote") or {}
    stamp = datetime.fromisoformat(str(q.get("market_time") or ""))
    if stamp.tzinfo is None or not 0 <= (now_utc() - stamp).total_seconds() <= row["exit_policy"]["max_quote_age_sec"]:
        raise ValueError("Option close quote expired")
    premium = amount(q["price"])
    limit = round_option_price(premium, buying=signal["side"] == "buy", combo=row["asset_type"] == "BAG")
    args = dict(type="limit", limit_price=float(limit), intent=signal["option_intent"],
                strategy=row["strategy"], right=row["right"], expiry=row["expiry"])
    if row["asset_type"] == "OPT":
        return canonical_option_order(signal, dict(args, strike=row["legs"][0]["strike"]))
    return canonical_bag_order(signal, dict(args, long_strike=row["legs"][0]["strike"], short_strike=row["legs"][1]["strike"]))


def manage_exits(service, cfg):
    from auto_live_options import get_config
    if not get_config(cfg)["automatic_exits"]:
        return False
    if (cfg.get("broker_identity") or {}).get("paper_mode") is not True:
        service.phase, service.message = "option_review", "Automatic option exits require an IBKR paper account"
        return True
    error = monitor(service, cfg, force=True)
    if error:
        service.phase, service.message = "option_review", error
        return True
    with service.desk._lock:
        rows = copy.deepcopy(rows_for(service.load(), cfg["broker_identity"]))
    for row in rows:
        if not row["remaining"] or row.get("hold") or resolved(row):
            continue
        if now_utc().astimezone(paper_loop.NY_TZ).date() > date.fromisoformat(row["expiry"]):
            service.phase, service.message = "option_review", "An expired option remains recorded; review settlement or assignment at the broker"
            return True
        if row.get("retry_after") and now_utc() < datetime.fromisoformat(row["retry_after"]):
            continue
        try:
            premium, quote = closing_quote(row)
            reason = exit_reason(row, premium, now_utc())
        except (ValueError, TypeError, KeyError) as exc:
            service.phase, service.message = "option_review", str(exc)
            return True
        if not reason:
            continue
        selling = row["strategy"].startswith("long_") or row["strategy"].endswith("debit")
        signal = copy.deepcopy(row["entry_signal"])
        for key in ("live_response", "fill", "review_order", "execution_quote", "broker_book", "reject_reason"):
            signal.pop(key, None)
        now = now_utc()
        signal.update(id=str(uuid.uuid4()), side="sell" if selling else "buy", source="live_agent", workspace="live",
                      option_intent="CLOSE" if row["asset_type"] == "BAG" else "STC" if selling else "BTC",
                      contracts=row["remaining"], suggested_shares=row["remaining"], signal_price=premium,
                      agent_exit="option_" + reason, agent_option_position_id=row["id"], agent_position=copy.deepcopy(row),
                      agent_revision=cfg["live_agent"]["revision"], agent_run_id=cfg["live_agent"]["run_id"],
                      agent_identity=copy.deepcopy(cfg["broker_identity"]), agent_exit_policy=copy.deepcopy(cfg["live_agent"]["policy"]),
                      status="pending", verdict="EXIT", confidence=1.0,
                      reason="Paper option exit: " + reason.replace("_", " "), ts=now.isoformat(), created_at=now.isoformat(),
                      expires_at=(now + timedelta(seconds=row["exit_policy"]["max_quote_age_sec"])).isoformat(),
                      quote=dict(price=premium, market_time=quote["received_at"], source="IBKR option quote", fresh=True))
        result = service.desk._ingest_one_signal(signal, cfg_override=cfg)
        sync_ledger(service)
        with service.desk._lock:
            raw = service.load()
            current = raw["option_positions"][row["id"]]
            current["retry_after"] = (now + timedelta(seconds=60)).isoformat()
            _event(current, "exit_attempt", result.get("error") or result.get("reject_reason") or "Exit sent; fills remain authoritative", signal_id=signal["id"])
            service.save(raw)
        service.record("option_exit", result.get("error") or "Paper option exit checked", signal, result.get("fill"))
        return True
    return False


def status(raw, identity):
    if not identity:
        return []
    return [dict(id=r["id"], ticker=r["ticker"], strategy=r["strategy"], expiry=r["expiry"],
                 remaining=r["remaining"], entry_price=r["entry_price"], hold=r.get("hold"),
                 externally_resolved=resolved(r),
                 awaiting_entry=r["awaiting_entry"], checked_at=r.get("checked_at"),
                 deadline=expiry_deadline(r["expiry"], r["exit_policy"]["exit_before_expiry_sessions"]).isoformat(),
                 events=r["events"][-5:]) for r in rows_for(raw, identity)]
