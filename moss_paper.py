"""Market-session paper worker. Contains no broker order or cancellation calls."""
import copy
import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import moss_policy as policy
import paper_loop
import market_universe
from trade_planner import paper_quantity


def now_utc():
    return datetime.now(timezone.utc)


def history(desk):
    """Use archived revisions so outcomes survive the UI's 500-row ring."""
    rows = {}
    for path in sorted((desk.DATA_DIR / "research_history").glob("*.jsonl"))[-30:]:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("source") == "moss_paper" and event.get("id"):
                rows[event["id"]] = event
    raw = desk._load_json(desk.DECISIONS_PATH, {})
    for e in (raw.get("events", []) if isinstance(raw, dict) else raw or []):
        if e.get("source") == "moss_paper" and e.get("id"):
            rows[e["id"]] = e
    return sorted(rows.values(), key=lambda e: e.get("ts", ""))[-10000:]


def entry_error(signal, cfg, ledger, quote, now):
    """Final check immediately before a local paper ledger write."""
    p = policy.settings(cfg)
    if not p["enabled"] or not cfg.get("paper_research_enabled") or not cfg.get("paper_auto_approve"):
        return "Moss paper automation was paused"
    if signal.get("moss_config_hash") != policy.fingerprint(p):
        return "Moss paper settings changed; require a new decision"
    error = policy.quote_error(quote, now, p["max_quote_age_sec"])
    if error:
        return error
    done = policy.aware(signal.get("decision_completed_at"))
    if not done or not 0 <= (now-done).total_seconds() <= p["max_order_latency_sec"]:
        return "stale_execution"
    if signal.get("mock") or signal.get("routed") or signal.get("brain_mode") == "mock" or str(signal.get("llm_model", "")).startswith("mock"):
        return "Mock research is excluded from Moss evidence"
    if signal.get("verdict") == "AVOID":
        return "AVOID research is not a paper entry"
    _, close = policy.session(now.astimezone(paper_loop.NY_TZ).date())
    if close is None or now >= close-timedelta(minutes=p["horizon_min"]+2):
        return "Too late for a complete intraday outcome"
    positions = [q for q in ledger.get("positions", []) if float(q.get("shares") or 0)>0]
    if any(q.get("ticker") == signal.get("ticker") for q in positions):
        return "A paper position already exists; no repeated exposure to the same move"
    if len(positions) >= p["max_positions"]:
        return "Moss paper position count limit"
    return None


class PaperWorkday:
    def __init__(self, desk):
        self.desk = desk
        self.lock = threading.RLock()
        self.busy = False
        self.last_error = None
        self.next_attempt = 0.
        self.last_housekeeping = 0.

    @property
    def path(self):
        return self.desk.DATA_DIR / "moss_workday.json"

    def load(self):
        raw = self.desk._load_json(self.path, {"days": {}, "cursor": 0})
        if not isinstance(raw, dict) or not isinstance(raw.get("days"), dict):
            raise ValueError("Moss workday state is unreadable")
        return raw

    def save(self, raw):
        if str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
            raise ValueError("Restore Moss workday state before continuing")
        self.desk._save_json(self.path, raw)

    def log(self, event):
        day = now_utc().astimezone(paper_loop.NY_TZ).date().isoformat()
        folder = self.desk.DATA_DIR / "moss_observations"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{day}.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(ts=now_utc().isoformat(), **event), allow_nan=False) + "\n")

    def status(self):
        cfg = self.desk.load_config()
        p = policy.settings(cfg)
        now = now_utc()
        with self.lock:
            raw = self.load()
            today = raw["days"].get(now.astimezone(paper_loop.NY_TZ).date().isoformat(), {})
        active = p["enabled"] and cfg.get("paper_research_enabled") and cfg.get("paper_auto_approve")
        return {"settings": p, "active": bool(active), "busy": self.busy, "error": self.last_error,
                "phase": "paused" if not active else "collecting" if paper_loop.is_rth(now) else "waiting_for_market",
                "today": today, "evaluation": raw.get("evaluation"), "universe":market_universe.status(self.desk),
                "schedule": "Every market day, 9:30 ET through the exchange close; holidays and early closes observed. Runs while the app and PC are on.",
                "scope": "Local paper fills only. Live execution settings are never changed."}

    def tick(self):
        """Short scheduler call; model/network work stays on one worker."""
        now = now_utc()
        cfg = self.desk.load_config()
        p = policy.settings(cfg)
        if time.monotonic() - self.last_housekeeping >= 10:
            self.last_housekeeping = time.monotonic()
            # Existing outcome checker has its own nonblocking lock. Observe due
            # horizons even when new paper entries have been paused.
            self.desk.check_decision_outcomes()
            self.close_due(cfg, now)
            self.close_report(now)
        if not (p["enabled"] and cfg.get("paper_research_enabled") and cfg.get("paper_auto_approve") and paper_loop.is_rth(now)):
            return
        with self.lock:
            if self.busy or time.monotonic() < self.next_attempt:
                return
            raw = self.load()
            last = policy.aware(raw.get("last_cycle_at"))
            if last and (now-last).total_seconds() < p["interval_sec"]:
                return
            raw["last_cycle_at"] = now.isoformat()
            self.save(raw)  # reserve before I/O; restart cannot repeat the cycle
            self.busy = True
            self.next_attempt = time.monotonic()+p["interval_sec"]
        threading.Thread(target=self.work, name="moss-paper-research", daemon=True).start()

    def work(self):
        from desk_operations import record_trace, utc
        trace_id, trace_started = uuid.uuid4().hex, utc()
        self.trace_inputs = []
        try:
            self.last_error = None
            self._work()
        except Exception as exc:
            self.last_error = str(exc)[:180] if isinstance(exc, ValueError) else f"Paper research paused: {type(exc).__name__}"
            self.log({"event": "cycle_error", "error": self.last_error})
        finally:
            try:
                record_trace(self.desk, trace_id, trace_started, "failed" if self.last_error else "completed",
                             self.trace_inputs, self.status().get("today", {}), self.last_error)
            except Exception:
                self.last_error = self.last_error or "Paper trace could not be saved; inspect evidence storage."
            with self.lock:
                self.busy = False

    def _work(self):
        from screener_logic import analyze_ticker
        cfg = self.desk.load_config()
        p = policy.settings(cfg)
        now = now_utc()
        if not (p["enabled"] and cfg.get("paper_research_enabled") and cfg.get("paper_auto_approve") and paper_loop.is_rth(now)):
            return
        day = now.astimezone(paper_loop.NY_TZ).date().isoformat()
        with self.lock:
            raw = self.load()
            progress = raw["days"].setdefault(day, {"cycles": 0, "model_calls": 0, "qualified_candidates": 0, "paper_fills": 0})
            progress["cycles"] += 1
            cursor = raw.get("cursor", 0)
            raw["cursor"] = cursor+p["candidates_per_cycle"]
            self.save(raw)
        symbols = market_universe.candidates(self.desk, p, cursor, now)
        ranked = []
        for symbol in symbols:
            current = self.desk.load_config()
            if not (current.get("paper_research_enabled") and current.get("paper_auto_approve")
                    and policy.settings(current) == p and paper_loop.is_rth(now_utc())):
                self.finish(day, "Paper collection paused or settings changed; wait for a fresh cycle", False)
                return
            try:
                analysis = analyze_ticker(symbol)
                quality = policy.assess(analysis, cfg, now_utc())
            except Exception as exc:
                analysis = {"ticker": symbol, "error": f"Market analysis unavailable: {type(exc).__name__}"}
                quality = policy.assess(analysis, cfg, now_utc())
            with self.lock:
                raw = self.load()
                day_state = raw["days"][day]
                day_state["candidates_checked"] = day_state.get("candidates_checked", 0)+1
                day_state["symbols_checked"] = sorted(set(day_state.get("symbols_checked", [])) | {symbol})
                self.save(raw)
            if hasattr(self, "trace_inputs"):
                self.trace_inputs.append({"ticker": symbol, "quote": analysis.get("quote"), "intraday": analysis.get("intraday"),
                                          "quality": quality, "analysis_error": analysis.get("error")})
            self.log({"event": "candidate", "ticker": symbol, "quality": quality,
                      "quote": analysis.get("quote"), "intraday": analysis.get("intraday"), "volume": analysis.get("volume")})
            if quality["ok"]:
                ranked.append((quality["score"], analysis, quality))
        if not ranked:
            with self.lock:
                raw=self.load();raw["days"][day]["last_result"]="No candidate passed fresh-data, liquidity and volatility checks";self.save(raw)
            return
        training = policy.fit(history(self.desk), now_utc())
        current = self.desk.load_config()
        if not (current.get("paper_research_enabled") and current.get("paper_auto_approve")
                and paper_loop.is_rth(now_utc())) or policy.settings(current) != p:
            self.finish(day, "Settings changed during collection; wait for a fresh cycle", False)
            return
        pcfg = self.desk.paper_research_config(cfg)
        model = self.desk._llm_public_status(pcfg).get("model")
        def research_weight(item):
            if p["personality"] != "empirical_bayes":
                return 1.
            matches = [w for w in training["weights"] if w["key"][0:5] ==
                       [item[1]["ticker"], model, self.desk.llm_trader.PROMPT_VERSION, p["horizon_min"], item[2]["setup"]]
                       and w["key"][6:] == [p["personality"], float(pcfg.get("slip_bps", 5)), self.desk._cfg_fee_bps(pcfg)]]
            count = sum(w["samples"] for w in matches)
            return sum(w["weight"]*w["samples"] for w in matches)/count if count else 1.
        # Rotation supplies coverage; most cycles choose the stronger measured
        # liquidity/volatility candidate. Every fourth cycle explores the least sampled.
        counts = {s: sum(w["samples"] for w in training["weights"] if w["ticker"]==s) for s in symbols}
        ranked.sort(key=lambda item: item[0]*research_weight(item), reverse=True)
        if progress["cycles"] % 4 == 0:
            ranked.sort(key=lambda item: counts[item[1]["ticker"]])
        _, analysis, quality = ranked[0]
        self.log({"event":"research_selection", "ticker":analysis["ticker"], "ranking_weight":research_weight(ranked[0]),
                  "training_hash":training["evidence_hash"], "exploration":progress["cycles"]%4 == 0})
        _, close = policy.session(now_utc().astimezone(paper_loop.NY_TZ).date())
        with self.lock:
            raw = self.load(); progress=raw["days"][day]
            progress["qualified_candidates"] += len(ranked)
            raw["evaluation"] = training
            usage = self.desk.llm_trader.model_cost_today()
            cost = usage.get("model_usd")
            reason = None
            if now_utc() >= close-timedelta(minutes=p["horizon_min"]+2): reason="Collecting only: insufficient session time for the evaluation horizon"
            elif progress["model_calls"] >= p["max_model_calls"]: reason="Collecting only: daily model-call cap reached"
            elif cost is None or cost >= p["model_budget_usd"]: reason="Collecting only: model spend unavailable or configured desk-wide budget reached"
            if reason:
                progress["last_result"]=reason;self.save(raw);return
            progress["model_calls"] += 1  # attempts count even if a request fails
            progress["last_result"] = f"Reviewing {analysis['ticker']}"
            self.save(raw)
        pcfg.update(decision_horizon_min=p["horizon_min"], _moss_paper=True)
        analysis = dict(analysis, companion_context={"style": "Patient Stoic researcher. Never force a trade or promise a profit.",
                           "personality": p["personality"], "paper_only": True,
                           "training_as_of": training["trained_at"], "weights": training["weights"][:30]})
        thesis = self.desk._research_thesis(analysis, pcfg, source="moss_paper")
        if hasattr(self, "trace_inputs"):
            self.trace_inputs.append({"operation": "configured_research_model", "result": thesis,
                                      "prompt_version": thesis.get("prompt_version"), "model": thesis.get("llm_model")})
        completed = now_utc()
        ident = thesis.get("decision_record_id")
        if not ident:
            raise ValueError("Research record unavailable; no paper fill attempted")
        common = {"moss_policy_version": policy.VERSION, "moss_personality": p["personality"],
                  "moss_setup": quality["setup"], "moss_quality": quality,
                  "moss_max_quote_age_sec": p["max_quote_age_sec"],
                  "moss_max_order_latency_sec": p["max_order_latency_sec"],
                  "decision_completed_at": completed.isoformat(), "moss_training_hash": training["evidence_hash"],
                  "moss_training_as_of": training["trained_at"]}
        self.desk._decision_ring.patch(ident, common)
        side = thesis.get("side")
        if side not in ("buy", "sell") or thesis.get("error") or thesis.get("execution_block") or thesis.get("abstain") or thesis.get("brain_mode") == "mock" or thesis.get("routed"):
            self.finish(day, "Research retained; no eligible directional paper entry", False)
            return
        row = next((w for w in training["weights"] if tuple(w["key"]) ==
                    (analysis["ticker"], thesis.get("llm_model"), thesis.get("prompt_version"),
                     p["horizon_min"],quality["setup"],side,p["personality"],float(pcfg.get("slip_bps",5)),self.desk._cfg_fee_bps(pcfg))), None)
        growth = row["paper_growth_fraction"] if row and p["personality"] == "empirical_bayes" else 0
        budget = p["base_order_usd"]+(p["max_order_usd"]-p["base_order_usd"])*growth
        signal = self.desk._analysis_to_signal(analysis, pcfg, self.desk.get_preset(pcfg.get("risk_preset")))
        if not signal:
            raise ValueError("No valid paper signal")
        signal.update(workspace="paper", paper_research=True, source="moss_paper", side=side,
                      llm_side=side, llm_model=thesis.get("llm_model"), brain_mode=thesis.get("brain_mode"),
                      llm_thesis=thesis.get("thesis"), llm_error=thesis.get("error"),
                      confidence=thesis.get("confidence"), decision_record_id=ident,
                      advisory_size_mult=thesis.get("advisory_size_mult",1),
                      moss_policy_version=policy.VERSION, moss_config_hash=policy.fingerprint(p),
                      decision_completed_at=completed.isoformat(), moss_budget=budget,
                      moss_weight_snapshot=row, mode_at_create="auto_paper")
        with self.desk._lock:
            ledger = self.desk.load_ledger()
            self.desk._size_paper_research(signal, pcfg, ledger)
            held = next((q for q in ledger.get("positions",[]) if q.get("ticker")==signal["ticker"]),None)
        if side == "sell" and not (held and float(held.get("moss_owned_shares") or 0)>0):
            self.finish(day,"Bearish research recorded; borrowed-share entries are unavailable",False)
            return
        if side == "sell":
            # Close only Moss's own paper holding, never borrow or close manual holdings.
            signal["suggested_shares"] = min(float(held["shares"]), float(held["moss_owned_shares"]))
            signal["moss_exit"] = True
        else:
            entry_price=signal["signal_price"]*(1+float(pcfg.get("slip_bps",5))/10000)
            advisory = max(0, min(1, policy.finite(signal.get("advisory_size_mult")) or 0))
            signal["suggested_shares"]=min(signal["suggested_shares"],paper_quantity(budget*advisory/(entry_price*(1+self.desk._cfg_fee_bps(pcfg)/10000)),True))
        self.desk._decision_ring.patch(ident,{"moss_execution_attempted":True,"moss_weight_snapshot":row,"moss_budget":budget})
        # Deliberately call the local fill function. Never call ingest_signal,
        # execute_gated_broker_or_paper, or any mode-dependent broker router.
        result = self.desk.paper_fill(signal, pcfg, source="auto_paper")
        stamp = now_utc()
        if result.get("ok"):
            fill=result["fill"]
            self.desk._decision_ring.patch(ident,{"moss_fill":{"id":fill["id"],"ts":fill["ts"],
                "latency_sec":(stamp-completed).total_seconds(),"price":fill["price"],"shares":fill["shares"],"fee_usd":fill["fee_usd"]}})
        else:
            self.desk._decision_ring.patch(ident,{"moss_execution_error":result.get("error") or "unknown_fill_failure"})
        self.log({"event":"paper_execution","decision_id":ident,"ticker":signal["ticker"],"budget":budget,"weight":row,
                  "result":{"ok":result.get("ok"),"error":result.get("error"),"fill":result.get("fill")}})
        self.finish(day, f"Paper fill: {signal['ticker']}" if result.get("ok") else f"Paper entry blocked: {result.get('error')}",bool(result.get("ok")))

    def finish(self, day, message, filled):
        with self.lock:
            raw=self.load();progress=raw["days"][day]
            progress["last_result"]=message;progress["paper_fills"]+=int(filled)
            self.save(raw)

    def close_due(self, cfg, now):
        if not paper_loop.is_rth(now):
            return
        p=policy.settings(cfg)
        _, close=policy.session(now.astimezone(paper_loop.NY_TZ).date())
        with self.desk._lock:
            positions=copy.deepcopy(self.desk.load_ledger().get("positions",[]))
        for pos in positions:
            owned=float(pos.get("moss_owned_shares") or 0)
            due=policy.aware(pos.get("moss_exit_at"))
            if owned <= 0 or not due or (now < due and not (p["flatten_before_close"] and now >= close-timedelta(minutes=2))):
                continue
            px=self.desk.fetch_last_price(pos["ticker"])
            quote=self.desk._QUOTE_SNAPSHOTS.get(pos["ticker"],{})
            if policy.quote_error(quote,now_utc(),p["max_quote_age_sec"]):
                continue
            signal={"id":str(uuid.uuid4()),"workspace":"paper","side":"sell","ticker":pos["ticker"],
                    "signal_price":px,"suggested_shares":min(owned,float(pos["shares"])),"quote":quote,"no_bracket":True,"moss_exit":True}
            result=self.desk.paper_fill(signal,self.desk.paper_research_config(cfg),"moss_horizon_exit")
            self.log({"event":"paper_horizon_exit","ticker":pos["ticker"],"ok":result.get("ok"),"error":result.get("error"),"fill":result.get("fill")})

    def close_report(self, now):
        # Finish any earlier observed session after restart as well; no fabricated
        # trades are created for days the app was off.
        with self.lock:
            raw=self.load()
            changed=False
            for day, progress in raw["days"].items():
                _, close=policy.session(datetime.fromisoformat(day).date())
                if not close or now < close+timedelta(seconds=130) or progress.get("report_saved"):
                    continue
                evidence=history(self.desk)
                evaluation=policy.fit(evidence,now)
                report={"session_day":day,"created_at":now.isoformat(),"activity":copy.deepcopy(progress),
                        "evaluation":evaluation,"paper_recap":self.desk.daily_recap(self.desk.paper_research_config(self.desk.load_config()),self.desk.load_ledger(),day=day),
                        "remaining_moss_positions":[p for p in self.desk.load_ledger().get("positions",[]) if p.get("moss_owned_shares")],
                        "note":"Real market observations and simulated fills; broker execution is separate. Missing prices never become fabricated closing fills."}
                self.desk._save_json(self.desk.DATA_DIR/"moss_reports"/f"{day}.json",report)
                progress["report_saved"]=report["created_at"]
                raw["evaluation"]=evaluation
                changed=True
            if changed:
                self.save(raw)
