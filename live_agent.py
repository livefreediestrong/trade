"""Persistent, opt-in broker agent. The existing desk owns submission/reconciliation.

Research may suggest buy/sell/hold; deterministic policy owns every order term.
Saving a policy pauses the agent. Only the explicit start route activates it.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from flask import Blueprint, jsonify, request

import paper_loop
from order_terms import canonical_order
from trade_planner import number

DEFAULTS = {
    "symbols": ["SPY"], "interval_sec": 120, "order_type": "limit",
    "max_order_usd": 100., "max_daily_loss_usd": 20., "max_orders_per_day": 3,
    "max_research_per_day": 100, "model_budget_usd": 5.,
    "limit_offset_bps": 5., "min_confidence": .6, "max_quote_age_sec": 30,
}


def now_utc():
    return datetime.now(timezone.utc)


def validate(body):
    if not isinstance(body, dict) or set(body) != set(DEFAULTS):
        raise ValueError("Save all displayed live-agent policy fields; unknown fields are not accepted")
    policy = copy.deepcopy(body)
    symbols = policy["symbols"]
    if (not isinstance(symbols, list) or not 1 <= len(symbols) <= 500 or
            any(not isinstance(s, str) or not re.fullmatch(r"[A-Z]{1,5}(?:\.[A-Z])?", s) for s in symbols)):
        raise ValueError("Choose 1–500 uppercase US stock/ETF symbols")
    policy["symbols"] = list(dict.fromkeys(symbols))
    if policy["order_type"] not in ("market", "limit"):
        raise ValueError("Choose market or DAY limit orders")
    ranges = {"interval_sec": (30, 3600), "max_order_usd": (.01, 1e9),
              "max_daily_loss_usd": (.01, 1e9), "max_orders_per_day": (1, 100000),
              "max_research_per_day": (1, 10000), "model_budget_usd": (.01, 100000),
              "limit_offset_bps": (0, 100), "min_confidence": (0, 1), "max_quote_age_sec": (1, 60)}
    integers = {"interval_sec", "max_orders_per_day", "max_research_per_day", "max_quote_age_sec"}
    for key, bounds in ranges.items():
        value = number(policy[key], key, *bounds)
        if key in integers and value != int(value):
            raise ValueError(f"{key} must be a whole number")
        policy[key] = int(value) if key in integers else float(value)
    return policy


def authorization_error(signal, cfg):
    saved = cfg.get("live_agent")
    tagged = signal.get("source") == "live_agent"
    if not saved and not tagged:
        return None
    if not isinstance(saved, dict) or not saved.get("enabled"):
        return "Live agent is paused; enable the saved policy explicitly"
    if (cfg.get("mode") != "auto_live" or not cfg.get("session_active") or not tagged or
            signal.get("agent_revision") != saved.get("revision") or
            signal.get("agent_run_id") != saved.get("run_id") or
            signal.get("agent_identity") != cfg.get("broker_identity")):
        return "Live agent authorization changed or this idea is outside its policy"
    if not paper_loop.is_rth(now_utc()):
        return "Live agent waits for regular US market hours"
    try:
        policy = validate(saved["policy"])
    except (ValueError, KeyError, TypeError):
        return "Live agent policy is invalid"
    if signal.get("ticker") not in policy["symbols"]:
        return "Symbol is outside the live agent's approved list"
    return None


def quote_error(quote, max_age):
    try:
        source = str(quote.get("source") or "").lower()
        if not source or any(word in source for word in ("mock", "demo", "synthetic", "fixture", "unknown")):
            return "Live agent requires attributable real market data"
        number(quote.get("price"), "Quote", .000001, 1e9)
        stamp = datetime.fromisoformat(str(quote.get("market_time")).replace("Z", "+00:00"))
        age = (now_utc()-stamp).total_seconds()
        if not quote.get("fresh") or not 0 <= age <= max_age or not paper_loop.is_rth(stamp):
            return "Live agent quote is stale, future-dated or outside the session"
    except (ValueError, TypeError, AttributeError):
        return "Live agent quote is missing or unverified"
    return None


def execution_terms(signal, cfg, price, execution_quote):
    """Called with the fresh execution price; never uses model-supplied order terms."""
    error = authorization_error(signal, cfg)
    if error:
        raise ValueError(error)
    policy = validate(cfg["live_agent"]["policy"])
    for quote in (signal.get("quote"), execution_quote):
        error = quote_error(quote, policy["max_quote_age_sec"])
        if error:
            raise ValueError(error)
    confidence = number(signal.get("confidence"), "Decision confidence", 0, 1)
    if confidence < Decimal(str(policy["min_confidence"])) or signal.get("verdict") != "PASS":
        raise ValueError("Live agent needs a PASS setup meeting its minimum confidence")
    if signal.get("lateness_label") in ("late", "chasing"):
        raise ValueError("Live agent will wait for a fresh entry setup")
    mark = number(price, "Execution price", .000001, 1e9)
    options = {"type": policy["order_type"]}
    bound = mark
    if options["type"] == "limit":
        decision_price = Decimal(str(signal["quote"]["price"]))
        buying = signal.get("side") == "buy"
        offset = Decimal(str(policy["limit_offset_bps"])) / 10000
        limit = decision_price * (1+offset if buying else 1-offset)
        quantum = Decimal(".01") if limit >= 1 else Decimal(".0001")
        limit = limit.quantize(quantum, rounding=ROUND_DOWN if buying else ROUND_UP)
        options["limit_price"] = float(limit)
        bound = max(mark, limit)
    proposed = number(signal.get("suggested_shares"), "Proposed shares", 0, 1e9)
    shares = min(int(proposed), int(Decimal(str(policy["max_order_usd"])) / bound))
    if shares < 1:
        raise ValueError("Agent order budget or proposed size is below one whole share")
    return canonical_order(dict(signal, suggested_shares=shares), options)


class LiveAgent:
    def __init__(self, desk):
        self.desk = desk
        self.cycle_lock = threading.Lock()
        self.phase = "not_configured"
        self.message = "Save a policy to prepare the live agent"

    @property
    def path(self):
        return self.desk.DATA_DIR / "live_agent.json"

    def load(self):
        raw = self.desk._load_json(self.path, {"days": {}, "attempts": {}, "events": [], "cursor": 0, "next_at": None})
        valid = (isinstance(raw, dict) and isinstance(raw.get("days"), dict) and
                 isinstance(raw.get("attempts"), dict) and isinstance(raw.get("events"), list) and
                 type(raw.get("cursor")) is int and raw["cursor"] >= 0)
        if valid:
            for row in raw["days"].values():
                if not isinstance(row, dict) or any(type(row.get(k)) is not int or row[k] < 0 for k in ("research", "orders")):
                    valid = False
            for row in raw["attempts"].values():
                if not isinstance(row, dict) or not isinstance(row.get("key"), str):
                    valid = False
        if not valid or str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
            self.desk._mark_corrupt(self.path, "Live agent state is unreadable")
            raise ValueError("Live agent state needs recovery; counters cannot be reset automatically")
        return raw

    def key(self, cfg):
        identity = cfg.get("broker_identity") or {}
        # Client IDs/endpoints are transport details, not a different account budget.
        scope = {k: identity.get(k) for k in ("broker", "account_id", "paper_mode")}
        digest = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()[:20]
        return now_utc().astimezone(paper_loop.NY_TZ).date().isoformat()+":"+digest

    def save(self, raw):
        if str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
            raise ValueError("Live agent state needs recovery")
        self.desk._save_json(self.path, raw)

    def status(self):
        with self.desk._lock:
            cfg = self.desk.load_config()
            saved = cfg.get("live_agent") or {}
            raw = self.load()
            message = self.message
            if saved and not saved.get("enabled"):
                message = "Agent paused; broker reconciliation continues"
            elif saved and not paper_loop.is_rth(now_utc()):
                message = "Waiting for the next regular US market session"
            return {"ok": True, "configured": bool(saved), "enabled": saved.get("enabled") is True,
                    "policy": saved.get("policy") or copy.deepcopy(DEFAULTS), "revision": saved.get("revision"),
                    "identity": cfg.get("broker_identity"), "mode": cfg.get("mode"),
                    "session_active": cfg.get("session_active"), "market_open": paper_loop.is_rth(now_utc()),
                    "phase": self.phase, "message": message, "busy": self.cycle_lock.locked(),
                    "today": raw["days"].get(self.key(cfg), {"research": 0, "orders": 0}),
                    "events": raw["events"][:30], "next_at": raw.get("next_at")}

    def record(self, status, message, signal=None):
        self.phase, self.message = status, message
        with self.desk._lock:
            raw = self.load()
            raw["events"].insert(0, {"at": now_utc().isoformat(), "status": status, "message": message,
                                      "signal_id": (signal or {}).get("id"), "ticker": (signal or {}).get("ticker")})
            raw["events"] = raw["events"][:200]
            self.save(raw)

    def trade_error(self, signal, cfg, day_pnl, reducing):
        error = authorization_error(signal, cfg)
        if error:
            return error
        policy = validate(cfg["live_agent"]["policy"])
        with self.desk._lock:
            raw = self.load()
            if signal.get("id") in raw["attempts"]:
                return "This agent decision already reserved a broker attempt; it cannot be retried"
            if raw["days"].get(self.key(cfg), {}).get("orders", 0) >= policy["max_orders_per_day"]:
                return "Live agent reached its daily broker-attempt limit"
        if not reducing and (day_pnl is None or day_pnl <= -policy["max_daily_loss_usd"]):
            return "Live agent daily loss limit reached or daily P&L unavailable"
        return None

    def reserve(self, signal, cfg):
        """Caller holds execution + data locks. A crash consumes, never repeats, an attempt."""
        raw = self.load()
        key = self.key(cfg)
        if signal["id"] in raw["attempts"]:
            raise ValueError("Agent decision already reserved")
        row = raw["days"].setdefault(key, {"research": 0, "orders": 0})
        if row["orders"] >= cfg["live_agent"]["policy"]["max_orders_per_day"]:
            raise ValueError("Live agent daily broker-attempt limit reached")
        row["orders"] += 1
        raw["attempts"][signal["id"]] = {"key": key, "revision": signal["agent_revision"]}
        self.save(raw)

    def tick(self):
        if not self.cycle_lock.acquire(blocking=False):
            return
        try:
            self._tick()
        except Exception as exc:
            # Never retry an uncertain broker submission. Its durable intent is
            # reconciled by the existing background worker before another cycle.
            self.phase, self.message = "error", type(exc).__name__+": "+str(exc)[:180]
            self.desk.append_journal("live_agent_error", {"error": self.message})
        finally:
            self.cycle_lock.release()

    def _tick(self):
        with self.desk._lock:
            cfg = copy.deepcopy(self.desk.load_config())
        saved = cfg.get("live_agent")
        if not saved:
            return
        if not saved.get("enabled") or cfg.get("mode") != "auto_live" or not cfg.get("session_active"):
            self.phase, self.message = "paused", "Agent paused; broker reconciliation continues"
            return
        if not paper_loop.is_rth(now_utc()):
            self.phase, self.message = "waiting_for_market", "Waiting for the next regular US market session"
            return
        policy = validate(saved["policy"])
        with self.desk._lock:
            raw = self.load()
            if self.desk._CORRUPT_PATHS:
                raise ValueError("Desk data needs recovery")
            if self.desk.load_ledger().get("pending_broker_orders"):
                self.phase, self.message = "reconciling", "A broker order is unresolved; waiting for broker evidence"
                return
            if raw.get("next_at") and now_utc() < datetime.fromisoformat(raw["next_at"]):
                return
            today = raw["days"].setdefault(self.key(cfg), {"research": 0, "orders": 0})
            if today["research"] >= policy["max_research_per_day"] or today["orders"] >= policy["max_orders_per_day"]:
                self.phase, self.message = "daily_limit", "Daily agent research or broker-attempt limit reached"
                return
            symbol = policy["symbols"][raw["cursor"] % len(policy["symbols"])]
            raw["cursor"] += 1
            today["research"] += 1
            raw["next_at"] = (now_utc()+timedelta(seconds=policy["interval_sec"])).isoformat()
            self.save(raw)  # Failures/no setup/restarts also consume this cycle.
        import broker_router
        context = broker_router.verify_execution_context()
        if not context.get("ok") or context.get("identity") != cfg.get("broker_identity"):
            self.record("blocked", "Broker identity is unavailable or changed")
            return
        if context["identity"].get("broker") != "ibkr":
            self.record("blocked", "This agent version requires the IBKR Gateway adapter")
            return
        cost = self.desk.llm_trader.model_cost_today().get("model_usd")
        if number(cost, "Recorded AI cost", 0, 1e12) >= Decimal(str(policy["model_budget_usd"])):
            self.record("budget", "Recorded desk AI budget reached; live research is paused")
            return
        def authorized():
            return self.desk.load_config() == cfg and paper_loop.is_rth(now_utc())
        self.phase, self.message = "researching", "Researching "+symbol
        signal = self.desk.generate_scan_signal(cfg, ticker=symbol, force=False, still_authorized=authorized)
        if not authorized():
            self.record("discarded", "Settings or session changed during research")
            return
        if not signal:
            self.record("no_setup", "No qualifying setup for "+symbol)
            return
        signal.update(source="live_agent", workspace="live", agent_revision=saved["revision"],
                      agent_run_id=saved.get("run_id"),
                      agent_identity=copy.deepcopy(cfg["broker_identity"]))
        # The quote's own timestamp, not collection completion, starts validity.
        error = quote_error(signal.get("quote"), policy["max_quote_age_sec"])
        if not error:
            stamp = datetime.fromisoformat(signal["quote"]["market_time"].replace("Z", "+00:00"))
            signal["expires_at"] = (stamp+timedelta(seconds=policy["max_quote_age_sec"])).isoformat()
        error = error or self.desk.signal_execution_block(signal, broker=True)
        if error:
            self.record("hold", error, signal)
            return
        # No paper clone, no reuse of the rehearsal plan, one execution owner.
        result = self.desk._ingest_one_signal(signal, cfg_override=cfg)
        with self.desk._lock:
            unresolved = any((row.get("signal") or {}).get("id") == result.get("id")
                             for row in self.desk.load_ledger().get("pending_broker_orders") or [])
        if unresolved:
            self.record("broker_pending", "Partial fill; remainder unresolved" if result.get("fill") else
                        "Broker submission unresolved; reconciliation continues", result)
            return
        self.record(result.get("status", "unknown"), result.get("reject_reason") or
                    ("Broker execution recorded" if result.get("fill") else "Decision retained"), result)


def register(app, desk):
    service = LiveAgent(desk)
    bp = Blueprint("live_agent", __name__)

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/live-agent")
    def status():
        return jsonify(service.status())

    @bp.post("/api/live-agent/policy")
    def policy():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"policy", "revision"}:
            raise ValueError("Policy and its current revision are required")
        proposed = validate(body["policy"])
        with desk._BROKER_EXEC_LOCK, desk._lock:
            cfg = desk.load_config()
            if desk._CORRUPT_PATHS:
                raise ValueError("Repair unreadable desk data before saving a policy")
            if (cfg.get("live_agent") or {}).get("revision") != body["revision"]:
                return jsonify(ok=False, error="Policy changed in another window; reload before saving"), 409
            cfg["live_agent"] = {"policy": proposed, "revision": str(uuid.uuid4()), "enabled": False}
            desk.save_config(cfg)
        service.phase, service.message = "paused", "Policy saved; agent paused"
        return jsonify(service.status())

    @bp.post("/api/live-agent/start")
    def start():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) != {"revision", "identity", "confirm"}:
            raise ValueError("Confirm the exact saved policy and displayed broker account")
        import broker_router
        context = broker_router.verify_execution_context()
        identity = context.get("identity")
        if (not context.get("ok") or not identity or identity.get("broker") != "ibkr" or
                identity != body["identity"] or type(identity.get("paper_mode")) is not bool):
            raise ValueError("IBKR account changed or is unavailable; refresh the account before activation")
        if body["confirm"] != ("PAPER" if identity["paper_mode"] else "REAL"):
            raise ValueError("Type PAPER for the broker simulator or REAL for the actual account")
        daily, equity, error = desk._broker_day_pnl()
        if error or daily is None or equity is None:
            raise ValueError(error or "Verified broker equity and daily P&L are required before activation")
        number(daily, "Broker daily P&L", -1e12, 1e12)
        number(equity, "Broker equity", .01, 1e12)
        with desk._BROKER_EXEC_LOCK, desk._lock:
            cfg = desk.load_config()
            saved = cfg.get("live_agent")
            service.load()
            if desk._CORRUPT_PATHS or desk.load_ledger().get("pending_broker_orders"):
                raise ValueError("Resolve unreadable state or unresolved broker orders before activation")
            if not saved or saved.get("revision") != body["revision"] or cfg.get("broker_identity") != identity:
                return jsonify(ok=False, error="Policy or account changed; reload before activation"), 409
            validate(saved["policy"])
            if daily <= -saved["policy"]["max_daily_loss_usd"]:
                raise ValueError("Agent daily loss stop is already reached")
            saved["enabled"] = True
            saved["run_id"] = str(uuid.uuid4())
            cfg.update(mode="auto_live", session_active=True, broker_identity=identity, session_started_at=desk._now_iso())
            desk.save_config(cfg)
            desk.append_journal("live_agent_started", {"revision": saved["revision"], "paper_mode": identity["paper_mode"]})
        service.phase, service.message = "enabled", "Agent enabled; waiting for the next eligible research cycle"
        return jsonify(service.status())

    @bp.post("/api/live-agent/pause")
    def pause():
        with desk._BROKER_EXEC_LOCK, desk._lock:
            cfg = desk.load_config()
            if cfg.get("live_agent"):
                cfg["live_agent"]["enabled"] = False
                desk.save_config(cfg)
        service.phase, service.message = "paused", "Paused; existing broker orders and positions remain at the broker"
        return jsonify(service.status())

    app.register_blueprint(bp)
    return service
