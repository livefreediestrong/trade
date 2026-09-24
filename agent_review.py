"""Automatic, replayable research conclusions. No execution or configuration writes."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading
import time

from desk_operations import EvidenceStore
from desk_workbench import fingerprint, timestamp
import moss_policy
import moss_paper
import paper_loop
import research_metrics


def conclusion(evaluation):
    overall = evaluation["overall"]
    n, days, mean = overall["outcomes"], overall["session_days"], overall["mean_net_bps"]
    if n < 30 or days < 5:
        return {"state": "collecting", "title": "More evidence needed",
                "text": f"{n} qualified paper outcomes across {days} session days. There is not enough evidence for a strategy conclusion.",
                "next": "Keep collecting and grading qualified observations within the saved paper limits."}
    if mean is None or mean <= 0:
        return {"state": "no_edge", "title": "No positive after-cost edge observed",
                "text": "The qualified sample has not produced positive average returns after its recorded costs.",
                "next": "Review loss patterns and execution friction; this result does not justify increasing exposure."}
    return {"state": "descriptive_positive", "title": "Positive sample; still unproven",
            "text": "Average recorded after-cost outcomes are positive. Selection effects, uncertainty and changing conditions remain.",
            "next": "Continue prospective evaluation on later sessions. This does not qualify real-money automation."}


class AgentReview:
    def __init__(self, desk, companion):
        self.desk, self.companion = desk, companion
        self.last_attempt = 0.0
        self.last_checked = None
        self.error = None
        self.lock = threading.Lock()

    @property
    def path(self):
        return self.desk.DATA_DIR / "agent_review.json"

    def status(self):
        saved = self.desk._load_json(self.path, {})
        if not isinstance(saved, dict):
            raise ValueError("Research review storage needs repair before saving conclusions.")
        if isinstance(saved, dict) and saved.get("execution_quality"):
            quality = saved["execution_quality"]
            saved = {**saved, "execution_quality": {**{k:v for k,v in quality.items() if k != "executions"},
                                                      "execution_count": len(quality.get("executions", []))}}
        return {"latest": saved if isinstance(saved, dict) else {}, "error": self.error,
                "last_checked": self.last_checked, "cadence_seconds": 300,
                "scope": "Automatic local analysis of retained evidence. No additional paid model calls or broker actions."}

    def tick(self, *, force=False, now=None):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if not force and self.last_attempt and time.monotonic()-self.last_attempt < 300:
                return
            self.last_attempt = time.monotonic()
            now = now or datetime.now(timezone.utc)
            if str(self.path.resolve()) in self.desk._CORRUPT_PATHS:
                raise ValueError("Research review storage needs repair before saving conclusions.")
            # Broker sync and market collection remain in their existing workers.
            with self.desk._lock:
                current_events = copy.deepcopy(self.companion.events())
                retained = {row["id"]: row for row in moss_paper.history(self.desk)}
                retained.update({row["id"]: row for row in current_events if row.get("id")})
                events = list(retained.values()) + [row for row in current_events if not row.get("id")]
                executions = copy.deepcopy(self.companion.trades.load().get("executions", []))
                signals = copy.deepcopy(self.desk.load_signals())
                previous = self.status()["latest"]
                paths = (self.path, self.desk.DECISIONS_PATH, self.desk.SIGNALS_PATH, self.companion.trades.path)
                if any(str(path.resolve()) in self.desk._CORRUPT_PATHS for path in paths):
                    raise ValueError("Retained evidence needs repair; no new conclusion was saved.")
            rows, excluded = moss_policy.qualified(events, now)
            eligible_executions = [r for r in executions if timestamp(r.get("ts")) is not None and timestamp(r.get("ts")) <= now.timestamp()]
            key = fingerprint({"events": events, "qualified_ids": [r["id"] for r in rows], "excluded": excluded,
                               "execution_timestamp_exclusions": len(executions)-len(eligible_executions),
                               "executions": eligible_executions, "signals": signals, "engine": "agent-review-v1"})
            if previous.get("input_hash") != key:
                evaluation = research_metrics.walk_forward(rows)
                quality = research_metrics.execution_costs(eligible_executions, signals)
                store = EvidenceStore(self.desk.DATA_DIR)
                evaluation_id = store.put("walk_forward", "paper:local", fingerprint(events), {
                    "engine": "research-studio-v1", "created_at": now.isoformat(), "events": events,
                    "input_hash": fingerprint(events), "extra_friction_bps": 0, "excluded": excluded, "result": evaluation})
                execution_evidence_id = store.put("execution_review", "research", key, {
                    "engine": research_metrics.VERSION, "created_at": now.isoformat(),
                    "executions": eligible_executions, "signals": signals, "result": quality,
                    "execution_timestamp_exclusions": len(executions)-len(eligible_executions)})
                payload = {"created_at": now.isoformat(), "input_hash": key, "conclusion": conclusion(evaluation),
                           "paper": evaluation["overall"], "excluded": excluded, "evaluation_id": evaluation_id,
                           "execution_evidence_id": execution_evidence_id,
                           "execution_quality": quality, "execution_timestamp_exclusions": len(executions)-len(eligible_executions),
                           "automatic": True, "scope": "Retained evidence only; missing fills, prices or costs are never invented. No live qualification or arming decision."}
                ident = store.put("agent_review", "research", key, payload)
                self.desk._save_json(self.path, {**payload, "evidence_id": ident})
            # Existing enabled daily research owns new session scans. Explicitly
            # paused scans are never resumed by this worker.
            scanner_factory = getattr(self.desk, "_market_scanner", None)
            if scanner_factory and self.companion.load()["settings"]["enabled"] and paper_loop.is_rth(now):
                scanner = scanner_factory()
                scan = scanner.snapshot()
                if scan["state"] == "idle" or (scan["stale"] and scan["state"] not in ("running", "initializing", "pausing", "paused")):
                    scanner.start()
            self.last_checked, self.error = now.isoformat(), None
        except Exception as exc:
            self.error = str(exc)[:200] if isinstance(exc, ValueError) else "Automatic evidence review unavailable; retained conclusions may be out of date."
        finally:
            self.lock.release()
