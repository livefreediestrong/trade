"""Persistent, opt-in broker agent. The existing desk owns submission/reconciliation.

Research may suggest buy/sell/hold; deterministic policy owns every order term.
Saving a policy pauses the agent. Only the explicit start route activates it.
"""
from __future__ import annotations

import copy
import os
import hashlib
import json
import re
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from flask import Blueprint, jsonify, request

import paper_loop
from order_terms import canonical_order
from trade_planner import number
import auto_live_options
import market_events

DEFAULTS = {
    # `symbols` is a saved UI hint; live AUTO-ORDERS follow research_universe()
    # (full equity-scrubbed watchlist + radar merge). Not SPY-only.
    "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "TSLA"],
    "interval_sec": 120, "order_type": "limit",
    # Uncapped sentinels (UI treats >=1e8 USD / >=1e5 trades as open). Never school $10/$2/1.
    "max_order_usd": 1e9, "max_daily_loss_usd": 1e9, "max_orders_per_day": 100000,
    "max_research_per_day": 100, "model_budget_usd": 5.,
    "limit_offset_bps": 5., "min_confidence": .6, "max_quote_age_sec": 30,
    # Protective exits for positions this agent opened (stocks): the planned stop and
    # target, a breakeven stop after the price moves this many R in favor (0 = off),
    # an optional maximum hold, and a flatten before the close (0 = off).
    "protective_exits": True, "breakeven_after_r": 1.0, "max_hold_min": 0,
    "flatten_before_close_min": 10,
}
# Fields added after policies were first saved; older saved policies get these defaults.
_ADDED_FIELDS = ("protective_exits", "breakeven_after_r", "max_hold_min", "flatten_before_close_min")

# Bad/delisted/unquotable symbols: skip for a while instead of blocking forever.
_QUOTE_SKIP: dict[str, dict] = {}
_QUOTE_SKIP_AFTER = 2  # consecutive quote failures before temporary deny
_QUOTE_SKIP_SEC = 6 * 3600

def _quote_skip_active(symbol: str) -> bool:
    row = _QUOTE_SKIP.get(symbol)
    if not row:
        return False
    if now_utc().timestamp() >= float(row.get("until", 0)):
        _QUOTE_SKIP.pop(symbol, None)
        return False
    return True

def _note_quote_failure(symbol: str, reason: str):
    row = _QUOTE_SKIP.get(symbol) or {"fails": 0}
    row["fails"] = int(row.get("fails") or 0) + 1
    row["reason"] = (reason or "")[:160]
    hard = any(k in (reason or "").lower() for k in (
        "could not be qualified", "no ibkr trade", "invalid us stock", "unqualified"))
    need = 1 if hard else _QUOTE_SKIP_AFTER
    if row["fails"] >= need:
        row["until"] = now_utc().timestamp() + _QUOTE_SKIP_SEC
    _QUOTE_SKIP[symbol] = row

def _note_quote_ok(symbol: str):
    _QUOTE_SKIP.pop(symbol, None)



def research_universe(cfg, policy=None):
    """Symbols Moss may evaluate AND auto-order (ideas / radar / ranking / live).

    Full equity-scrubbed watchlist (FX/crypto junk dropped via equity_loop_symbols).
    Not clamped to policy['symbols'] — that saved list is a UI hint only.
    Radar movers are merged in so hot names outside the saved list still rotate.
    Falls back to the saved symbols hint only when the watchlist is empty.
    """
    policy = policy or {}
    try:
        wl = list((cfg or {}).get("watchlist") or [])
        symbols = paper_loop.equity_loop_symbols(wl)
        try:
            import market_radar as _mr
            hot = [
                str(r.get("ticker") or "").upper()
                for r in (_mr.get_cached().get("movers") or [])
                if r.get("ticker")
            ]
            symbols = paper_loop.merge_radar_into_focus(symbols, hot, cap_extra=12)
        except Exception:
            pass
    except Exception:
        symbols = []
    if not symbols:
        symbols = list(dict.fromkeys(
            s for s in (policy.get("symbols") or list(DEFAULTS["symbols"]))
            if isinstance(s, str) and s
        ))
    return [s for s in symbols if not _quote_skip_active(s)]


def order_universe(cfg, policy=None):
    """Live auto-order allow-list — same full evaluated equity universe."""
    return research_universe(cfg, policy)


def now_utc():
    return datetime.now(timezone.utc)


def validate(body):
    if isinstance(body, dict):
        body = {**{k: DEFAULTS[k] for k in _ADDED_FIELDS if k not in body}, **body}
    if not isinstance(body, dict) or set(body) != set(DEFAULTS):
        raise ValueError("Save all displayed live-agent policy fields; unknown fields are not accepted")
    policy = copy.deepcopy(body)
    symbols = policy["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 500:
        raise ValueError("Choose 1–500 uppercase US stock/ETF symbols")
    # Same scrub as equity watchlist (allows VIX1D / BRK.B; drops BTC/USD junk).
    cleaned = paper_loop.equity_loop_symbols([str(s).upper() for s in symbols if isinstance(s, str)])
    if not cleaned:
        raise ValueError("Choose 1–500 uppercase US stock/ETF symbols")
    policy["symbols"] = cleaned
    if policy["order_type"] not in ("market", "limit"):
        raise ValueError("Choose market or DAY limit orders")
    ranges = {"interval_sec": (30, 3600), "max_order_usd": (.01, 1e9),
              "max_daily_loss_usd": (.01, 1e9), "max_orders_per_day": (1, 100000),
              "max_research_per_day": (1, 10000), "model_budget_usd": (.01, 100000),
              "limit_offset_bps": (0, 100), "min_confidence": (0, 1), "max_quote_age_sec": (1, 60),
              "breakeven_after_r": (0, 10), "max_hold_min": (0, 1440), "flatten_before_close_min": (0, 120)}
    integers = {"interval_sec", "max_orders_per_day", "max_research_per_day", "max_quote_age_sec",
                "max_hold_min", "flatten_before_close_min"}
    if not isinstance(policy["protective_exits"], bool):
        raise ValueError("protective_exits must be true or false")
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
    allowed = order_universe(cfg, policy)
    if signal.get("ticker") not in allowed and not signal.get("agent_exit"):
        return "Symbol is outside the live agent's evaluated equity universe"
    return None


def quote_error(quote, max_age):
    try:
        source = str(quote.get("source") or "").lower()
        if not source or any(word in source for word in ("mock", "demo", "synthetic", "fixture", "unknown")):
            return "Live agent requires attributable real market data"
        number(quote.get("price"), "Quote", .000001, 1e9)
        stamp = datetime.fromisoformat(str(quote.get("market_time")).replace("Z", "+00:00"))
        age = (now_utc()-stamp).total_seconds()
        # IBKR delayed ticks may be slightly older than Finnhub; trust IB fresh flag up to 120s.
        limit = max(int(max_age), 120) if "ibkr" in source else int(max_age)
        if not quote.get("fresh") or not 0 <= age <= limit or not paper_loop.is_rth(stamp):
            return "Live agent quote is stale, future-dated or outside the session"
    except (ValueError, TypeError, AttributeError):
        return "Live agent quote is missing or unverified"
    return None


def _realtime_signal_quote(desk, symbol, max_age):
    """Real-time replacement for an IBKR-delayed quote, or None (keep IBKR)."""
    try:
        import data_sources as ds
        helper = getattr(desk, "_realtime_quote_or_none", None)
        quote = helper(ds, symbol) if callable(helper) else None
    except Exception:
        return None
    if not quote or quote_error(quote, max_age):
        return None
    # expires_at = market_time + max_age, so an older real-time print would
    # shrink the execution window below what the IBKR receipt stamp allows.
    # Only swap when most of that window remains.
    try:
        stamp = datetime.fromisoformat(str(quote.get("market_time")).replace("Z", "+00:00"))
        if (now_utc() - stamp).total_seconds() > min(10.0, float(max_age) / 3):
            return None
    except (ValueError, TypeError):
        return None
    return dict(quote, fresh=True, delayed=False)


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
    if signal.get("agent_exit"):
        return _exit_terms(signal, policy, price)
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


def _exit_terms(signal, policy, price):
    """Sell terms for a protective exit of an agent-opened position.

    Reducing only (the desk caps shares to what is held). Limit exits sit far
    enough below the market to fill in a moving market: 25 bps minimum, 50 bps
    for stop-loss and end-of-day exits.
    """
    if signal.get("side") != "sell":
        raise ValueError("Protective exits only sell agent-opened long positions")
    number(price, "Execution price", .000001, 1e9)
    shares = number(signal.get("suggested_shares"), "Exit shares", 1, 1e9)
    options = {"type": policy["order_type"]}
    if options["type"] == "limit":
        urgent = signal.get("agent_exit") in ("stop_loss", "breakeven_stop", "end_of_day")
        offset_bps = max(Decimal(str(policy["limit_offset_bps"])), Decimal(50 if urgent else 25))
        limit = Decimal(str(signal["quote"]["price"])) * (1 - offset_bps / 10000)
        quantum = Decimal(".01") if limit >= 1 else Decimal(".0001")
        options["limit_price"] = float(limit.quantize(quantum, rounding=ROUND_UP))
    return canonical_order(dict(signal, suggested_shares=int(shares)), options)


def _event_window(cfg):
    """The scheduled high-impact event pausing new agent entries now, if any. Calendar faults never block."""
    try:
        return market_events.active_window(cfg)
    except Exception:  # noqa: BLE001
        return None


def earnings_block(signal, cfg, policy, now=None):
    """No new agent buy in a stock reporting today, or before the next open when held overnight."""
    if not cfg.get("event_guard_enabled", True) or signal.get("side") != "buy" or signal.get("agent_exit"):
        return None
    earn = signal.get("earnings")
    if not isinstance(earn, dict) or not earn.get("date"):
        return None
    try:
        day = date.fromisoformat(str(earn["date"])[:10])
    except ValueError:
        return None
    today = (now or now_utc()).astimezone(paper_loop.NY_TZ).date() if paper_loop.NY_TZ else (now or now_utc()).date()
    ticker = signal.get("ticker")
    if day == today:
        return f"{ticker} reports earnings today; Fox opens nothing new in it"
    following = today + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    holds_overnight = not (policy.get("protective_exits") and int(policy.get("flatten_before_close_min") or 0) > 0)
    if day == following and holds_overnight:
        return f"{ticker} reports earnings before the next session and Fox is set to hold overnight"
    return None


def _minutes_to_close(now):
    et = now.astimezone(paper_loop.NY_TZ) if paper_loop.NY_TZ else now
    close = paper_loop.session_close_time(et.date())
    if close is None:
        return None
    return (datetime.combine(et.date(), close, tzinfo=et.tzinfo) - et).total_seconds() / 60


def exit_decision(row, bid, now, policy, minutes_to_close):
    """(reason or None, updates) for one managed position at the current bid.

    Reasons: stop_loss / breakeven_stop (bid at or below the stop), take_profit
    (bid at or above the target), max_hold, end_of_day.
    """
    updates = {}
    entry, stop = float(row["entry"]), float(row["stop"])
    risk = float(row.get("risk_per_share") or entry - stop)
    trigger_r = float(policy.get("breakeven_after_r") or 0)
    if trigger_r > 0 and not row.get("breakeven") and risk > 0 and bid >= entry + trigger_r * risk:
        stop = max(stop, entry)
        updates.update(stop=round(stop, 4), breakeven=True)
    if bid <= stop:
        return ("breakeven_stop" if (row.get("breakeven") or updates.get("breakeven")) else "stop_loss"), updates
    if bid >= float(row["target"]):
        return "take_profit", updates
    exit_at = row.get("exit_at")
    if exit_at and now >= datetime.fromisoformat(exit_at):
        return "max_hold", updates
    before = int(policy.get("flatten_before_close_min") or 0)
    if before > 0 and minutes_to_close is not None and minutes_to_close <= before:
        return "end_of_day", updates
    return None, updates


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
            pol = saved.get("policy") or copy.deepcopy(DEFAULTS)
            if isinstance(pol, dict):  # policies saved before protective exits existed
                pol = {**{k: DEFAULTS[k] for k in _ADDED_FIELDS}, **pol}
            try:
                eval_syms = research_universe(cfg, pol if isinstance(pol, dict) else {})
            except Exception:
                eval_syms = []
            order_syms = list(eval_syms)  # live auto-orders track full evaluated universe
            ao = auto_live_options.status_payload(cfg)
            return {"ok": True, "configured": bool(saved), "enabled": saved.get("enabled") is True,
                    "policy": pol, "revision": saved.get("revision"),
                    "identity": cfg.get("broker_identity"), "mode": cfg.get("mode"),
                    "session_active": cfg.get("session_active"), "market_open": paper_loop.is_rth(now_utc()),
                    "phase": self.phase, "message": message, "busy": self.cycle_lock.locked(),
                    "today": raw["days"].get(self.key(cfg), {"research": 0, "orders": 0}),
                    "events": raw["events"][:30], "next_at": raw.get("next_at"),
                    "managed": raw.get("managed") or {},
                    "eval_symbols_count": len(eval_syms),
                    "eval_symbols_sample": eval_syms[:12],
                    "order_symbols": order_syms,
                    "spy_only": False,
                    "auto_options": ao.get("auto_options"),
                    "auto_options_label": ao.get("label"),
                    "auto_options_armed": ao.get("armed"),
                    "eval_note": (
                        f"Live auto-orders + eval: full equity watchlist ({len(eval_syms)} symbols; junk deny-list on)"
                    )}

    def _track_entry(self, signal, fill, policy):
        """Remember an agent stock buy so its planned stop/target can be enforced."""
        try:
            shares = float(fill.get("shares") or 0)
            price = float(fill.get("price") or 0)
        except (TypeError, ValueError):
            return
        if (signal.get("side") != "buy" or shares <= 0 or price <= 0
                or str(signal.get("asset_type") or "STK").upper() in ("OPT", "BAG")):
            return
        ref = float(signal.get("signal_price") or price)
        stop, target = signal.get("stop"), signal.get("target")
        # Keep the planned distances, re-anchored to the actual fill price.
        stop_dist = ref - float(stop) if isinstance(stop, (int, float)) and stop < ref else ref * 0.008
        target_dist = float(target) - ref if isinstance(target, (int, float)) and target > ref else stop_dist * 2
        ticker = str(signal.get("ticker") or "").upper()
        now = now_utc()
        with self.desk._lock:
            raw = self.load()
            managed = raw.setdefault("managed", {})
            row = managed.get(ticker)
            if row:  # add to an existing agent position: blend the entry
                total = float(row["shares"]) + shares
                price = (float(row["entry"]) * float(row["shares"]) + price * shares) / total
                shares = total
            managed[ticker] = {
                "shares": shares, "entry": round(price, 4),
                "stop": round(price - stop_dist, 4), "target": round(price + target_dist, 4),
                "risk_per_share": round(stop_dist, 4), "breakeven": False,
                "opened_at": (row or {}).get("opened_at") or now.isoformat(),
                "exit_at": ((now + timedelta(minutes=policy["max_hold_min"])).isoformat()
                            if policy["max_hold_min"] else None),
                "signal_id": signal.get("id"),
            }
            self.save(raw)

    def _manage_exits(self, cfg, policy):
        """Close agent positions that hit their stop/target/hold/close rule. True if an order went out."""
        import broker_router
        with self.desk._lock:
            managed = dict(self.load().get("managed") or {})
            if not managed or self.desk.load_ledger().get("pending_broker_orders"):
                return False
        try:
            context = broker_router.verify_execution_context()
            if not context.get("ok") or context.get("identity") != cfg.get("broker_identity"):
                return False  # never send an exit to an account the policy was not approved for
            positions = broker_router.get_positions()
        except Exception:  # noqa: BLE001 - unreadable account: try again next tick
            return False
        if not isinstance(positions, dict) or not positions.get("ok"):
            return False
        held = {}
        for row in positions.get("positions") or []:
            try:
                qty = float(row.get("qty") or 0)
            except (TypeError, ValueError):
                continue
            # A short is never "held" for a protective sell (the desk also refuses it).
            held[str(row.get("symbol") or "").upper()] = -abs(qty) if str(row.get("side") or "").lower() == "short" else qty
        now = now_utc()
        to_close = _minutes_to_close(now)
        for ticker, row in managed.items():
            shares = int(min(float(row["shares"]), held.get(ticker, 0.0)))
            if shares < 1:  # sold elsewhere (or never filled): stop managing it
                self._update_managed(ticker, None)
                continue
            retry_after = row.get("retry_after")
            if retry_after and now < datetime.fromisoformat(retry_after):
                continue  # a recent exit attempt was refused; do not hammer the gates
            try:
                quote = broker_router.stock_quote(ticker)
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(quote, dict) or not (quote.get("ok") and quote.get("fresh") and quote.get("price")) or quote.get("delayed"):
                continue  # never act on a stale or delayed price
            bid = quote.get("bid") if isinstance(quote.get("bid"), (int, float)) and quote.get("bid") > 0 else quote["price"]
            reason, updates = exit_decision(row, float(bid), now, policy, to_close)
            if updates:
                self._update_managed(ticker, updates)
            if not reason:
                continue
            signal = {
                "id": str(uuid.uuid4()), "ticker": ticker, "side": "sell", "suggested_shares": shares,
                "source": "live_agent", "workspace": "live", "agent_exit": reason, "verdict": "EXIT",
                "confidence": 1.0, "signal_price": float(bid), "status": "pending",
                "reason": f"Protective exit ({reason.replace('_', ' ')}) of an agent position: entry "
                          f"{row['entry']}, stop {updates.get('stop', row['stop'])}, target {row['target']}, bid {bid}",
                "quote": {"price": float(bid), "source": quote.get("source") or "IBKR", "market_time": quote.get("market_time"),
                          "received_at": quote.get("received_at"), "fresh": True, "bid": quote.get("bid"),
                          "ask": quote.get("ask"), "con_id": quote.get("con_id")},
                "agent_revision": cfg["live_agent"]["revision"], "agent_run_id": cfg["live_agent"].get("run_id"),
                "agent_identity": copy.deepcopy(cfg["broker_identity"]),
                "created_at": now.isoformat(), "ts": now.isoformat(),
                "expires_at": (now + timedelta(seconds=policy["max_quote_age_sec"])).isoformat(),
            }
            result = self.desk._ingest_one_signal(signal, cfg_override=cfg)
            fill = result.get("fill") if isinstance(result.get("fill"), dict) else None
            if fill:
                remaining = float(row["shares"]) - float(fill.get("shares") or 0)
                self._update_managed(ticker, {"shares": remaining, "retry_after": None} if remaining >= 1 else None)
            else:
                self._update_managed(ticker, {"retry_after": (now + timedelta(seconds=60)).isoformat()})
            self.record("exit_" + reason, result.get("reject_reason") or result.get("error") or
                        (f"Protective {reason.replace('_', ' ')} exit sent for {shares} {ticker}"), signal, fill)
            return True
        return False

    def _update_managed(self, ticker, updates):
        with self.desk._lock:
            raw = self.load()
            managed = raw.setdefault("managed", {})
            if updates is None:
                managed.pop(ticker, None)
            elif ticker in managed:
                managed[ticker].update(updates)
            self.save(raw)

    def record(self, status, message, signal=None, fill=None):
        self.phase, self.message = status, message
        event = {"at": now_utc().isoformat(), "status": status, "message": message,
                 "signal_id": (signal or {}).get("id"), "ticker": (signal or {}).get("ticker")}
        if (signal or {}).get("side") in ("buy", "sell"):
            event["side"] = signal["side"]
        if (signal or {}).get("agent_exit"):
            event["exit_reason"] = signal["agent_exit"]
        if isinstance(fill, dict):
            event["fill"] = {"shares": fill.get("shares"), "price": fill.get("price")}
        with self.desk._lock:
            raw = self.load()
            raw["events"].insert(0, event)
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
            if (not signal.get("agent_exit")
                    and raw["days"].get(self.key(cfg), {}).get("orders", 0) >= policy["max_orders_per_day"]):
                return "Live agent reached its daily broker-attempt limit"
        if not reducing and (day_pnl is None or day_pnl <= -policy["max_daily_loss_usd"]):
            return "Live agent daily loss limit reached or daily P&L unavailable"
        if not reducing:
            window = _event_window(cfg)
            if window:
                return market_events.window_message(window)
            earnings = earnings_block(signal, cfg, policy)
            if earnings:
                return earnings
        return None

    def reserve(self, signal, cfg):
        """Caller holds execution + data locks. A crash consumes, never repeats, an attempt."""
        raw = self.load()
        key = self.key(cfg)
        if signal["id"] in raw["attempts"]:
            raise ValueError("Agent decision already reserved")
        row = raw["days"].setdefault(key, {"research": 0, "orders": 0})
        if not signal.get("agent_exit") and row["orders"] >= cfg["live_agent"]["policy"]["max_orders_per_day"]:
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
        # Optional scheduled soft PnL refresh while auto_live and not risk_ready.
        # Never invents Ready; only re-requests Daily P&L via soft API reconnect.
        try:
            import broker_router
            minutes = float(os.environ.get("IBKR_PNL_SOFT_REFRESH_MINUTES", "5") or 0)
            book = None
            try:
                book = broker_router.get_account()
            except Exception:
                book = None
            ready = bool(book and book.get("risk_ready") is True and book.get("account", {}).get("day_pnl") is not None)
            if minutes > 0 and not ready:
                # If Gateway went away after signing in, relaunch it once (login/2FA
                # may still be needed). Only runs during an active auto_live session and
                # never reopens a login window closed before sign-in.
                ensure = getattr(broker_router, "ensure_gateway", None)
                if callable(ensure):
                    try:
                        ensure(launch_if_down=True, automatic=True)
                    except Exception:
                        pass
                refresher = getattr(broker_router, "maybe_scheduled_soft_refresh", None)
                if callable(refresher):
                    refresher(interval_minutes=minutes, auto_live=True, risk_ready=False)
        except Exception:
            pass
        if not paper_loop.is_rth(now_utc()):
            self.phase, self.message = "waiting_for_market", "Waiting for the next regular US market session"
            return
        policy = validate(saved["policy"])
        if policy["protective_exits"] and self._manage_exits(cfg, policy):
            return  # an exit went out this tick; new research waits for the next one
        window = _event_window(cfg)
        if window:
            self.phase, self.message = "event_window", market_events.window_message(window)
            return  # no research spend while new entries are paused; exits ran above
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
            eval_symbols = research_universe(cfg, policy)
            if not eval_symbols:
                self.phase, self.message = "blocked", "No equity symbols available to evaluate"
                return
            symbol = eval_symbols[raw["cursor"] % len(eval_symbols)]
            raw["cursor"] += 1
            today["research"] += 1
            raw["next_at"] = (now_utc()+timedelta(seconds=policy["interval_sec"])).isoformat()
            self.save(raw)  # Failures/no setup/restarts also consume this cycle.
        # Live auto-orders use the same full evaluated equity universe (not SPY-only).
        import broker_router
        context = broker_router.verify_execution_context()
        if not context.get("ok") or context.get("identity") != cfg.get("broker_identity"):
            self.record("blocked", "Broker identity is unavailable or changed")
            return
        if context["identity"].get("broker") != "ibkr":
            self.record("blocked", "This agent version requires the IBKR Gateway adapter")
            return
        usage = self.desk.llm_trader.model_cost_today()
        # Budget counts only live-agent research (paper Moss has its own budget).
        # Older ledgers without scopes count as zero live spend: never a block.
        scoped = (usage.get("scopes") or {}).get("live_agent") or {}
        cost = scoped.get("model_usd") or 0.0
        if number(cost, "Recorded AI cost", 0, 1e12) >= Decimal(str(policy["model_budget_usd"])):
            self.record("budget", "Recorded desk AI budget reached; live research is paused")
            return
        def authorized():
            return self.desk.load_config() == cfg and paper_loop.is_rth(now_utc())
        self.phase, self.message = "researching", "Evaluating "+symbol+" (live-order eligible · full universe)"
        scope = getattr(self.desk.llm_trader, "cost_scope", None)
        if callable(scope):
            with scope("live_agent"):
                signal = self.desk.generate_scan_signal(cfg, ticker=symbol, force=False, still_authorized=authorized)
        else:
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
        # Prefer a fresh IBKR live/delayed quote over Yahoo/Finnhub for execution gates.
        try:
            import broker_router
            ibq = broker_router.stock_quote(symbol)
            if ibq.get("ok") and ibq.get("fresh") and ibq.get("price"):
                signal["quote"] = {
                    "price": ibq["price"], "source": ibq.get("source") or "IBKR delayed mkt data",
                    "market_time": ibq.get("market_time"), "received_at": ibq.get("received_at"),
                    "age_sec": ibq.get("age_sec"), "fresh": True,
                    "bid": ibq.get("bid"), "ask": ibq.get("ask"), "last": ibq.get("last"),
                    "con_id": ibq.get("con_id"), "delayed": bool(ibq.get("delayed")),
                    "market_data_type": ibq.get("market_data_type"),
                }
                signal["signal_price"] = ibq["price"]
                _note_quote_ok(symbol)
                if ibq.get("delayed"):
                    # Delayed IBKR prices trail the market ~15 min. Prefer a
                    # real-time quote that passes the same gate; if none does,
                    # keep the IBKR quote so the order path is never blocked.
                    realtime = _realtime_signal_quote(self.desk, symbol, policy["max_quote_age_sec"])
                    if realtime:
                        signal["quote"] = realtime
                        signal["signal_price"] = realtime["price"]
            else:
                err = (ibq.get("error") or "ibkr_quote_unavailable")[:160]
                _note_quote_failure(symbol, err)
                # Junk / unqualified / no delayed tick: skip without burning cycles on stale Finnhub.
                if _quote_skip_active(symbol) or any(k in err.lower() for k in (
                        "could not be qualified", "no ibkr trade", "invalid us stock")):
                    self.record("hold", f"IBKR quote unavailable ({err}) — symbol temporarily skipped", signal)
                    return
        except Exception as exc:
            _note_quote_failure(symbol, type(exc).__name__)
            if _quote_skip_active(symbol):
                self.record("hold", f"IBKR quote error ({type(exc).__name__}) — symbol temporarily skipped", signal)
                return
        # The quote's own timestamp, not collection completion, starts validity.
        error = quote_error(signal.get("quote"), policy["max_quote_age_sec"])
        if error:
            _note_quote_failure(symbol, error)
            self.record("hold", error + (" — symbol temporarily skipped" if _quote_skip_active(symbol) else ""), signal)
            return
        stamp = datetime.fromisoformat(signal["quote"]["market_time"].replace("Z", "+00:00"))
        signal["expires_at"] = (stamp+timedelta(seconds=policy["max_quote_age_sec"])).isoformat()
        error = self.desk.signal_execution_block(signal, broker=True) or earnings_block(signal, cfg, policy)
        if error:
            self.record("hold", error, signal)
            return
        # Optional: convert stock PASS into armed OPT/BAG when auto_options.enabled.
        auto_cfg = auto_live_options.get_config(cfg)
        if auto_cfg.get("enabled") and signal.get("verdict") == "PASS":
            try:
                with self.desk._lock:
                    day = self.load()["days"].get(self.key(cfg), {"research": 0, "orders": 0})
                if day.get("research", 0) % max(1, int(auto_cfg.get("every_n_stock_cycles") or 1)) == 0:
                    signal = self._maybe_convert_to_option(signal, cfg, auto_cfg, context)
            except (ValueError, TypeError, KeyError) as exc:
                self.record("options_skip", str(exc)[:180], signal)
                return
        # No paper clone, no reuse of the rehearsal plan, one execution owner.
        result = self.desk._ingest_one_signal(signal, cfg_override=cfg)
        if isinstance(result.get("fill"), dict):
            self._track_entry(signal, result["fill"], policy)
        with self.desk._lock:
            unresolved = any((row.get("signal") or {}).get("id") == result.get("id")
                             for row in self.desk.load_ledger().get("pending_broker_orders") or [])
        if unresolved:
            self.record("broker_pending", "Partial fill; remainder unresolved" if result.get("fill") else
                        "Broker submission unresolved; reconciliation continues", result)
            return
        fill = result.get("fill") if isinstance(result.get("fill"), dict) else None
        if fill:
            verb = "Bought" if signal.get("side") == "buy" else "Sold"
            message = f"{verb} {float(fill.get('shares') or 0):g} {signal.get('ticker')} at ${float(fill.get('price') or 0):,.2f}"
        else:
            message = result.get("reject_reason") or "Decision retained"
        self.record(result.get("status", "unknown"), message, dict(signal, id=result.get("id") or signal.get("id")), fill)


    def _maybe_convert_to_option(self, signal, cfg, auto_cfg, context):
        """Attach OPT/BAG fields using IBKR chain + options_desk strategy names. No order yet."""
        import broker_router
        ticker = str(signal.get("ticker") or "").upper()
        px = float(signal.get("signal_price") or (signal.get("quote") or {}).get("price") or 0)
        if px <= 0:
            raise ValueError("Underlying price missing for options conversion")
        book = None
        try:
            book = broker_router.get_account()
        except Exception:
            book = None
        err = auto_live_options.risk_ready_error(book)
        if err:
            raise ValueError(err)
        stock_shares = 0.0
        try:
            pos = broker_router.get_positions()
            for row in (pos.get("positions") or []):
                if str(row.get("symbol") or "").upper() == ticker:
                    stock_shares = float(row.get("qty") or 0)
                    break
        except Exception:
            stock_shares = 0.0
        chain = broker_router.option_chain(ticker)
        if not chain.get("ok"):
            raise ValueError(chain.get("error") or "Option chain unavailable")
        # Normalize chain shape from broker_ibkr.option_chain
        classes = chain.get("chains") or chain.get("classes") or chain.get("option_classes") or []
        expirations, strikes = [], []
        if classes:
            for cls in classes:
                expirations.extend(cls.get("expirations") or cls.get("expiries") or [])
                strikes.extend(cls.get("strikes") or [])
        else:
            expirations = chain.get("expirations") or chain.get("expiries") or []
            strikes = chain.get("strikes") or []
        norm = {"expirations": expirations, "strikes": strikes, "classes": classes}
        candidate = auto_live_options.build_option_candidate(
            signal, auto_cfg, chain=norm, underlying_price=px, stock_shares=stock_shares,
        )
        # Quote the chosen structure for premium / con_id.
        if candidate["asset_type"] == "OPT":
            action = "BUY" if candidate["option_intent"] in ("BTO", "BTC") else "SELL"
            quotes = broker_router.option_quotes([{
                "symbol": ticker, "right": candidate["right"], "expiry": candidate["expiry"],
                "strike": candidate["strike"], "action": action,
            }])
            if not quotes.get("ok") or not (quotes.get("legs") or []):
                raise ValueError(quotes.get("error") or "Option quote unavailable")
            leg = quotes["legs"][0]
            if not leg.get("con_id") or int(leg.get("multiplier") or 0) != 100:
                raise ValueError("Only standard 100-share options can auto-trade")
            _require_live_option_data([leg])
            raw_premium = leg.get("ask") if action == "BUY" else leg.get("bid")
            try:
                premium = float(raw_premium or 0)
            except (TypeError, ValueError):
                premium = 0.0
            if premium <= 0:
                raise ValueError("Executable option premium missing")
            signal.update(candidate)
            signal["suggested_shares"] = candidate["contracts"]
            signal["signal_price"] = premium
            signal["ack_symbol"] = leg.get("local_symbol")
            signal["review_contract"] = {
                "con_id": int(leg["con_id"]), "symbol": ticker, "local_symbol": leg.get("local_symbol"),
                "right": candidate["right"], "expiry": candidate["expiry"], "strike": float(candidate["strike"]),
                "multiplier": 100, "currency": "USD", "sec_type": "OPT",
            }
            signal["quote"] = {
                "price": premium, "source": quotes.get("source") or "IBKR option quote",
                "market_time": quotes.get("received_at") or signal.get("quote", {}).get("market_time"),
                "bid": leg.get("bid"), "ask": leg.get("ask"), "fresh": True,
                "local_symbol": leg.get("local_symbol"), "con_id": int(leg["con_id"]), "multiplier": 100,
                # 1=live; delayed/frozen legs were refused above.
                "market_data_type": leg.get("market_data_type"),
                "delayed": leg.get("market_data_type") in (3, 4),
            }
            signal["reason"] = (signal.get("reason") or "") + f" | auto_options {candidate['option_strategy']} {candidate['option_intent']}"
            signal["broker_book"] = book
            return signal
        # BAG: quote both legs for net premium estimate
        from options_desk import legs as desk_legs
        plan = {
            "symbol": ticker, "expiry": candidate["expiry"], "strategy": candidate["option_strategy"],
            "right": candidate["right"], "kind": "debit" if "debit" in candidate["option_strategy"] else "credit",
            "long_strike": candidate["long_strike"], "short_strike": candidate["short_strike"],
            "contracts": candidate["contracts"], "multiplier": 100, "budget": 250, "fee_per_contract": 0.65,
        }
        quotes = broker_router.option_quotes(desk_legs(plan))
        if not quotes.get("ok") or len(quotes.get("legs") or []) != 2:
            raise ValueError(quotes.get("error") or "BAG leg quotes unavailable")
        rows = quotes["legs"]
        # options_desk.legs order is [long BUY, short SELL]; verify rather than assume.
        if rows[0].get("action") != "BUY" or rows[1].get("action") != "SELL":
            raise ValueError("BAG leg quotes returned in an unexpected order")
        _require_live_option_data(rows)
        try:
            debit = float(rows[0]["ask"]) - float(rows[1]["bid"])
        except (TypeError, ValueError, KeyError):
            raise ValueError("BAG leg bid/ask missing")
        premium = abs(debit)
        if premium <= 0:
            raise ValueError("BAG net premium missing or non-positive")
        signal.update(candidate)
        signal["suggested_shares"] = candidate["contracts"]
        signal["signal_price"] = premium
        signal["review_contract"] = {
            "symbol": ticker, "right": candidate["right"], "expiry": candidate["expiry"],
            "long_strike": candidate["long_strike"], "short_strike": candidate["short_strike"],
            "legs": [
                {"con_id": int(rows[0]["con_id"]), "strike": candidate["long_strike"], "local_symbol": rows[0].get("local_symbol")},
                {"con_id": int(rows[1]["con_id"]), "strike": candidate["short_strike"], "local_symbol": rows[1].get("local_symbol")},
            ],
            "multiplier": 100, "sec_type": "BAG",
        }
        signal["ack_symbol"] = f"{ticker}-BAG-{candidate['option_strategy']}"
        signal["quote"] = {
            "price": premium, "source": quotes.get("source") or "IBKR option quote",
            "market_time": quotes.get("received_at") or signal.get("quote", {}).get("market_time"),
            "fresh": True, "net_debit": debit,
            "market_data_type": rows[0].get("market_data_type"),
            "delayed": any(r.get("market_data_type") in (3, 4) for r in rows),
        }
        signal["reason"] = (signal.get("reason") or "") + f" | auto_options BAG {candidate['option_strategy']}"
        signal["broker_book"] = book
        return signal



def _require_live_option_data(legs):
    """Auto options price limits and size risk from this quote, so it must be live.

    Stock tickets tolerate delayed IBKR data by owner decision, but option
    premiums move far faster than the underlying and the quote's receipt time
    would otherwise pass the freshness window on 15-minute-old prices.
    Types: 1=live, 2=frozen, 3=delayed, 4=delayed-frozen.
    """
    for leg in legs:
        try:
            kind = int(leg.get("market_data_type"))
        except (TypeError, ValueError):
            kind = None
        if kind != 1:
            raise ValueError(
                f"Option quote is not live market data (type {leg.get('market_data_type')}); "
                "auto options need a live options data subscription"
            )


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
        if not isinstance(body, dict) or not {"policy", "revision"} <= set(body) or set(body) - {"policy", "revision", "auto_options"}:
            raise ValueError("Policy and its current revision are required")
        proposed = validate(body["policy"])
        auto_opts = auto_live_options.validate(body["auto_options"]) if "auto_options" in body else None
        with desk._BROKER_EXEC_LOCK, desk._lock:
            cfg = desk.load_config()
            if desk._CORRUPT_PATHS:
                raise ValueError("Repair unreadable desk data before saving a policy")
            if (cfg.get("live_agent") or {}).get("revision") != body["revision"]:
                return jsonify(ok=False, error="Policy changed in another window; reload before saving"), 409
            prev = cfg.get("live_agent") or {}
            entry = {"policy": proposed, "revision": str(uuid.uuid4()), "enabled": False}
            if auto_opts is not None:
                entry["auto_options"] = auto_opts
            elif isinstance(prev.get("auto_options"), dict):
                entry["auto_options"] = prev["auto_options"]
            cfg["live_agent"] = entry
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
