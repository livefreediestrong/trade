"""Persistent, opt-in broker agent. The existing desk owns submission/reconciliation.

Research may suggest buy/sell/hold; deterministic policy owns every order term.
Saving a policy pauses the agent. Only the explicit start route activates it.
"""
from __future__ import annotations

import copy
from pathlib import Path
import os
import hashlib
import json
import math
import threading
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP

from flask import Blueprint, jsonify, request

import paper_loop
from order_terms import canonical_order
from trade_planner import number
import auto_live_options
import market_events
import stock_protection
import stop_fills
import stop_review
import option_lifecycle
import option_review
from risk_policy import effective_limits

DEFAULTS = {
    # `symbols` is a saved UI hint; live AUTO-ORDERS follow research_universe()
    # (full equity-scrubbed watchlist + radar merge). Not SPY-only.
    "symbols": ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "META", "TSLA"],
    "interval_sec": 120, "order_type": "limit",
    # Legacy extra ceilings. The account preset always supplies the effective cap.
    "max_order_usd": 1e9, "max_daily_loss_usd": 1e9, "max_orders_per_day": 100000,
    "max_research_per_day": 100, "model_budget_usd": 5.,
    "limit_offset_bps": 5., "min_confidence": .6, "max_quote_age_sec": 30,
    # Protective exits for positions this agent opened (stocks): the planned stop and
    # target, a breakeven stop after the price moves this many R in favor (0 = off),
    # an optional maximum hold, and a flatten before the close (0 = off).
    "protective_exits": True, "breakeven_after_r": 1.0, "max_hold_min": 0,
    "flatten_before_close_min": 10, "broker_stops": False,
    # Most agent stock positions (open or still filling) in one sector; 0 = off.
    "max_positions_per_sector": 2,
}
# Fields added after policies were first saved; older saved policies get these defaults.
_ADDED_FIELDS = ("protective_exits", "breakeven_after_r", "max_hold_min", "flatten_before_close_min",
                 "max_positions_per_sector", "broker_stops")

SCREEN_GAP_SEC = 30  # after a screen with no PASS (no model call), the next symbol comes up this soon
# This many different symbols in a row without history means the provider is down.
COVERAGE_OUTAGE_STREAK = 3
# A symbol whose one share exceeds the per-order limit is rechecked after this long.
UNAFFORDABLE_RECHECK_SEC = 3600
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



def research_universe(cfg, policy=None, *, include_unavailable=False):
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
            if not (cfg or {}).get("radar_enabled"):
                raise ValueError("radar is off")  # its display cache must not add order symbols
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
    symbols = _tradeable_listed(symbols)
    return symbols if include_unavailable else [s for s in symbols if not _quote_skip_active(s)]


_DESK = None  # set by register(); lets the universe read the listed-symbol directory
_LISTED_CACHE = {"key": None, "symbols": None}


def _listed_symbols():
    """US-listed stocks/ETFs from the desk's Nasdaq-traded directory, or None if unavailable."""
    if _DESK is None:
        return None
    try:
        path = Path(_DESK.DATA_DIR) / "market_universe.json"
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        if _LISTED_CACHE["key"] != key:
            import market_universe
            rows = (market_universe.load(_DESK) or {}).get("symbols") or []
            listed = {str(r.get("symbol") or "").upper() for r in rows if isinstance(r, dict) and r.get("symbol")}
            _LISTED_CACHE.update(key=key, symbols=listed if len(listed) >= 1000 else None)
        return _LISTED_CACHE["symbols"]
    except (OSError, ValueError, TypeError):
        return None


def _tradeable_listed(symbols):
    """Drop what this desk cannot trade intraday: OTC/foreign names, mutual funds and
    unknown tickers (absent from the US-listed directory) and leveraged/inverse ETFs.
    Without a usable directory only the leveraged/inverse filter applies."""
    import data_sources
    listed = _listed_symbols()
    return [s for s in symbols if not data_sources.is_leveraged_or_inverse(s)
            and (listed is None or s.upper() in listed)]


def order_universe(cfg, policy=None):
    """Live auto-order allow-list — same full evaluated equity universe."""
    return research_universe(cfg, policy)


def now_utc():
    return datetime.now(timezone.utc)


def account_scope(identity):
    """Stable ownership; reconnecting with a new client ID is the same account."""
    if (not isinstance(identity, dict) or not identity.get("account_id")
            or not identity.get("broker") or type(identity.get("paper_mode")) is not bool):
        return None
    return {k: identity[k] for k in ("broker", "account_id", "paper_mode")}


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
              "breakeven_after_r": (0, 10), "max_hold_min": (0, 1440), "flatten_before_close_min": (0, 120),
              "max_positions_per_sector": (0, 10)}
    integers = {"interval_sec", "max_orders_per_day", "max_research_per_day", "max_quote_age_sec",
                "max_hold_min", "flatten_before_close_min", "max_positions_per_sector"}
    if not isinstance(policy["protective_exits"], bool):
        raise ValueError("protective_exits must be true or false")
    if type(policy["broker_stops"]) is not bool:
        raise ValueError("broker_stops must be true or false")
    if policy["broker_stops"] and not policy["protective_exits"]:
        raise ValueError("Keep protective exits enabled for the broker-held stop paper test")
    for key, bounds in ranges.items():
        value = number(policy[key], key, *bounds)
        if key in integers and value != int(value):
            raise ValueError(f"{key} must be a whole number")
        policy[key] = int(value) if key in integers else float(value)
    return policy


def authorization_error(signal, cfg):
    saved = cfg.get("live_agent")
    tagged = signal.get("source") == "live_agent"
    if not saved and not tagged and cfg.get("mode") != "auto_live":
        return None
    if (tagged and signal.get("agent_exit") and not signal.get("agent_option_position_id")
            and isinstance(saved, dict) and not saved.get("enabled") and cfg.get("mode") == "auto_live"):
        # Pausing Fox or ending the session stops new risk, never the protective exits
        # for positions he opened. Live trading OFF (mode manual) still stops every order.
        if not cfg.get("broker_identity") or signal.get("agent_identity") != cfg.get("broker_identity"):
            return "Live agent authorization changed or this idea is outside its policy"
        if not paper_loop.is_rth(now_utc()):
            return "Live agent waits for regular US market hours"
        try:
            validate(saved["policy"])
        except (ValueError, KeyError, TypeError):
            return "Live agent policy is invalid"
        return None
    if not isinstance(saved, dict) or not saved.get("enabled"):
        return "Live agent is paused; enable the saved policy explicitly"
    if not saved.get("revision") or not saved.get("run_id"):
        return "Fox authorization is incomplete; explicitly start a saved policy"
    stock_exit = signal.get("agent_exit") and not signal.get("agent_option_position_id")
    if (cfg.get("mode") != "auto_live" or not (cfg.get("session_active") or stock_exit) or not tagged or
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
    if policy["broker_stops"] and (cfg.get("broker_identity") or {}).get("paper_mode") is not True:
        return "Broker-held stops are restricted to IBKR paper qualification"
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
        if quote.get("frozen") or "frozen" in source:
            return "Live agent cannot act on a frozen quote"
        if quote.get("delayed") or quote.get("market_data_type") in (3, 4) or "delayed" in source:
            return "Live orders require real-time prices; a recently received delayed quote is not current"
        limit = int(max_age)
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


def sector_block(signal, policy, managed, pending_orders):
    """Stop a new agent buy when the agent already holds enough names in that sector.

    Owner decision: like the event and WSB guards, this only reduces risk. A missing
    sector (ETFs, lookup failures) never blocks. Counts the agent's tracked positions
    and its buys still waiting at the broker; adding to a name already held is allowed.
    """
    cap = int(policy.get("max_positions_per_sector") or 0)
    sector = str(signal.get("sector") or "").strip()
    ticker = str(signal.get("ticker") or "").upper()
    if cap <= 0 or not sector or sector.lower() == "unknown" or signal.get("side") != "buy":
        return None
    held = set()
    for name, row in (managed or {}).items():
        try:
            open_shares = float((row or {}).get("shares") or 0) > 0
        except (TypeError, ValueError):
            open_shares = False
        if open_shares and (row or {}).get("sector") == sector:
            held.add(str(name).upper())
    for item in pending_orders or []:
        sig = (item or {}).get("signal") or {}
        if sig.get("source") == "live_agent" and sig.get("side") == "buy" and sig.get("sector") == sector:
            held.add(str(sig.get("ticker") or "").upper())
    held.discard(ticker)
    held.discard("")
    if len(held) < cap:
        return None
    names = ", ".join(sorted(held)[:4])
    return (f"Fox already holds {len(held)} {sector} position{'s' if len(held) != 1 else ''} ({names}); "
            f"the per-sector limit is {cap}, so he skips {ticker} to keep the account spread out")


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


# The PASS rule reads first-hour volume (09:30-10:30 ET), which stops changing at
# 10:30, and the backtest only tested entries at 10:30. Later entries have no evidence.
ENTRY_WINDOW_ET = (dtime(10, 30), dtime(11, 0))


def _entry_window_message(now=None):
    """None inside the automatic-entry window, else a plain explanation."""
    local = (now or now_utc()).astimezone(paper_loop.NY_TZ).time()
    start, end = ENTRY_WINDOW_ET
    if start <= local < end:
        return None
    return ("Automatic buys only happen from 10:30 to 11:00 a.m. Eastern, right after the first-hour volume "
            "is known (the only time the strategy was tested). Exits keep running all day.")


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
        # Breakeven means covering the round-trip commissions too, never above half
        # the trigger move (the stop must stay below the price that triggered it).
        cost = max(0.0, float(row.get("cost_per_share") or 0))
        stop = max(stop, entry + min(cost, 0.5 * trigger_r * risk))
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
        self._worker = None
        self.phase = "not_configured"
        self.message = "Save a policy to prepare the live agent"
        self.exit_checks = {}
        self._unaffordable = {}  # symbol -> monotonic time to recheck (in memory; resets on restart)

    @property
    def path(self):
        return self.desk.DATA_DIR / "live_agent.json"

    def load(self):
        raw = self.desk._load_json(self.path, {"days": {}, "attempts": {}, "events": [], "cursor": 0, "next_at": None})
        valid = (isinstance(raw, dict) and isinstance(raw.get("days"), dict) and
                 isinstance(raw.get("attempts"), dict) and isinstance(raw.get("events"), list) and
                 type(raw.get("cursor")) is int and raw["cursor"] >= 0)
        if valid:
            coverage = raw.get("research_coverage", {})
            valid = isinstance(coverage, dict) and len(coverage) <= 1000
            if valid:
                try:
                    valid = all(isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("reason"), str)
                                and datetime.fromisoformat(v["retry_at"]).tzinfo is not None for k, v in coverage.items())
                except (TypeError, ValueError, KeyError):
                    valid = False
        if valid:
            valid = isinstance(raw.get("managed", {}), dict) and isinstance(raw.get("managed_fill_totals", {}), dict)
            valid = valid and stock_protection.state_valid(raw.get("native_stops", {}))
            valid = valid and option_lifecycle.state_valid(raw.get("option_positions", {}))
            history = raw.get("native_stop_history", [])
            valid = (valid and isinstance(history, list)
                     and stock_protection.state_valid(dict(enumerate(history))))
            if valid:
                refs = [r["ref"] for r in list(raw.get("native_stops", {}).values()) + history]
                valid = len(refs) == len(set(refs))
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
                    "reviews": self.current_reviews(raw, cfg), "decision_owner": "Fox",
                    "managed": raw.get("managed") or {},
                    "option_positions": option_lifecycle.status(raw, cfg.get("broker_identity")),
                    "option_check_error": getattr(self, "option_check_error", None),
                    "stop_corrections": stop_review.pending(raw, cfg.get("broker_identity")),
                    "entry_evidence": self._safe_promotion(cfg),
                    "exit_checks": copy.deepcopy(self.exit_checks),
                    "account_scope": account_scope(cfg.get("broker_identity")),
                    "eval_symbols_count": len(eval_syms),
                    "eval_symbols_sample": eval_syms[:12],
                    "order_symbols": order_syms,
                    "spy_only": False,
                    "auto_options": ao.get("auto_options"),
                    "auto_options_label": ao.get("label"),
                    "auto_options_armed": ao.get("armed"),
                    "auto_options_entry_block": ao.get("entry_block"),
                    "auto_options_lifecycle": ao.get("lifecycle"),
                    "research_coverage": [dict(ticker=k, **v) for k, v in (raw.get("research_coverage") or {}).items()
                                          if datetime.fromisoformat(v["retry_at"]) > now_utc()],
                    "eval_note": (
                        f"Live auto-orders + eval: full equity watchlist ({len(eval_syms)} symbols; junk deny-list on)"
                    )}

    def current_reviews(self, raw, cfg):
        """Actual completed screens only, isolated by account/day; expire after one hour."""
        now = now_utc()
        rows = []
        reviews = raw.get("reviews")
        for row in reviews if isinstance(reviews, list) else []:
            if not isinstance(row, dict) or row.get("verdict") not in ("PASS", "WATCH", "AVOID", "UNAVAILABLE"):
                continue
            try:
                age = (now - datetime.fromisoformat(row["at"])).total_seconds()
                if row.get("scope") == self.key(cfg) and 0 <= age <= 3600:
                    rows.append({**row, "age_sec": round(age)})
            except (TypeError, ValueError, KeyError):
                continue
        return sorted(rows, key=lambda r: ({"PASS": 0, "WATCH": 1}.get(r["verdict"], 2), -datetime.fromisoformat(r["at"]).timestamp()))[:10]

    def note_review(self, cfg, symbol, signal, notes):
        from companion_evidence import decision_detail, daytime_case
        row = {"ticker": symbol, "at": now_utc().isoformat(), "scope": self.key(cfg),
               "verdict": (signal or {}).get("verdict") or notes.get("verdict") or "UNAVAILABLE",
               "reason": str((signal or {}).get("llm_thesis") or (signal or {}).get("reason") or notes.get("text") or notes.get("error") or "Assessment unavailable")[:500],
               "side": (signal or {}).get("llm_side") or "hold",
               "error": (signal or {}).get("data_error") or (signal or {}).get("llm_error") or notes.get("error"),
               "detail": decision_detail(signal or notes)}
        decision_id = (signal or {}).get("decision_record_id")
        if decision_id:
            decision = next((r for r in self.desk._decision_ring.latest(250) if r.get("id") == decision_id), None)
            row["case"] = daytime_case(decision, row["detail"])
        with self.desk._lock:
            raw = self.load()
            reviews = raw.get("reviews") if isinstance(raw.get("reviews"), list) else []
            raw["reviews"] = [row] + [r for r in reviews if isinstance(r, dict) and
                                     (r.get("ticker"), r.get("scope")) != (symbol, row["scope"])][:99]
            self.save(raw)

    def _track_entry(self, signal, fill, policy):
        """Apply a cumulative confirmed stock fill once, including later reconciliation."""
        if fill.get("confirmed") is False or fill.get("price_estimated") is True:
            return
        try:
            shares = float(fill.get("shares") or 0)
            price = float(fill.get("price") or 0)
        except (TypeError, ValueError):
            return
        if (not math.isfinite(shares) or not math.isfinite(price)
                or signal.get("side") not in ("buy", "sell") or shares < 0 or (shares and price <= 0)
                or str(signal.get("asset_type") or "STK").upper() in ("OPT", "BAG")):
            return
        owner = account_scope(signal.get("agent_identity"))
        if not owner or not signal.get("id"):
            return
        ref = float(signal.get("signal_price") or price)
        stop, target = signal.get("stop"), signal.get("target")
        # Keep the planned distances, re-anchored to the actual fill price.
        stop_dist = ref - float(stop) if isinstance(stop, (int, float)) and stop < ref else ref * 0.008
        target_dist = float(target) - ref if isinstance(target, (int, float)) and target > ref else stop_dist * 2
        ticker = str(signal.get("ticker") or "").upper()
        now = now_utc()
        opened = now
        try:
            stamp = datetime.fromisoformat(str(fill.get("ts") or "").replace("Z", "+00:00"))
            if stamp.tzinfo and stamp <= now:
                opened = stamp
        except ValueError:
            pass
        with self.desk._lock:
            raw = self.load()
            managed = raw.setdefault("managed", {})
            row = managed.get(ticker)
            totals = raw.setdefault("managed_fill_totals", {})
            key = json.dumps([owner, signal["id"]], sort_keys=True)
            previous = totals.get(key) or {"shares": 0., "notional": 0.}
            notional = shares * price
            delta = shares - previous["shares"]
            if delta == 0 and notional == previous["notional"]:
                return
            if row and row.get("account_scope") != owner:
                return  # legacy/unowned and other-account records are never adopted
            if delta < 0 or (delta == 0 and notional != previous["notional"]):
                if row is None and signal.get("side") == "sell" and previous.get("position_before"):
                    row = copy.deepcopy(previous["position_before"])
                    managed[ticker] = row
                if row:
                    row["tracking_error"] = "Broker corrected an earlier fill; review this position before automatic exits resume"
                totals[key] = {**previous, "shares": shares, "notional": notional}
                self.save(raw)
                return
            totals[key] = {"shares": shares, "notional": notional, "position_before": copy.deepcopy(row)}
            if signal.get("side") == "sell":
                if row:
                    remaining = max(0., float(row["shares"]) - delta)
                    if remaining < 1:
                        managed.pop(ticker, None)
                        self._archive_released_stop(raw, ticker)
                    else:
                        row.update(shares=remaining, retry_after=None)
                self.save(raw)
                return
            if delta <= 0:
                self.save(raw)
                return
            price = (notional - previous["notional"]) / delta
            shares = delta
            if row:  # add only the new shares, never loosen a previously raised stop
                total = float(row["shares"]) + delta
                price = (float(row["entry"]) * float(row["shares"]) + price * delta) / total
                shares = total
            exit_at = ((opened + timedelta(minutes=policy["max_hold_min"])).isoformat()
                       if policy["max_hold_min"] else None)
            if (row or {}).get("exit_at"):
                exit_at = min(row["exit_at"], exit_at) if exit_at else row["exit_at"]
            managed[ticker] = {
                "shares": shares, "entry": round(price, 4),
                "stop": max(round(price - stop_dist, 4), float((row or {}).get("stop") or 0)),
                "target": round(price + target_dist, 4),
                "risk_per_share": round(stop_dist, 4), "breakeven": bool((row or {}).get("breakeven")),
                "opened_at": (row or {}).get("opened_at") or opened.isoformat(),
                "exit_at": exit_at, "account_scope": owner,
                "cost_per_share": self._cost_per_share(shares, price),
                "tracking_error": (row or {}).get("tracking_error"),
                "signal_id": signal.get("id"),
                "sector": signal.get("sector") or (row or {}).get("sector"),
            }
            self.save(raw)

    def _sync_managed_fills(self):
        """Recover fills confirmed after submission or restart, from the existing ledger.

        Only new signals carrying their saved exit policy are adopted. Old unbound
        records need review; a current account setting is not evidence of ownership.
        """
        with self.desk._lock:
            signals = {s.get("id"): s for s in self.desk.load_signals()}
            fills = copy.deepcopy(self.desk.load_ledger().get("broker_fills") or [])
        for fill in reversed(fills):
            signal = signals.get(fill.get("signal_id")) or {}
            policy = signal.get("agent_exit_policy")
            if (signal.get("source") == "live_agent" and isinstance(policy, dict)
                    and fill.get("confirmed") is True):
                self._track_entry(signal, fill, validate(policy))

    def _exit_check(self, ticker, state, message):
        with self.desk._lock:
            self.exit_checks[ticker] = {"state": state, "message": message, "checked_at": now_utc().isoformat()}

    def _manage_exits(self, cfg, policy):
        """Close agent positions that hit their stop/target/hold/close rule. True if an order went out."""
        import broker_router
        with self.desk._lock:
            raw = self.load()
            managed = copy.deepcopy(raw.get("managed") or {})
            native_records = raw.get("native_stops") or {}
            use_native = policy.get("broker_stops") or bool(native_records)
            pending_rows = self.desk.load_ledger().get("pending_broker_orders") or []
            pending = bool(pending_rows)
            # Only an unresolved order on the same stock blocks that stock's exit.
            pending_tickers = {str((p.get("signal") or {}).get("ticker") or "").upper() for p in pending_rows}
            if not managed:
                return False
        try:
            context = broker_router.verify_execution_context()
            if not context.get("ok") or context.get("identity") != cfg.get("broker_identity"):
                return bool(use_native)  # never send an exit to an unapproved account
            positions = broker_router.get_positions()
            # A different account/connection after the read is not evidence
            # that the original account sold its shares.
            after = broker_router.verify_execution_context()
            if not after.get("ok") or after.get("identity") != context.get("identity"):
                raise ValueError("Broker account changed during holdings check")
        except Exception:  # noqa: BLE001 - unreadable account: try again next tick
            for ticker in managed:
                self._exit_check(ticker, "holdings_unverified", "Holdings could not be verified; tracked positions were kept")
            return bool(use_native)
        if (not isinstance(positions, dict) or positions.get("ok") is not True
                or not isinstance(positions.get("positions"), list)
                or ("identity" in positions and positions["identity"] != context["identity"])):
            for ticker in managed:
                self._exit_check(ticker, "holdings_unverified", "Holdings could not be verified; tracked positions were kept")
            return bool(use_native)
        held = {}
        try:
            for row in positions["positions"]:
                if not isinstance(row, dict) or isinstance(row.get("qty"), bool):
                    raise ValueError("Unreadable holding")
                qty = float(row.get("qty"))
                symbol = str(row.get("symbol") or "").strip().upper()
                side = str(row.get("side") or "").lower()
                if not symbol or symbol in held or not math.isfinite(qty) or side not in ("long", "short"):
                    raise ValueError("Incomplete or ambiguous holdings")
                if side == "long" and qty < 0:
                    raise ValueError("Holding side and quantity disagree")
                held[symbol] = -abs(qty) if side == "short" else qty
        except (TypeError, ValueError):
            for ticker in managed:
                self._exit_check(ticker, "holdings_unverified", "Some holdings were unreadable; tracked positions were kept")
            return bool(use_native)
        with self.desk._lock:
            current = self.desk.load_config()
            saved = current.get("live_agent") or {}
            expected = cfg.get("live_agent") or {}
            # Pause/session state is deliberately not checked: exits protect existing
            # positions. Live trading OFF or a changed account/policy stops them.
            # A change made while holdings were read (including pausing) means that
            # read may not match the settings; act on a fresh read next tick instead.
            if (current.get("broker_identity") != cfg.get("broker_identity")
                    or current.get("mode") != "auto_live" or current.get("mode") != cfg.get("mode")
                    or current.get("session_active") != cfg.get("session_active")
                    or saved.get("enabled") != expected.get("enabled")
                    or saved.get("revision") != expected.get("revision")
                    or saved.get("run_id") != expected.get("run_id")):
                return False
        now = now_utc()
        to_close = _minutes_to_close(now)
        native_blocked = False
        for ticker, row in managed.items():
            if not row.get("account_scope") or row.get("account_scope") != account_scope(cfg.get("broker_identity")):
                self._exit_check(ticker, "account_review", "Position belongs to another account or its original account is unverified")
                continue
            native = policy.get("broker_stops") or ticker in native_records
            if not native and ticker in pending_tickers:
                self._exit_check(ticker, "reconciling", "An order on this stock is unresolved; its exit waits for broker evidence")
                continue
            # Native stop executions change managed shares through their durable
            # accounting receipt. A holdings-only shrink would apply a fill twice.
            shares = int(float(row["shares"])) if native else int(min(float(row["shares"]), held.get(ticker, 0.0)))
            def check_stop(reason=None):
                nonlocal native_blocked
                try:
                    outcome = stock_protection.sync(self, cfg, ticker, row, max(0, shares), reason)
                except Exception:
                    outcome = {"state": "review", "message": "Broker stop could not be verified; no separate sell will be sent"}
                if outcome["state"] == "shrink":
                    # Never reclaim shares sold outside this agent; resize the stop next check.
                    if not self._update_managed(ticker, {"shares": outcome["held"]}, expected=row):
                        outcome = {"state": "waiting", "message": "The tracked position changed during the stop check"}
                self._exit_check(ticker, "broker_stop_" + outcome["state"], outcome["message"])
                if outcome["state"] not in {"protected", "released"}:
                    native_blocked = True
                return outcome
            if shares < 1:  # An outstanding stop must be resolved before forgetting its position.
                if native and check_stop().get("allow_exit") is not True:
                    continue
                self._update_managed(ticker, None, expected=row)
                continue
            if row.get("tracking_error"):
                self._exit_check(ticker, "review", row["tracking_error"])
                continue
            if shares < float(row["shares"]):
                if not self._update_managed(ticker, {"shares": shares}, expected=row):
                    self._exit_check(ticker, "position_changed", "A newer fill changed this position; checking again next cycle")
                    continue
                row = dict(row, shares=shares)  # never reclaim shares later bought outside this agent
            retry_after = row.get("retry_after")
            retry_wait = retry_after and now < datetime.fromisoformat(retry_after)
            if retry_wait and not native:
                self._exit_check(ticker, "retry_wait", "Previous exit was refused; waiting before checking again")
                continue  # a recent exit attempt was refused; do not hammer the gates
            try:
                quote = broker_router.stock_quote(ticker)
            except Exception:  # noqa: BLE001
                if native:
                    check_stop(None if policy.get("broker_stops") else "disable")
                    continue
                self._exit_check(ticker, "quote_unavailable", "Could not read a current broker quote")
                continue
            if (not isinstance(quote, dict) or not quote.get("ok") or quote.get("delayed")
                    or quote_error(quote, policy["max_quote_age_sec"])):
                if native:
                    check_stop(None if policy.get("broker_stops") else "disable")
                    continue
                self._exit_check(ticker, "quote_unavailable", "Waiting for a fresh real-time broker quote")
                continue  # never act on a stale or delayed price
            bid = quote.get("bid") if isinstance(quote.get("bid"), (int, float)) and quote.get("bid") > 0 else quote["price"]
            reason, updates = exit_decision(row, float(bid), now, policy, to_close)
            if updates:
                if not self._update_managed(ticker, updates, expected=row):
                    self._exit_check(ticker, "position_changed", "The tracked position changed during the quote check")
                    continue
                row = dict(row, **updates)
            if native:
                stop_reason = reason if not pending and not retry_wait else None
                if not policy.get("broker_stops"):
                    stop_reason = "disable"
                outcome = check_stop(stop_reason)
                if outcome.get("allow_exit") is not True or pending or retry_wait:
                    continue
                if not policy.get("broker_stops"):
                    with self.desk._lock:
                        raw = self.load()
                        self._archive_released_stop(raw, ticker)
                        self.save(raw)
                    if not policy["protective_exits"]:
                        continue
            if not reason:
                self._exit_check(ticker, "watching", "Current quote checked; no exit rule met")
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
                "agent_exit_policy": copy.deepcopy(policy),
                "agent_position": copy.deepcopy(row),
                "created_at": now.isoformat(), "ts": now.isoformat(),
                "expires_at": (now + timedelta(seconds=policy["max_quote_age_sec"])).isoformat(),
            }
            result = self.desk._ingest_one_signal(signal, cfg_override=cfg)
            fill = result.get("fill") if isinstance(result.get("fill"), dict) else None
            if fill:
                self._track_entry(signal, fill, policy)
            else:
                self._update_managed(ticker, {"retry_after": (now + timedelta(seconds=60)).isoformat()}, expected=row)
            self._exit_check(ticker, "exit_attempt", result.get("reject_reason") or result.get("error") or "Exit submitted; broker fill evidence remains authoritative")
            self.record("exit_" + reason, result.get("reject_reason") or result.get("error") or
                        (f"Protective {reason.replace('_', ' ')} exit sent for {shares} {ticker}"), signal, fill)
            return True
        with self.desk._lock:
            if use_native:
                error = stock_protection.entry_error(self.load(), cfg)
                if error or native_blocked:
                    self.phase, self.message = "protection_review", error or "Broker-held protection is unverified; new research waits"
                    return True  # Defer new research and risk while protection is uncertain.
        return False

    @staticmethod
    def _archive_released_stop(raw, ticker):
        record = (raw.get("native_stops") or {}).get(ticker)
        if record and record.get("state") == "released":
            raw.setdefault("native_stop_history", []).insert(0, {"ticker": ticker, **raw["native_stops"].pop(ticker)})
            # Preserve the correction evidence; execution IDs are not disposable
            # just because a newer position uses the same ticker.

    def _update_managed(self, ticker, updates, expected=None):
        with self.desk._lock:
            raw = self.load()
            managed = raw.setdefault("managed", {})
            if expected is not None and managed.get(ticker) != expected:
                return False  # A newer fill/correction wins over an older read.
            if updates is None:
                managed.pop(ticker, None)
                self._archive_released_stop(raw, ticker)
            elif ticker in managed:
                managed[ticker].update(updates)
            self.save(raw)
            return True

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
        if str((signal or {}).get("asset_type") or "").upper() in ("OPT", "BAG"):
            event["asset_type"] = signal["asset_type"].upper()
            event["option_strategy"] = signal.get("option_strategy")
        with self.desk._lock:
            raw = self.load()
            raw["events"].insert(0, event)
            raw["events"] = raw["events"][:200]
            self.save(raw)

    def trade_error(self, signal, cfg, day_pnl, reducing):
        error = authorization_error(signal, cfg)
        if error:
            return error
        if not reducing and not signal.get("agent_exit"):
            promotion = self.promotion(cfg)
            if not promotion["passed"]:
                return promotion["plain"]
        policy = validate(cfg["live_agent"]["policy"])
        if not reducing and str(signal.get("asset_type") or "").upper() in ("OPT", "BAG"):
            blocker = auto_live_options.entry_block(cfg)
            if blocker:
                return blocker
        with self.desk._lock:
            raw = self.load()
            option_error = option_lifecycle.gate_error(raw, cfg.get("broker_identity"), signal.get("ticker") if reducing else None)
            if option_error:
                return option_error
            if not reducing and signal.get("asset_type") not in ("OPT", "BAG"):
                if any(r["ticker"] == signal.get("ticker") and (r["remaining"] or r["awaiting_entry"]) and not option_lifecycle.resolved(r)
                       for r in option_lifecycle.rows_for(raw, cfg.get("broker_identity"))):
                    return "The option test owns this underlying; adding stock would make assignment tracking ambiguous"
            if not signal.get("agent_exit"):
                protection_error = stop_fills.gate_error(self, raw, cfg.get("broker_identity") or {}) or stock_protection.entry_error(raw, cfg)
                if protection_error:
                    return protection_error
            if signal.get("agent_option_position_id"):
                tracked = raw.get("option_positions", {}).get(signal["agent_option_position_id"])
                if (not tracked or tracked != signal.get("agent_position") or tracked.get("hold")
                        or tracked["account_scope"] != account_scope(cfg.get("broker_identity"))):
                    return "Tracked option changed; wait for a fresh exit check"
            elif signal.get("agent_exit"):
                tracked = (raw.get("managed") or {}).get(str(signal.get("ticker") or "").upper())
                if (not tracked or not isinstance(signal.get("agent_position"), dict)
                        or tracked != signal["agent_position"] or tracked.get("tracking_error")
                        or tracked.get("account_scope") != account_scope(cfg.get("broker_identity"))):
                    return "Tracked position changed or needs review; create a fresh protective exit"
            if signal.get("id") in raw["attempts"]:
                return "This agent decision already reserved a broker attempt; it cannot be retried"
            if (not signal.get("agent_exit")
                    and raw["days"].get(self.key(cfg), {}).get("orders", 0) >= effective_limits(cfg)["max_orders_per_day"]):
                return "Live agent reached its daily broker-attempt limit"
        if not reducing and (day_pnl is None or day_pnl <= -policy["max_daily_loss_usd"]):
            return "Live agent daily loss limit reached or daily P&L unavailable"
        if not reducing:
            with self.desk._lock:
                tracked = (self.load().get("managed") or {}).get(str(signal.get("ticker") or "").upper())
                if tracked and (tracked.get("account_scope") != account_scope(cfg.get("broker_identity"))
                                or tracked.get("tracking_error")):
                    return "Review the existing tracked position and its account before adding new shares"
            window = _event_window(cfg)
            if window:
                return market_events.window_message(window)
            outside = _entry_window_message()
            if outside:
                return outside
            earnings = earnings_block(signal, cfg, policy)
            if earnings:
                return earnings
            with self.desk._lock:
                managed = copy.deepcopy(self.load().get("managed") or {})
            pending = self.desk.load_ledger().get("pending_broker_orders") or []
            crowded = sector_block(signal, policy, managed, pending)
            if crowded:
                return crowded
        return None

    def reserve(self, signal, cfg):
        """Caller holds execution + data locks. A crash consumes, never repeats, an attempt."""
        raw = self.load()
        key = self.key(cfg)
        if signal["id"] in raw["attempts"]:
            raise ValueError("Agent decision already reserved")
        row = raw["days"].setdefault(key, {"research": 0, "orders": 0})
        if not signal.get("agent_exit") and row["orders"] >= effective_limits(cfg)["max_orders_per_day"]:
            raise ValueError("Live agent daily broker-attempt limit reached")
        row["orders"] += 1
        raw["attempts"][signal["id"]] = {"key": key, "revision": signal["agent_revision"]}
        self.save(raw)

    def promotion(self, cfg):
        """The pre-registered evidence test for automatic real-money buys (fails closed)."""
        import lessons
        import strategy_scorecard
        try:
            order_usd = self._share_price_cap(cfg)
            if not order_usd:
                raise ValueError("per-order limit unknown")
            fees = (self.desk._order_commission_usd(cfg, 1, order_usd, "buy")
                    + self.desk._order_commission_usd(cfg, 1, order_usd, "sell"))
            hurdle = fees / order_usd * 1e4
        except Exception:  # noqa: BLE001 - an unknown hurdle can never be beaten
            hurdle = float("inf")
        try:
            rows = lessons.recent(self.desk.LESSONS_PATH, 5000)
        except Exception:  # noqa: BLE001
            rows = []
        return strategy_scorecard.promotion_status(rows, hurdle_bps=hurdle)

    def _safe_promotion(self, cfg):
        try:
            return self.promotion(cfg)
        except Exception as exc:  # noqa: BLE001 - status must still render
            return {"passed": False, "plain": "Evidence check unavailable (" + type(exc).__name__ + "); automatic buys stay locked."}

    def _cost_per_share(self, shares, price):
        """Round-trip commissions per share for this position (0 when unknown)."""
        try:
            return round(float(self.desk._round_trip_commission_per_share(self.desk.load_config(), shares, price)), 4)
        except Exception:  # noqa: BLE001 - the stop then falls back to the entry price
            return 0.0

    def _unaffordable_active(self, symbol):
        until = self._unaffordable.get(symbol)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._unaffordable.pop(symbol, None)
            return False
        return True

    def _share_price_cap(self, cfg):
        """Largest whole-share price the effective per-order limit allows, or None if unknown."""
        try:
            book = self.desk._broker_book_cached() or {}
            if book.get("ok") is not True or book.get("risk_ready") is not True:
                return None
            return effective_limits(cfg, book.get("equity"))["max_order_usd"]
        except Exception:  # noqa: BLE001 - unknown only disables this cost-saving filter
            return None

    @staticmethod
    def _history_provider_ok():
        """A liquid reference with history means missing symbols are not a provider outage."""
        try:
            import data_sources
            bars, _ = data_sources.get_daily_with_fallback("SPY", days=30)
            return bars is not None and len(bars) > 0
        except Exception:  # noqa: BLE001
            return False

    def _refund_screen(self, cfg, policy):
        """A screen that found no PASS made no model call: it does not use up the
        daily research allowance, and the next symbol comes up sooner."""
        with self.desk._lock:
            raw = self.load()
            today = raw["days"].setdefault(self.key(cfg), {"research": 0, "orders": 0})
            today["research"] = max(0, int(today.get("research") or 0) - 1)
            raw["next_at"] = (now_utc()+timedelta(seconds=min(SCREEN_GAP_SEC, policy["interval_sec"]))).isoformat()
            self.save(raw)

    def tick(self):
        if not self.cycle_lock.acquire(blocking=False):
            return
        self._run_tick()

    def schedule_tick(self):
        """Reserve one cycle before launch; leave broker reconciliation responsive."""
        if not self.cycle_lock.acquire(blocking=False):
            return False
        try:
            cfg = self.desk.load_config()
            saved = cfg.get("live_agent")
            exits_only = bool(saved and cfg.get("mode") == "auto_live" and (self.load().get("managed") or {}))
            if not exits_only and (not saved or not saved.get("enabled") or cfg.get("mode") != "auto_live"
                                   or not cfg.get("session_active")):
                self.phase = "paused" if saved else "not_configured"
                self.message = "Agent paused; broker reconciliation continues" if saved else "Save a policy to prepare the live agent"
                self.cycle_lock.release()
                return False
            self._worker = threading.Thread(target=self._run_tick, name="live-agent-cycle", daemon=True)
            self._worker.start()
            return True
        except Exception:
            self.phase, self.message = "error", "Could not start the agent check; the scheduler will retry"
            self.cycle_lock.release()
            return False

    def _run_tick(self):
        try:
            self._tick()
        except Exception as exc:
            # Never retry an uncertain broker submission. Its durable intent is
            # reconciled by the existing background worker before another cycle.
            self.phase, self.message = "error", type(exc).__name__+": "+str(exc)[:180]
            try:
                self.desk.append_journal("live_agent_error", {"error": self.message})
            except Exception:
                self.message += "; activity log could not be written"
        finally:
            self.cycle_lock.release()

    def _tick(self):
        with self.desk._lock:
            cfg = copy.deepcopy(self.desk.load_config())
        saved = cfg.get("live_agent")
        if not saved:
            return
        # Reconcile option exposure even when paused and outside market hours.
        # This observer never submits or changes any broker order. Its failure
        # must not skip the stock protective exits below; it blocks new risk.
        review_errors = []
        if cfg.get("broker_identity") and self.load().get("option_positions"):
            try:
                option_lifecycle.monitor(self, cfg)
            except Exception as exc:  # noqa: BLE001
                self.option_check_error = "Option holdings check failed ("+type(exc).__name__+"); new entries wait"
                review_errors.append(self.option_check_error)
        if not saved.get("enabled") or cfg.get("mode") != "auto_live" or not cfg.get("session_active"):
            managed = self.load().get("managed") or {}
            if managed and cfg.get("mode") == "auto_live" and paper_loop.is_rth(now_utc()):
                # Paused for new trades only: stops, targets and the end-of-day close
                # keep protecting the positions Fox opened. Live trading OFF stops these too.
                try:
                    policy = validate(saved["policy"])
                except (ValueError, KeyError, TypeError):
                    self.phase, self.message = "paused", "Agent paused; its saved policy is invalid, so exits need your review"
                    return
                self._sync_managed_fills()
                if policy["protective_exits"] and self._manage_exits(cfg, policy):
                    return
                count = len(self.load().get("managed") or {})
                self.phase, self.message = "paused_exits", (
                    f"Paused for new trades. Protective exits still watch {count} open position{'s' if count != 1 else ''}.")
                return
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
        if policy["broker_stops"] and (cfg.get("broker_identity") or {}).get("paper_mode") is not True:
            self.phase, self.message = "protection_review", "Broker-held stop testing requires an IBKR paper account"
            return
        self._sync_managed_fills()
        # Reconciliation failures leave their own entry blocks in place
        # (stop_accounting_error / option review); stock exits still run.
        try:
            stop_fills.reconcile(self)
        except Exception as exc:  # noqa: BLE001
            review_errors.append("Stop accounting could not be verified ("+type(exc).__name__+"); new entries wait")
        try:
            if option_lifecycle.manage_exits(self, cfg):
                return
        except Exception as exc:  # noqa: BLE001
            review_errors.append("Option exit check failed ("+type(exc).__name__+"); new entries wait")
        if (policy["protective_exits"] or self.load().get("native_stops")) and self._manage_exits(cfg, policy):
            return  # an exit went out this tick; new research waits for the next one
        with self.desk._lock:
            raw = self.load()
            protection_error = review_errors[0] if review_errors else None
            protection_error = protection_error or stop_fills.gate_error(self, raw, cfg.get("broker_identity") or {}) or stock_protection.entry_error(raw, cfg)
            protection_error = protection_error or option_lifecycle.gate_error(raw, cfg.get("broker_identity"))
        if protection_error:
            self.phase, self.message = "protection_review", protection_error
            return
        window = _event_window(cfg)
        if window:
            self.phase, self.message = "event_window", market_events.window_message(window)
            return  # no research spend while new entries are paused; exits ran above
        outside = _entry_window_message()
        if outside:
            self.phase, self.message = "entry_window", outside
            return  # no research or model spend outside the tested window; exits ran above
        with self.desk._lock:
            raw = self.load()
            if self.desk._CORRUPT_PATHS:
                raise ValueError("Desk data needs recovery")
            if self.desk.load_ledger().get("pending_broker_orders"):
                self.phase, self.message = "reconciling", "A broker order is unresolved; waiting for broker evidence"
                return
            today = raw["days"].get(self.key(cfg), {"research": 0, "orders": 0})
            if today["research"] >= policy["max_research_per_day"] or today["orders"] >= effective_limits(cfg)["max_orders_per_day"]:
                self.phase, self.message = "daily_limit", "Daily agent research or broker-attempt limit reached"
                return
            if raw.get("next_at") and now_utc() < datetime.fromisoformat(raw["next_at"]):
                return
        # No research was performed when identity or the model budget blocks us.
        # Keep the counters/cursor untouched until those prerequisites pass.
        import broker_router
        context = broker_router.verify_execution_context()
        if not context.get("ok") or context.get("identity") != cfg.get("broker_identity"):
            self.phase, self.message = "blocked", "Broker identity is unavailable or changed"
            return
        if context["identity"].get("broker") != "ibkr":
            self.phase, self.message = "blocked", "This agent version requires the IBKR Gateway adapter"
            return
        usage = self.desk.llm_trader.model_cost_today()
        scoped = (usage.get("scopes") or {}).get("live_agent") or {}
        if usage.get("error") or number(scoped.get("committed_usd", scoped.get("model_usd")) or 0., "Recorded AI cost", 0, 1e12) >= Decimal(str(policy["model_budget_usd"])):
            self.phase, self.message = "budget", "Recorded live-agent AI budget reached; research is paused"
            return
        with self.desk._lock:
            if self.desk.load_config() != cfg:
                self.phase, self.message = "discarded", "Settings changed during account checks"
                return
            raw = self.load()
            if self.desk._CORRUPT_PATHS:
                raise ValueError("Desk data needs recovery")
            if self.desk.load_ledger().get("pending_broker_orders"):
                self.phase, self.message = "reconciling", "A broker order is unresolved; waiting for broker evidence"
                return
            if raw.get("next_at") and now_utc() < datetime.fromisoformat(raw["next_at"]):
                return
            today = raw["days"].setdefault(self.key(cfg), {"research": 0, "orders": 0})
            if today["research"] >= policy["max_research_per_day"] or today["orders"] >= effective_limits(cfg)["max_orders_per_day"]:
                self.phase, self.message = "daily_limit", "Daily agent research or broker-attempt limit reached"
                return
            eval_symbols = research_universe(cfg, policy, include_unavailable=True)
            if not eval_symbols:
                self.phase, self.message = "blocked", "No equity symbols available to evaluate"
                return
            coverage = raw.setdefault("research_coverage", {})
            coverage = {k: v for k, v in coverage.items() if datetime.fromisoformat(v["retry_at"]) > now_utc()}
            raw["research_coverage"] = coverage
            symbol = None
            for _ in range(len(eval_symbols)):
                candidate = eval_symbols[raw["cursor"] % len(eval_symbols)]
                raw["cursor"] += 1
                if (candidate not in coverage and not _quote_skip_active(candidate)
                        and not self._unaffordable_active(candidate)):
                    symbol = candidate
                    break
            if symbol is None:
                raw["next_at"] = (now_utc()+timedelta(seconds=policy["interval_sec"])).isoformat()
                self.save(raw)
                self.phase, self.message = "coverage_wait", "Every symbol is cooling down (missing data or one share above the per-order limit); broker reconciliation and exits continue"
                return
            today["research"] += 1
            raw["next_at"] = (now_utc()+timedelta(seconds=policy["interval_sec"])).isoformat()
            self.save(raw)  # Failures/no setup/restarts also consume this cycle.
        def authorized():
            return self.desk.load_config() == cfg and paper_loop.is_rth(now_utc())
        self.phase, self.message = "researching", "Evaluating "+symbol+" (live-order eligible · full universe)"
        scope = getattr(self.desk.llm_trader, "cost_scope", None)
        notes = {}
        share_cap = self._share_price_cap(cfg)
        # Only PASS setups can become agent orders, so only they reach the paid brain.
        if callable(scope):
            with scope("live_agent", scope_cap=policy["model_budget_usd"]):
                signal = self.desk.generate_scan_signal(cfg, ticker=symbol, force=False, still_authorized=authorized,
                                                        pass_only=True, notes=notes, max_share_price=share_cap)
        else:
            signal = self.desk.generate_scan_signal(cfg, ticker=symbol, force=False, still_authorized=authorized,
                                                    pass_only=True, notes=notes, max_share_price=share_cap)
        if not authorized():
            self.record("discarded", "Settings or session changed during research")
            return
        self.note_review(cfg, symbol, signal, notes)
        if not signal:
            # Only an explicit completed WATCH/AVOID screen earns a fast retry.
            # Providers also return None on failures; those retain the durable
            # attempt and full interval reserved before I/O.
            if notes.get("error") or notes.get("verdict") not in ("WATCH", "AVOID"):
                if notes.get("coverage_unavailable") is True:
                    misses = getattr(self, "_coverage_misses", [])
                    misses = [s for s in misses if s != symbol] + [symbol]
                    if len(misses) >= COVERAGE_OUTAGE_STREAK and not self._history_provider_ok():
                        # Several different symbols in a row with no history is a
                        # provider outage, not missing coverage: undo their cooldowns
                        # and keep the normal research interval as backoff.
                        self._coverage_misses = []
                        with self.desk._lock:
                            raw = self.load()
                            for missed in misses:
                                raw.setdefault("research_coverage", {}).pop(missed, None)
                            self.save(raw)
                        self.record("blocked", "Price history is unavailable for several symbols in a row, including the SPY reference; treating it as a data-provider outage and retrying after the saved research interval")
                        return
                    self._coverage_misses = misses
                    with self.desk._lock:
                        raw = self.load()
                        raw.setdefault("research_coverage", {})[symbol] = {
                            "reason": "No usable history from the configured price sources",
                            "retry_at": (now_utc()+timedelta(hours=1)).isoformat()}
                        raw["next_at"] = (now_utc()+timedelta(seconds=min(SCREEN_GAP_SEC, policy["interval_sec"]))).isoformat()
                        self.save(raw)
                    self.record("coverage_wait", symbol+" has no usable price history; set aside for one hour. Checking another symbol next.")
                    return
                self.record("blocked", "Market data or setup assessment unavailable for "+symbol+"; retrying after the saved research interval")
                return
            self._coverage_misses = []  # A completed screen proves the price sources respond.
            price = notes.get("price")
            if share_cap and isinstance(price, (int, float)) and price > share_cap:
                # Rechecked hourly: prices move, and the account limit follows equity.
                self._unaffordable[symbol] = time.monotonic() + UNAFFORDABLE_RECHECK_SEC
            self._refund_screen(cfg, policy)
            why = ""
            if notes.get("verdict"):
                why = f" ({notes['verdict']}: {str(notes.get('text') or '').strip()[:160]})".replace(": )", ")")
            self.record("no_setup", "No qualifying setup for "+symbol+why)
            return
        signal.update(source="live_agent", workspace="live", agent_revision=saved["revision"],
                      agent_run_id=saved.get("run_id"),
                      agent_exit_policy=copy.deepcopy(policy),
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
                    # the delayed quote remains visible and quote_error blocks it.
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
        if auto_cfg.get("enabled") and not auto_live_options.entry_block(cfg) and signal.get("verdict") == "PASS":
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
            if policy["broker_stops"] and signal.get("side") == "buy" and str(signal.get("asset_type") or "STK").upper() == "STK":
                # Protect confirmed entry shares immediately, including a partial
                # entry. Do not wait for the next research interval.
                self._manage_exits(cfg, policy)
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
            qty = float(fill.get('shares') or 0)
            if str(signal.get("asset_type") or "").upper() in ("OPT", "BAG"):
                what = (f"{qty:g} {signal.get('ticker')} {signal.get('option_strategy') or 'option'} "
                        f"contract{'s' if qty != 1 else ''}")
                message = f"{verb} {what} at ${float(fill.get('price') or 0):,.2f} premium"
            else:
                message = f"{verb} {qty:g} {signal.get('ticker')} at ${float(fill.get('price') or 0):,.2f}"
        else:
            message = result.get("reject_reason") or "Decision retained"
        self.record(result.get("status", "unknown"), message, dict(signal, id=result.get("id") or signal.get("id")), fill)


    def _maybe_convert_to_option(self, signal, cfg, auto_cfg, context):
        """Attach OPT/BAG fields using IBKR chain + options_desk strategy names. No order yet."""
        blocker = auto_live_options.entry_block(cfg)
        if blocker:
            raise ValueError(blocker)
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
        # Size cautions on the stock idea (WSB crowding) carry over to the contracts.
        mult = signal.get("advisory_size_mult")
        if isinstance(mult, (int, float)) and 0 <= mult < 1:
            contracts = int(int(candidate.get("contracts") or 0) * mult)
            if contracts < 1:
                raise ValueError("Size caution (WSB crowding) leaves under one contract")
            candidate = dict(candidate, contracts=contracts)
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

    By owner decision (2026-09-28) stock entries also require live data;
    option premiums move even faster, and a receipt time alone would pass the
    freshness window on 15-minute-old prices.
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
    global _DESK
    _DESK = desk
    service = LiveAgent(desk)
    bp = Blueprint("live_agent", __name__)
    import live_research
    consume_start_review = live_research.register(bp, desk, service)

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
        if (not isinstance(body, dict) or not {"revision", "identity", "confirm"} <= set(body)
                or set(body) - {"revision", "identity", "confirm", "review_token"}):
            raise ValueError("Confirm the exact saved policy and displayed broker account")
        reviewed = consume_start_review(body) if "review_token" in body else None
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
            live_research.check_review(reviewed, cfg)
            if desk._CORRUPT_PATHS or desk.load_ledger().get("pending_broker_orders"):
                raise ValueError("Resolve unreadable state or unresolved broker orders before activation")
            if not saved or saved.get("revision") != body["revision"] or cfg.get("broker_identity") != identity:
                return jsonify(ok=False, error="Policy or account changed; reload before activation"), 409
            validate(saved["policy"])
            if saved["policy"].get("broker_stops") and identity["paper_mode"] is not True:
                raise ValueError("Broker-held stops are for IBKR paper qualification only; live activation is unavailable")
            if daily <= -min(saved["policy"]["max_daily_loss_usd"], effective_limits(cfg, equity)["max_daily_loss_usd"]):
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
        service.phase, service.message = "paused", ("Paused for new trades. Stop-loss, target and end-of-day exits keep "
                                                     "watching positions Fox opened; turn Live trading OFF to stop every order.")
        return jsonify(service.status())

    stop_review.register(bp, desk)
    option_review.register(bp, desk)
    import option_activity
    option_activity.register(bp, desk)
    app.register_blueprint(bp)
    return service
