"""Cumulative weekly research, sharing the daily review's durable model receipts.

No order/config write path. Model opinions never become trading instructions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from datetime import date, datetime, timedelta

from after_close_review import AfterCloseReview, session_day, utcnow
from agent_review import conclusion
from desk_workbench import timestamp
from narrative_checks import checked_report
from real_trade_journal import review as execution_review
import moss_paper
import moss_policy
import paper_loop
import research_metrics


def week_sessions(monday):
    return [monday + timedelta(days=i) for i in range(5)
            if paper_loop.session_close_time(monday + timedelta(days=i))]


def week_time(monday):
    sessions = week_sessions(monday)
    if not sessions:
        return None
    last = sessions[-1]
    return datetime.combine(last, paper_loop.session_close_time(last), tzinfo=paper_loop.NY_TZ) + timedelta(minutes=90)


def schedule(now):
    today = now.astimezone(paper_loop.NY_TZ).date()
    monday = today - timedelta(days=today.weekday())
    due = upcoming = None
    for i in range(54):
        at = week_time(monday - timedelta(weeks=i))
        if at and at <= now:
            due = at
            break
    for i in range(54):
        at = week_time(monday + timedelta(weeks=i))
        if at and at > now:
            upcoming = at
            break
    return due, upcoming


class WeeklyReview(AfterCloseReview):
    retention = 52
    cost_scope = "weekly_review"
    period_label = "weekly"

    @property
    def path(self):
        return self.desk.DATA_DIR / "weekly_reviews.json"

    def status(self, now=None):
        now = now or utcnow()
        due, upcoming = schedule(now)
        try:
            with self.lock:
                rows = self.load()["reports"]
                latest = checked_report(rows[max(rows)]) if rows else None
                history = [{"day": k, "status": v.get("status"), "completed_at": v.get("completed_at")}
                           for k, v in sorted(rows.items(), reverse=True)]
            return {"busy": self.busy, "phase": self.phase, "error": self.error, "latest": latest,
                    "history": history, "next_at": upcoming.isoformat() if upcoming else None,
                    "due_day": due.date().isoformat() if due else None,
                    "schedule": "90 minutes after the week's final US stock session closes. Holidays and early closes included. Latest missed week catches up while the app runs; 52 reports retained."}
        except ValueError:
            return {"busy": False, "error": "Weekly review storage needs recovery; existing reports preserved.", "latest": None, "history": []}

    def tick(self, now=None):
        now = now or utcnow()
        if not self.companion.load()["settings"]["enabled"] or self.companion.stop.is_set():
            return False
        # Avoid competing with the closing day's collection and two model requests.
        if self.companion.after_close.busy:
            return False
        due, _ = schedule(now)
        if due is None:
            return False
        day = due.date().isoformat()
        with self.lock:
            raw = self.load()
            row = raw["reports"].get(day, {})
            if self.busy or row.get("status") in ("complete", "partial") or row.get("collection_attempts", 0) >= 3:
                return False
            if timestamp(row.get("attempted_at")) and now.timestamp() - timestamp(row["attempted_at"]) < 300:
                return False
            row.update(day=day, version="weekly-v1", kind="weekly", status="running", attempted_at=now.isoformat(),
                       collection_attempts=row.get("collection_attempts", 0) + 1, models=row.get("models", {}))
            raw["reports"][day] = row
            self.save(raw)
            self.busy, self.phase, self.error = True, "collecting", None
        try:
            threading.Thread(target=self._work, args=(day, now), name="weekly-research", daemon=True).start()
        except Exception:
            self.busy = False
            raise
        return True

    def collect(self, day, now):
        end = date.fromisoformat(day)
        start = end - timedelta(days=end.weekday())
        if end not in week_sessions(start) or end != week_sessions(start)[-1]:
            raise ValueError("Weekly review requires the week's final market session")
        cutoff = min(now, datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=paper_loop.NY_TZ))
        with self.desk._lock:
            current = copy.deepcopy(self.companion.events())
            archived = copy.deepcopy(moss_paper.history(self.desk))
            journal = copy.deepcopy(self.companion.trades.load())
        if any(str(p.resolve()) in self.desk._CORRUPT_PATHS for p in (self.desk.DECISIONS_PATH, self.companion.trades.path)):
            raise ValueError("Evidence store needs recovery")
        merged = {str(r["id"]): r for r in archived if isinstance(r, dict) and r.get("id")}
        # Newest current revision wins, including withdrawal/correction.
        merged.update({str(r["id"]): r for r in reversed(current) if r.get("id")})
        events = [r for r in merged.values() if timestamp(r.get("ts")) is not None
                  and timestamp(r["ts"]) <= cutoff.timestamp() and session_day(r) <= day]
        qualified, excluded = moss_policy.qualified(events, cutoff)
        first, prior_start = start.isoformat(), (start - timedelta(weeks=4)).isoformat()
        rows = [r for r in qualified if first <= session_day(r) <= day]
        prior = [r for r in qualified if prior_start <= session_day(r) < first]
        recorded = [r for r in events if first <= session_day(r) <= day]
        # Existing collector supplies bounded online releases/prices, not past news reconstructed as current.
        evidence = self.companion.after_close.collect(day, now)
        with self.companion.after_close.lock:
            dailies = copy.deepcopy(self.companion.after_close.load()["reports"])
        days = [d.isoformat() for d in week_sessions(start)]
        available = [d for d in days if isinstance(dailies.get(d, {}).get("evidence"), dict)]
        observed = sorted({session_day(r) for r in recorded})
        evidence["period"] = {"start": first, "end": day, "expected_sessions": days,
                              "daily_reports_available": available, "missing_daily_reports": [d for d in days if d not in available],
                              "observed_sessions": observed, "sessions_without_observations": [d for d in days if d not in observed]}
        evidence["outcomes"] = {"id": "outcomes", "label": "Whole-week qualified practice observations",
            "today": research_metrics.expectancy(rows), "prior_20_observed_sessions": research_metrics.expectancy(prior),
            "comparison_label": "Four previous calendar weeks", "excluded": excluded,
            "today_recorded": len(recorded), "qualified_event_ids": [r["id"] for r in rows],
            "verdict": conclusion(research_metrics.walk_forward([*prior, *rows])),
            "note": "Calculated from unique current record versions, not an average of daily averages. These are simulated price observations, not portfolio returns."}
        identity = journal.get("identity") or {}
        account = identity.get("account_id")
        seen, executions = set(), []
        for r in journal.get("executions", []):
            stamp = timestamp(r.get("ts"))
            if not account or r.get("account_id") != account or not isinstance(r.get("paper_mode"), bool) or r["paper_mode"] != identity.get("paper_mode"):
                continue
            if stamp is None or stamp > cutoff.timestamp() or not first <= session_day(r) <= day:
                continue
            # The journal's execution ID is authoritative; identical copies are not extra fills.
            key = str(r.get("execution_id") or r.get("exec_id") or r.get("id") or json.dumps(r, sort_keys=True))
            if key not in seen:
                seen.add(key)
                executions.append(r)
        evidence["executions"] = {"id": "executions", "label": "Retained weekly broker fills",
            "live": execution_review([r for r in executions if r["paper_mode"] is False]),
            "broker_paper": execution_review([r for r in executions if r["paper_mode"] is True]),
            "note": "Current journal identity only. No complete account equity curve; deposits and withdrawals are not profit. Missing history remains unknown."}
        headlines, documents = {}, {}
        for source in [*(dailies[d]["evidence"] for d in available), evidence]:
            for kind, dest in (("headlines", headlines), ("documents", documents)):
                for r in source.get(kind, []):
                    published = timestamp(r.get("published_at"))
                    # Some official documents have no exact publication instant; keep only dated daily receipt and label it.
                    if published and datetime.combine(start, datetime.min.time(), tzinfo=paper_loop.NY_TZ).timestamp() <= published <= cutoff.timestamp():
                        dest[str(r.get("url") or r.get("id"))] = r
        evidence["headlines"] = list(headlines.values())[-35:]
        evidence["documents"] = list(documents.values())[-8:]
        with self.lock:
            previous = [(k, v) for k, v in self.load()["reports"].items() if k < first]
        evidence["memories"] = [{"id": "week_" + k, "label": "Earlier weekly hypotheses · unproven",
            "verdict": (v.get("evidence", {}).get("outcomes", {}).get("verdict")),
            "hypotheses": (v.get("models", {}).get("fox", {}).get("result", {}).get("hypotheses", []))}
            for k, v in sorted(previous)[-4:]]
        evidence["coverage"].update(label="Week coverage and learning limits",
            note=f"{len(available)} of {len(days)} daily evidence reports available. {len(observed)} sessions have recorded observations. Missing days were not filled with zeros.",
            news="Bounded current online collection plus saved, dated daily headlines and official excerpts for this week. Missing days and sources are not reconstructed. Later news is hindsight, not proof of knowledge at entry.",
            learning="Conclusions use current corrected observations. Weekly model hypotheses remain unproven; no model-weight training, rule promotion or risk changes. Historical reports are frozen snapshots; corrections apply to future calculations.")
        evidence["outcome_cutoff"] = cutoff.isoformat()
        evidence.pop("hash", None)
        evidence["hash"] = hashlib.sha256(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()
        return evidence
