"""Moss: bounded daily research, a local notebook, and rehearsal-only plans.

No broker submission/cancellation imports. Qualified recorded outcomes train a
small research ranker; foundation-model weights remain unchanged.
"""
from __future__ import annotations

import copy
import re
import threading
import time
import uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request, Response

import data_sources
import desk_workbench
import paper_loop
import research_learning
import moss_policy
from moss_paper import PaperWorkday
from real_trade_journal import TradeJournal
from trade_planner import estimate, number

DEFAULTS = {"enabled": True, "name": "Moss", "daily_target": 10.0,
            "research_short_selling": True, "use_model": True}
PLAN = {"budget": 10.0, "daily_loss_limit": 2.0, "max_orders": 1,
        "order_type": "limit", "symbols": ["SPY"], "fractional": False,
        "short_selling": False}


def now_utc():
    return datetime.now(timezone.utc)


def next_session(now):
    local = now.astimezone(paper_loop.NY_TZ)
    for offset in range(370):
        day = local.date() + timedelta(days=offset)
        close = paper_loop.session_close_time(day)
        if close is not None:
            opening = datetime.combine(day, paper_loop.RTH_OPEN, tzinfo=paper_loop.NY_TZ)
            closing = datetime.combine(day, close, tzinfo=paper_loop.NY_TZ)
            if local < closing:
                return max(local, opening).isoformat()
    return None


def historical_observation(symbol, bars, source, now):
    """Use completed daily bars only; do not label a closing price as live."""
    local = now.astimezone(paper_loop.NY_TZ)
    points = []
    if bars is not None:
        for stamp, row in bars.sort_index().iterrows():
            day = stamp.date()
            close_time = paper_loop.session_close_time(day)
            if day > local.date() or not close_time:
                continue
            if day == local.date() and local.time().replace(tzinfo=None) < close_time:
                continue
            price = desk_workbench.finite(row.get("Close"))
            if price and price > 0:
                points.append({"date": day.isoformat(), "close": price})
    if len(points) < 2:
        return {"symbol": symbol, "available": False, "source": source,
                "note": "Not enough completed daily prices to compare."}
    prices = [p["close"] for p in points]
    moves = [(prices[i] / prices[i-1] - 1) * 100 for i in range(1, len(prices))]
    latest_day = datetime.fromisoformat(points[-1]["date"]).date()
    expected_day = local.date()
    close_today = paper_loop.session_close_time(expected_day)
    if not close_today or local.time().replace(tzinfo=None) < close_today:
        expected_day -= timedelta(days=1)
    while not paper_loop.session_close_time(expected_day):
        expected_day -= timedelta(days=1)
    stale = latest_day < expected_day
    daily = moves[-1]
    twenty = (prices[-1] / prices[-21] - 1) * 100 if len(prices) >= 21 else None
    return {"symbol": symbol, "available": True, "source": source,
            "as_of": points[-1]["date"], "stale": stale, "close": prices[-1],
            "day_change_pct": round(daily, 3), "twenty_day_pct": round(twenty, 3) if twenty is not None else None,
            "sample_days": len(points), "mean_abs_daily_move_pct": round(sum(abs(m) for m in moves[-20:]) / len(moves[-20:]), 3),
            "recent_prices": points[-21:],
            "note": f"{symbol} closed {'higher' if daily > 0 else 'lower' if daily < 0 else 'unchanged'} by {abs(daily):.2f}% on {points[-1]['date']}. " +
                    ("Prices are behind the latest completed session; wait for newer data." if stale else "A daily pattern is context, not an intraday entry signal.")}


def memory_summary(events):
    groups = desk_workbench.calibration(events)
    qualified = research_learning.train(events)["qualified_samples"]
    return {"observations": len(events), "reviewed_outcomes": qualified, "groups": groups,
            "note": "No qualified scored outcomes yet. I can collect evidence, but I cannot infer an edge." if not qualified else
                    "Past outcomes train research-ranking parameters. They do not establish profitability or fine-tune the language model."}


def validate_plan(body):
    if not isinstance(body, dict) or set(body) - set(PLAN):
        raise ValueError("Save only the rehearsal fields shown. This plan cannot arm live trading.")
    value = dict(PLAN, **body)
    value["budget"] = float(number(value["budget"], "Per-order budget", .01, 100_000))
    value["daily_loss_limit"] = float(number(value["daily_loss_limit"], "Daily loss limit", .01, 100_000))
    count = number(value["max_orders"], "Maximum orders", 1, 100)
    if count != int(count):
        raise ValueError("Maximum orders must be a whole number")
    value["max_orders"] = int(count)
    if value["order_type"] not in ("limit", "market"):
        raise ValueError("Choose limit or market")
    for key in ("fractional", "short_selling"):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key} must be true or false")
    symbols = value["symbols"]
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 20 or any(not isinstance(s, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", s) for s in symbols):
        raise ValueError("Use 1–20 uppercase stock symbols")
    value["symbols"] = list(dict.fromkeys(symbols))
    return value


class Companion:
    def __init__(self, desk):
        self.desk = desk
        self.lock = threading.RLock()
        self.busy = False
        self.last_error = None
        self.last_attempt = 0.0
        self.stop = threading.Event()
        self._threads = {}
        self.paper = PaperWorkday(desk)
        self.trades = TradeJournal(desk)
        from agent_review import AgentReview
        self.review = AgentReview(desk, self)
        from companion_news import HeadlineDesk
        self.news = HeadlineDesk(desk)

    @property
    def path(self):
        return self.desk.DATA_DIR / "research_companion.json"

    def load(self):
        raw = self.desk._load_json(self.path, {"settings": DEFAULTS, "briefs": [], "plan": PLAN})
        if not isinstance(raw, dict) or not isinstance(raw.get("briefs", []), list):
            raise ValueError("Research notebook is unreadable; restore it before continuing")
        return dict(raw, settings=dict(DEFAULTS, **raw.get("settings", {})), plan=dict(PLAN, **raw.get("plan", {})))

    def save(self, raw):
        if str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
            raise ValueError("Research notebook is unreadable; restore it before saving")
        self.desk._save_json(self.path, raw)

    def events(self):
        raw = self.desk._load_json(self.desk.DECISIONS_PATH, {})
        return (raw.get("events", []) if isinstance(raw, dict) else raw)[-500:]

    def status(self):
        with self.lock:
            saved = self.load()
            cfg = self.desk.load_config()
            now = now_utc()
            return {"ok": True, "settings": saved["settings"], "plan": saved["plan"],
                    "busy": self.busy, "error": self.last_error, "market_open": paper_loop.is_rth(now),
                    "next_session": next_session(now), "briefs": saved.get("briefs", [])[:30],
                    "notebook_count": len(saved.get("briefs", [])), "memory": memory_summary(self.events()),
                    "paper_workday": self.paper.status(), "actual_trades": self.trades.status(),
                    "agent_review": self.review.status(),
                    "execution_settings": {k: cfg.get(k) for k in ("mode", "session_active", "kill_switch", "rth_only", "risk_preset", "live_agent")},
                    "live_automation": "not_armed", "desk_execution_mode": cfg.get("mode"),
                    "schedule": "Once per market session while the app is running; weekends and exchange holidays skipped. Missed days are not fabricated.",
                    "learned_model": saved.get("learned_model"),
                    "access": {"market_history": True, "watchlists": True, "past_research": True,
                               "paper_book": True, "live_account_read_only": True, "cost_tools": True,
                               "notebook_and_weights": True, "live_submission": False, "live_cancellation": False},
                    "learning": "Qualified outcomes train a small statistical research ranker. The language model is not fine-tuned; live risk limits never change."}

    def due(self, now):
        saved = self.load()
        day = now.astimezone(paper_loop.NY_TZ).date().isoformat()
        return bool(saved["settings"]["enabled"] and paper_loop.is_rth(now) and
                    not any(b.get("session_day") == day and b.get("scheduled") for b in saved["briefs"]))

    def run(self, scheduled=False):
        with self.lock:
            if self.busy:
                return False
            if scheduled and not self.due(now_utc()):
                return False
            if time.monotonic() - self.last_attempt < (300 if scheduled and self.last_error else 60):
                return False
            self.last_attempt = time.monotonic()
            self.busy = True
            self.last_error = None
        threading.Thread(target=self._work, args=(scheduled,), name="moss-research", daemon=True).start()
        return True

    def _work(self, scheduled=False):
        from desk_operations import record_trace, utc
        trace_id, trace_started = uuid.uuid4().hex, utc()
        trace_inputs, trace_output, trace_status = [], {}, "failed"
        try:
            with self.lock:
                saved = self.load()
                settings = copy.deepcopy(saved["settings"])
            with self.desk._lock:
                cfg = copy.deepcopy(self.desk.load_config())
                paper_book = copy.deepcopy(self.desk.load_ledger())
            now = now_utc()
            training = research_learning.train(self.events(), now)
            symbols = paper_loop.equity_loop_symbols(cfg.get("watchlist") or ["SPY"])
            actual_model = self.desk._llm_public_status(cfg).get("model")
            rank = {symbol: max((w["weight"] for w in training["weights"] if w["ticker"] == symbol and
                                w["active_for_ranking"] and w["model"] == actual_model and
                                w["prompt_version"] == self.desk.llm_trader.PROMPT_VERSION), default=1) for symbol in symbols}
            # Stable ranking affects attention only, never a signal or order.
            symbols = sorted(symbols, key=lambda s: rank[s], reverse=True)[:4]
            import market_discovery
            discovery = market_discovery.candidates(self.desk, now)
            if moss_policy.settings(cfg)["universe"] == "broad_us" and discovery:
                # Research attention only: retain focus names and the four-symbol budget.
                extra = [r["symbol"] for r in discovery if r["symbol"] not in symbols[:2]]
                symbols = list(dict.fromkeys(symbols[:2]+extra[:2]+symbols[2:]))[:4]
            book = self.desk._broker_book_cached() or {}
            account = {"equity": book.get("equity"), "daily_pnl": book.get("day_pnl_usd"),
                       "risk_ready": book.get("risk_ready", False), "live_positions": book.get("positions", []),
                       "paper_equity": paper_book.get("equity"), "paper_positions": paper_book.get("positions", [])}
            observations, headlines = [], []
            for symbol in symbols:
                bars, source = data_sources.get_daily_with_fallback(symbol, days=120)
                observations.append(historical_observation(symbol, bars, source, now))
                try:
                    news = self.desk.news_stream.news_for_symbol(symbol, limit=2)
                    if isinstance(news, list):
                        headlines.extend(dict(n, symbol=symbol) for n in news[:2] if isinstance(n, dict))
                except Exception as exc:
                    self.trades.last_error = f"Read-only trade sync unavailable: {type(exc).__name__}"
            memory = memory_summary(self.events())
            import market_catalog
            global_observations = market_catalog.notebook_observations(self.desk)
            trace_inputs = observations + global_observations
            valid = [o for o in observations if o.get("available") and not o.get("stale")]
            if not any(o.get("available") for o in observations):
                raise ValueError("Market history unavailable. No successful daily notebook entry was recorded.")
            if scheduled and not valid:
                raise ValueError("Market history is behind the latest completed session. Daily research will retry when data is current.")
            model = None
            # At most one configured research call per notebook run. It writes
            # a research decision, never ingests a signal or approves an order.
            if settings["use_model"] and valid and paper_loop.is_rth(now) and not moss_policy.settings(cfg)["enabled"]:
                from screener_logic import analyze_ticker
                analysis = analyze_ticker(valid[0]["symbol"])
                quote = analysis.get("quote") or {}
                # Collection can outlive the operator's permission. Read both
                # controls again immediately before reserving any model work.
                with self.lock:
                    current = self.load()
                    settings = copy.deepcopy(current["settings"])
                    if scheduled and not settings["enabled"]:
                        return
                    with self.desk._lock:
                        cfg = copy.deepcopy(self.desk.load_config())
                    permitted = (settings["use_model"] and cfg.get("llm_enabled", True)
                                 and cfg.get("llm_on_scan", True) and paper_loop.is_rth(now_utc())
                                 and not moss_policy.settings(cfg)["enabled"])
                    attempt_day = now.astimezone(paper_loop.NY_TZ).date().isoformat()
                    attempt = (current.get("model_attempts") or {}).get(attempt_day) if scheduled else None
                    if permitted and not analysis.get("error") and quote.get("fresh") and not attempt and scheduled:
                        attempts = dict(current.get("model_attempts") or {})
                        attempts[attempt_day] = {"status": "started", "started_at": now_utc().isoformat()}
                        current["model_attempts"] = dict(sorted(attempts.items())[-90:])
                        self.save(current)
                if permitted and attempt:
                    model = copy.deepcopy(attempt.get("result")) or {"error": "A model request was already attempted this session; no duplicate request was started."}
                elif permitted and not analysis.get("error") and quote.get("fresh"):
                    analysis["companion_context"] = {"style": "Stoic-inspired: patient, plainspoken, evidence before action. Distinguish observations from forecasts. Never guarantee profit or chase a daily target.",
                        "training": training, "short_research": settings["research_short_selling"],
                        "instruction": "Discuss bearish scenarios as research only. No borrowed-share execution is available."}
                    research_cfg = dict(cfg, mode="manual", _paper_research=True,
                                        companion_short_research=settings["research_short_selling"])
                    thesis = self.desk._research_thesis(analysis, research_cfg, source="moss_notebook")
                    model = {k: thesis.get(k) for k in ("thesis", "side", "llm_model", "brain_mode", "risks", "error", "mock", "routed", "prompt_version")}
                    if scheduled:
                        with self.lock:
                            current = self.load()
                            current.setdefault("model_attempts", {})[attempt_day] = {
                                "status": "completed", "completed_at": now_utc().isoformat(), "result": copy.deepcopy(model)}
                            self.save(current)
                else:
                    model = {"error": "Fresh intraday data unavailable; model entry advice skipped."} if permitted else None
            brief = {"id": uuid.uuid4().hex, "created_at": now.isoformat(),
                     "session_day": now.astimezone(paper_loop.NY_TZ).date().isoformat(), "scheduled": scheduled,
                     "observations": observations, "headlines": headlines, "memory": memory, "model": model,
                     "global_observations": global_observations, "trace_id": trace_id,
                     "scanner_context": [r for r in discovery if r["symbol"] in symbols],
                     "target": settings["daily_target"], "research_short_selling": settings["research_short_selling"],
                     "account_context": account, "training": training,
                     "actual_trade_review": self.trades.status()["real"],
                     "summary": "Attend to what is in our control: evidence, patience and position size. " + (f"I reviewed {len(valid)} symbols with current completed-session data." if valid else "Current completed-session data is missing; wait before drawing conclusions."),
                     "next_step": "Compare a fresh quote, costs and risk before considering an entry. Holding cash is a valid choice.",
                     "short_note": "Downward moves can be studied, but borrowed-share execution is unavailable here. Locate, borrow rate, margin and buy-in risks are unverified." if settings["research_short_selling"] else None}
            with self.lock:
                current = self.load()
                if scheduled and not current["settings"]["enabled"]:
                    return
                current["briefs"] = [brief] + current.get("briefs", [])[:89]
                current["learned_model"] = training
                self.save(current)
            trace_status, trace_output = "completed", {"brief_id": brief["id"], "summary": brief["summary"], "model": model}
        except Exception as exc:
            self.last_error = str(exc)[:220] if isinstance(exc, ValueError) else f"Research paused: {type(exc).__name__}. Try again when data is available."
        finally:
            try:
                record_trace(self.desk, trace_id, trace_started, trace_status, trace_inputs, trace_output, self.last_error)
            except Exception:
                self.last_error = self.last_error or "Research trace could not be saved; inspect evidence storage."
            with self.lock:
                self.busy = False

    def start(self):
        def loop():
            while not self.stop.wait(30):
                try:
                    self.news.refresh()
                    self.review.tick()
                    self.run(scheduled=True)
                except Exception:
                    self.last_error = "Notebook unavailable; scheduled research paused."
        def paper_workday():
            while not self.stop.wait(5):
                try:
                    self.paper.tick()
                except Exception as exc:
                    self.paper.last_error = f"Workday paused: {type(exc).__name__}"
        def broker_journal():
            while not self.stop.is_set():
                try:
                    self.trades.tick()
                except Exception:
                    pass
                self.stop.wait(60)
        with self.lock:
            for name, target in (("moss-daily-schedule", loop), ("moss-paper-workday", paper_workday),
                                 ("moss-broker-read-only", broker_journal)):
                thread = self._threads.get(name)
                if thread is None or not thread.is_alive():
                    thread = threading.Thread(target=target, name=name, daemon=True)
                    thread.start()
                    self._threads[name] = thread


def register(app, desk):
    service = Companion(desk)
    app.config["COMPANION_AVAILABLE"] = True
    bp = Blueprint("companion", __name__)

    @bp.get("/manual-live-enablement")
    def manual_enablement():
        return Response((Path(__file__).parent / "docs" / "MANUAL_LIVE_ENABLEMENT.md").read_text(encoding="utf-8"), mimetype="text/plain")

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/companion")
    def state():
        return jsonify(service.status())

    @bp.get("/api/companion/news")
    def headlines():
        service.news.refresh()
        return jsonify(service.news.snapshot())

    @bp.post("/api/companion/research")
    def research():
        started = service.run()
        return jsonify(ok=started, error=None if started else "Research is running or cooling down for one minute."), 202 if started else 409

    @bp.post("/api/companion/settings")
    def settings():
        return update_settings()

    @bp.post("/api/companion/paper")
    def paper_settings():
        p = moss_policy.validate(request.get_json(silent=True))
        with desk._lock:
            cfg = dict(desk.load_config())
            cfg.update(moss_paper=p, paper_research_enabled=p["enabled"], paper_auto_approve=p["enabled"])
            if p["enabled"]:
                cfg["paper_fractional_enabled"] = True
            desk.save_config(cfg)
            desk.append_journal("moss_paper_config", {"personality":p["personality"],"enabled":p["enabled"]})
        return jsonify(ok=True, settings=p, live_mode_unchanged=cfg.get("mode"))

    @bp.post("/api/companion/trades/sync")
    def sync_trades():
        service.trades.sync()
        return jsonify(ok=True, journal=service.trades.status())

    @bp.post("/api/companion/universe/refresh")
    def refresh_universe():
        import market_universe
        return jsonify(ok=True, universe=market_universe.refresh(desk))

    @bp.get("/api/companion/reports/<day>")
    def workday_report(day):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",day):
            raise ValueError("Use YYYY-MM-DD")
        path=desk.DATA_DIR/"moss_reports"/f"{day}.json"
        if not path.exists():
            return jsonify(ok=False,error="No completed paper workday report for that day"),404
        return jsonify(ok=True,report=desk._load_json(path,{}))

    def update_settings():
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body) - set(DEFAULTS):
            raise ValueError("Use only the researcher settings shown")
        for key in ("enabled", "research_short_selling", "use_model"):
            if key in body and not isinstance(body[key], bool):
                raise ValueError(f"{key} must be true or false")
        if "daily_target" in body:
            body["daily_target"] = float(number(body["daily_target"], "Research profit target", 0, 100_000))
        if "name" in body and (not isinstance(body["name"], str) or not 1 <= len(body["name"].strip()) <= 24):
            raise ValueError("Name must be 1–24 characters")
        with service.lock:
            saved = service.load()
            saved["settings"].update(body)
            service.save(saved)
        return jsonify(ok=True, settings=saved["settings"])

    @bp.post("/api/companion/plan")
    def plan():
        value = validate_plan(request.get_json(silent=True))
        with service.lock:
            saved = service.load()
            saved["plan"] = value
            service.save(saved)
        return jsonify(ok=True, plan=value, armed=False, note="Rehearsal plan saved. Live trading mode and risk settings did not change.")

    @bp.post("/api/companion/rehearse")
    def rehearse():
        with service.lock:
            plan = service.load()["plan"]
        with desk._lock:
            signals = copy.deepcopy(desk.load_signals())
        rows = []
        for signal in signals:
            if signal.get("workspace") != "live" or signal.get("status") != "pending" or signal.get("ticker") not in plan["symbols"]:
                continue
            reason = desk.signal_execution_block(signal, broker=True)
            rows.append({"ticker": signal.get("ticker"), "signal_id": signal.get("id"),
                         "would_review": not bool(reason), "reason": reason or "Would request a fresh account-bound order review; no submission."})
        blockers = ["Live automation is not armed; this build only rehearses the saved plan.",
                    "Broker-paper execution and recovery qualification still required.",
                    "Daily loss, maximum order count and dollar limits are saved test parameters, not active live guards."]
        if plan["fractional"]:
            blockers.append("Fractional stock orders are unavailable through the current Gateway adapter.")
        if plan["short_selling"]:
            blockers.append("Borrowed-share execution and borrow-cost verification are unavailable.")
        if plan["order_type"] == "market":
            blockers.append("Market orders do not cap the execution price or guarantee a total spend.")
        return jsonify(ok=True, armed=False, submitted=0, plan=plan, candidates=rows[:30], blockers=blockers)

    @bp.post("/api/cost-estimate")
    def costs():
        return jsonify(estimate(request.get_json(silent=True)))

    @bp.get("/api/cost-estimate/quote/<symbol>")
    def quote(symbol):
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Use a stock symbol such as SPY")
        return jsonify(ok=True, quote=data_sources.latest_quote(symbol))

    app.register_blueprint(bp)
    return service
