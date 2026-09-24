"""Local evidence, attention and evaluation. No broker mutation capability."""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import time
import uuid

from flask import Blueprint, jsonify, request
from desk_workbench import finite, fingerprint, timestamp
import moss_policy
import paper_loop

VERSION = "evidence-v1"


def utc():
    return datetime.now(timezone.utc).isoformat()


class EvidenceStore:
    def __init__(self, folder):
        self.path = Path(folder)/"evidence.sqlite3"

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,kind TEXT,scope TEXT,logical_id TEXT,observed_at TEXT,payload TEXT);
            CREATE INDEX IF NOT EXISTS records_lookup ON records(kind,scope,logical_id);
            CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY,first_seen TEXT,last_seen TEXT,resolved_at TEXT,reviewed_at TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS suites(id TEXT PRIMARY KEY,created_at TEXT,stage TEXT,payload TEXT);
            CREATE TABLE IF NOT EXISTS transitions(id INTEGER PRIMARY KEY,suite_id TEXT,at TEXT,stage TEXT,note TEXT);
            """)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def put(self, kind, scope, logical_id, payload):
        return self.put_many([(kind, scope, logical_id, payload)])[0]

    def put_many(self, records):
        """Validate all records first, then persist the batch in one transaction."""
        rows = []
        observed_at = utc()
        for kind, scope, logical_id, payload in records:
            content = {"kind": kind, "scope": scope, "logical_id": str(logical_id), "payload": payload}
            ident = fingerprint(content)
            rows.append((ident, kind, scope, str(logical_id), observed_at,
                         json.dumps(payload, sort_keys=True, allow_nan=False)))
        if not rows:
            return []
        with self.db() as db:
            db.executemany("INSERT OR IGNORE INTO records VALUES(?,?,?,?,?,?)", rows)
        return [row[0] for row in rows]

    def list(self, kind=None, limit=150):
        with self.db() as db:
            rows = db.execute("SELECT * FROM records WHERE (? IS NULL OR kind=?) ORDER BY observed_at DESC,rowid DESC LIMIT ?",
                              (kind, kind, limit)).fetchall()
        return [dict(r, payload=json.loads(r["payload"])) for r in rows]

    def get(self, ident):
        with self.db() as db:
            row = db.execute("SELECT * FROM records WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("Evidence record not found")
        result = dict(row, payload=json.loads(row["payload"]))
        if fingerprint({k: result[k] for k in ("kind", "scope", "logical_id", "payload")}) != ident:
            raise ValueError("Evidence fingerprint mismatch")
        return result

    def incidents(self, active):
        now = utc()
        with self.db() as db:
            for item in active:
                db.execute("""INSERT INTO incidents VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    last_seen=excluded.last_seen,resolved_at=NULL,
                    reviewed_at=CASE WHEN incidents.resolved_at IS NOT NULL THEN NULL ELSE incidents.reviewed_at END,
                    payload=excluded.payload""", (item["id"], now, now, None, None, json.dumps(item)))
            ids = {i["id"] for i in active}
            for row in db.execute("SELECT id FROM incidents WHERE resolved_at IS NULL").fetchall():
                if row["id"] not in ids:
                    db.execute("UPDATE incidents SET resolved_at=? WHERE id=?", (now, row["id"]))
            rows = db.execute("SELECT * FROM incidents ORDER BY resolved_at IS NOT NULL,last_seen DESC LIMIT 80").fetchall()
        return [dict(r, payload=json.loads(r["payload"])) for r in rows]


def scope(row, default="unknown"):
    identity = row.get("review_identity") or row.get("identity") or {}
    account = row.get("account_id") or identity.get("account_id")
    paper = row.get("paper_mode", identity.get("paper_mode"))
    if account and isinstance(paper, bool):
        return ("broker_paper:" if paper else "live:")+str(account)
    workspace = row.get("workspace")
    if workspace == "paper" or row.get("source") == "moss_paper" or default == "paper":
        return "paper:local"
    return "unverified:"+str(workspace or default)


def sync(desk, store):
    with desk._lock:
        signals = copy.deepcopy(desk.load_signals())
        decisions = desk._load_json(desk.DECISIONS_PATH, {})
        events = copy.deepcopy(decisions.get("events", []) if isinstance(decisions, dict) else decisions)
        ledger = copy.deepcopy(desk.load_ledger())
        broker = desk._load_json(desk.DATA_DIR/"broker_execution_journal.json", {}).get("executions", [])
        briefs = desk._load_json(desk.DATA_DIR/"research_companion.json", {}).get("briefs", [])
        options = desk._load_json(desk.DATA_DIR/"options_paper.json", {})
    batches = [("signal", signals, "unknown"), ("decision", events, "research"),
               ("fill", ledger.get("fills", []), "paper"), ("fill", ledger.get("broker_fills", []), "live"),
               ("execution", broker, "live"), ("notebook", briefs, "research"),
               ("option_fill", options.get("fills", []), "paper")]
    records = []
    for kind, rows, default in batches:
        for row in rows:
            logical = row.get("execution_id") or row.get("id") or row.get("seq")
            if logical is None:
                continue
            records.append((kind, scope(row, default), logical, row))
    store.put_many(records)
    return len(records)


def dossier(store, ident):
    record = store.get(ident)
    payload = record["payload"]
    related, relationships = [], []
    # Only explicit references within a verified workspace/account may join.
    reference = payload.get("signal_id") or payload.get("order_ref")
    if reference and not record["scope"].startswith("unverified:"):
        with store.db() as db:
            rows = db.execute("SELECT id FROM records WHERE scope=? AND kind='signal' AND logical_id=? ORDER BY observed_at",
                              (record["scope"], str(reference))).fetchall()
        for row in rows:
            signal = store.get(row["id"])
            a, b = payload.get("con_id"), signal["payload"].get("con_id")
            if payload.get("asset_type") in ("OPT", "FOP", "FUT") and (not a or a != b):
                continue
            if payload.get("ticker") and payload.get("ticker") != signal["payload"].get("ticker"):
                continue
            related.append(signal)
            relationships.append({"type": "explicit_signal_reference", "from": ident, "to": row["id"],
                                  "source": "Retained order_ref/signal_id and matching account/workspace", "observed_at": record["observed_at"]})
    for evidence_id in payload.get("input_evidence_ids", []):
        try:
            linked = store.get(evidence_id)
        except ValueError:
            continue
        related.append(linked)
        relationships.append({"type": "recorded_input", "from": ident, "to": evidence_id, "source": "Recorded execution trace"})
    decision_id = payload.get("decision_record_id")
    if decision_id and record["scope"] == "paper:local":
        with store.db() as db:
            ids = db.execute("SELECT id FROM records WHERE kind='decision' AND scope=? AND logical_id=? ORDER BY observed_at",
                             (record["scope"], str(decision_id))).fetchall()
        for row in ids:
            linked = store.get(row["id"])
            if payload.get("ticker") != linked["payload"].get("ticker"):
                continue
            related.append(linked)
            relationships.append({"type": "recorded_research_decision", "from": ident, "to": row["id"], "source": "Explicit decision_record_id in local paper workspace"})
    return {"record": record, "related": related, "relationships": relationships,
            "missing": [] if related else ["No verified explicit link to other retained records. Ticker equality alone is insufficient."],
            "note": "Append-only observed revisions. Indexed time is not market time. A later correction never overwrites this snapshot."}


def attention(book, paper, universe, traces):
    items = []
    account = str(book.get("account_id") or "unverified")
    def add(key, title, effect, action, severity="blocking"):
        items.append({"id": key, "title": title, "effect": effect, "action": action, "severity": severity})
    if not book.get("ok"):
        add("broker:"+account, "Broker account data unavailable", "Account → positions / balance → new live risk blocked",
            "Check Gateway connection and account identity in Live trading.")
    elif finite(book.get("day_pnl_usd")) is None:
        add("pnl:"+account, "Daily P&L has no verified value", "Gateway P&L → daily loss check → new live risk blocked",
            "Inspect the P&L feed diagnostic. A connected socket does not establish daily P&L.")
    elif not book.get("risk_ready"):
        add("risk:"+account, "Broker risk data is not ready", str(book.get("risk_error") or "New live risk blocked"),
            "Review the account health details.")
    if paper.get("error"):
        add("paper:worker", "Paper research needs attention", str(paper["error"]), "Inspect the latest trace and paper rule results.", "research")
    if universe.get("error"):
        add("market:directory", "Symbol directory refresh failed", "Retained directory → discovery may be outdated",
            "Refresh the directory after the provider recovers.", "research")
    for trace in traces[:1]:
        if trace["payload"].get("status") == "failed":
            add("trace:"+trace["logical_id"], "Latest researcher run failed", str(trace["payload"].get("error")),
                "Open its evidence record; the failed run remains visible.", "research")
    return items


def rules(cfg, paper, now=None):
    now = now or datetime.now(timezone.utc)
    p = moss_policy.settings(cfg)
    conditions = [{"name": "Moss paper enabled", "passed": p["enabled"]},
                  {"name": "Separate paper workspace enabled", "passed": bool(cfg.get("paper_research_enabled"))},
                  {"name": "Paper approval enabled", "passed": bool(cfg.get("paper_auto_approve"))},
                  {"name": "US regular session open", "passed": paper_loop.is_rth(now)}]
    from research_companion import next_session
    last = (paper.get("today") or {}).get("last_result")
    return [{"id": "moss_paper", "trigger": f"Every {p['interval_sec']} seconds during US regular sessions",
             "conditions": conditions, "effect": "Collect eligible US candidates; existing risk gate may approve a local paper fill",
             "next_run": (now+timedelta(seconds=p["interval_sec"])).isoformat() if all(c["passed"] for c in conditions)
             else next_session(now) if all(c["passed"] for c in conditions[:3]) else None,
             "next_run_basis": "Earliest scheduling estimate; existing worker cooldown, budget and close cutoff still apply",
             "last_result": last, "busy": paper.get("busy", False), "retry": "One reserved cycle per interval; no duplicate scheduler",
             "budget": {"calls_per_day": p["max_model_calls"], "recorded_cost_usd": p["model_budget_usd"], "filled_trades": p["max_trades_per_day"]},
             "per_candidate_checks": ["Timestamped fresh quote", "Completed intraday bars", "Liquidity and spread", "Paper loss / position limits", "Approval and unchanged settings at fill"],
             "broker_writes": False}]


def metrics(rows, threshold=0., friction=0.):
    selected = [r for r in rows if finite(r.get("confidence")) is not None and finite(r["confidence"]) >= threshold]
    values = [r["outcome_executable_move_bps"]-friction for r in selected]
    return {"samples": len(values), "coverage": len(values)/len(rows) if rows else None,
            "mean_net_bps": sum(values)/len(values) if values else None,
            "win_rate": sum(v > 0 for v in values)/len(values) if values else None,
            "recorded_model_cost_usd": sum(finite(r.get("model_cost_usd")) or 0 for r in selected),
            "missing_cost_samples": sum(finite(r.get("model_cost_usd")) is None for r in selected),
            "evidence_ids": [r.get("evidence_id") for r in selected]}


def evaluate(events, friction, now):
    qualified, excluded = moss_policy.qualified(events, now)
    rows = [r for r in qualified if finite(r.get("confidence")) is not None and 0 <= finite(r["confidence"]) <= 1]
    excluded["missing_or_invalid_confidence"] = len(qualified)-len(rows)
    rows.sort(key=lambda r: (timestamp(r["ts"]), str(r["id"])))
    n = len(rows)
    a, b = int(n*.6), int(n*.8)
    train, validation, test = rows[:a], rows[a:b], rows[b:]
    # Purge cross-symbol overlapping horizons across split boundaries as well.
    if validation:
        train = [r for r in train if timestamp(r["outcome_ts"]) < timestamp(validation[0]["ts"])]
    if test:
        validation = [r for r in validation if timestamp(r["outcome_ts"]) < timestamp(test[0]["ts"])]
    splits = {"train": train, "validation": validation, "test": test}
    variants = []
    for threshold in (0., .6, .7, .8):
        variants.append({"confidence_threshold": threshold, "splits": {k: metrics(v, threshold, friction) for k, v in splits.items()}})
    return {"variants": variants, "excluded": excluded, "qualified": len(rows),
            "split_ids": {k: [r["id"] for r in v] for k, v in splits.items()},
            "purged_boundary_samples": n-sum(len(v) for v in splits.values()),
            "status": "descriptive_review_required" if all(len(v) >= 30 for v in splits.values()) else "insufficient_evidence",
            "hold_cash_baseline_net_bps": 0, "extra_roundtrip_friction_bps": friction,
            "scope": "Moss local-paper outcomes only; fixed chronological 60/20/20 split, purged boundaries. Re-filtering recorded decisions does not simulate different entries or portfolio returns.",
            "limitations": "All four prespecified variants retained. Outcomes may be dependent across instruments/regimes. No live promotion; 20 samples is not proof of an edge."}


def record_trace(desk, run_id, started, status, inputs, output, error=None):
    store = EvidenceStore(desk.DATA_DIR)
    ids = [store.put("research_input", "paper:local", f"{run_id}:{i}", item) for i, item in enumerate(inputs)]
    return store.put("trace", "paper:local", run_id, {"started_at": started, "finished_at": utc(),
                    "duration_sec": max(0, time.time()-(timestamp(started) or time.time())),
                    "status": status, "input_evidence_ids": ids, "output": output, "error": error,
                    "scope": "Recorded tool observations and outcome; no private model chain-of-thought"})


def register(app, desk, companion):
    bp = Blueprint("operations", __name__)
    def store():
        return EvidenceStore(desk.DATA_DIR)

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/operations")
    def overview():
        import market_universe
        from release_tools import manifest
        evidence = store()
        sync(desk, evidence)
        book = desk._broker_book_cached() or {}
        paper = companion.paper.status()
        universe = market_universe.status(desk)
        traces = evidence.list("trace", 5)
        incidents = evidence.incidents(attention(book, paper, universe, traces))
        cfg = desk.load_config()
        return jsonify(ok=True, incidents=incidents, rules=rules(cfg, paper),
                       records=evidence.list(limit=100), traces=traces,
                       release=manifest(desk.APP_DIR), evidence_schema=VERSION,
                       research_config_fingerprint=fingerprint({k: cfg.get(k) for k in ("moss_paper", "brain_mode", "paper_risk_preset", "slip_bps", "fee_bps")}),
                       health={"broker_connected_data": bool(book.get("ok")), "daily_pnl_valid": finite(book.get("day_pnl_usd")) is not None,
                               "risk_ready": bool(book.get("risk_ready")), "directory_count": universe["count"]})

    @bp.post("/api/operations/incidents/<ident>/review")
    def review_incident(ident):
        with store().db() as db:
            if not db.execute("SELECT id FROM incidents WHERE id=?", (ident,)).fetchone():
                raise ValueError("Incident not found")
            db.execute("UPDATE incidents SET reviewed_at=? WHERE id=?", (utc(), ident))
        return jsonify(ok=True, note="Marked reviewed. The underlying risk/data gate remains active.")

    @bp.get("/api/operations/evidence/<ident>")
    def inspect(ident):
        return jsonify(ok=True, **dossier(store(), ident))

    @bp.get("/api/operations/evidence")
    def records():
        return jsonify(ok=True, records=store().list(request.args.get("kind") or None))

    @bp.route("/api/operations/suites", methods=["GET", "POST"])
    def suites():
        evidence = store()
        if request.method == "POST":
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or set(body)-{"extra_friction_bps", "hypothesis"}:
                raise ValueError("Use hypothesis and extra_friction_bps; candidate search is bounded to four fixed thresholds")
            friction = finite(body.get("extra_friction_bps", 5))
            hypothesis = str(body.get("hypothesis") or "Higher recorded confidence may improve net expectancy").strip()
            if friction is None or not 0 <= friction <= 100 or not 1 <= len(hypothesis) <= 500:
                raise ValueError("Use 0–100 extra round-trip bps and a hypothesis up to 500 characters")
            from moss_paper import history
            events = copy.deepcopy(history(desk))
            now = datetime.now(timezone.utc)
            for event in events:
                event["evidence_id"] = evidence.put("decision", "paper:local", event["id"], event)
            result = evaluate(events, friction, now)
            ident = uuid.uuid4().hex
            item = {"id": ident, "created_at": now.isoformat(), "engine_version": VERSION, "hypothesis": hypothesis,
                    "capture": events, "capture_hash": fingerprint(events), "result": result,
                    "proposal": "Compare all recorded variants; observe a fixed candidate on new paper outcomes before review.",
                    "search_budget": {"variants": 4, "model_calls": 0, "changes_to_runtime_policy": 0}}
            with evidence.db() as db:
                prior = [json.loads(r[0]) for r in db.execute("SELECT payload FROM suites")]
                previous_test_ids = {e for p in prior for e in p["result"]["split_ids"]["test"]}
                item["reused_test_outcomes"] = len(previous_test_ids.intersection(result["split_ids"]["test"]))
                db.execute("INSERT INTO suites VALUES(?,?,?,?)", (ident, utc(), "offline_evaluated", json.dumps(item, allow_nan=False)))
                db.execute("INSERT INTO transitions(suite_id,at,stage,note) VALUES(?,?,?,?)", (ident, utc(), "proposed", hypothesis))
                db.execute("INSERT INTO transitions(suite_id,at,stage,note) VALUES(?,?,?,?)", (ident, utc(), "offline_evaluated", result["status"]))
        with evidence.db() as db:
            rows = db.execute("SELECT * FROM suites ORDER BY created_at DESC LIMIT 30").fetchall()
        output = []
        for row in rows:
            item = json.loads(row["payload"])
            item.pop("capture", None)
            with evidence.db() as db:
                item["transitions"] = [dict(r) for r in db.execute("SELECT at,stage,note FROM transitions WHERE suite_id=? ORDER BY id", (item["id"],))]
            output.append(dict(item, stage=row["stage"]))
        return jsonify(ok=True, suites=output)

    @bp.get("/api/operations/suites/<ident>/replay")
    def replay_suite(ident):
        with store().db() as db:
            row = db.execute("SELECT payload FROM suites WHERE id=?", (ident,)).fetchone()
        if not row:
            raise ValueError("Suite not found")
        item = json.loads(row[0])
        if fingerprint(item["capture"]) != item["capture_hash"] or item["engine_version"] != VERSION:
            raise ValueError("Capture integrity or evaluator version changed")
        result = evaluate(item["capture"], item["result"]["extra_roundtrip_friction_bps"], datetime.fromisoformat(item["created_at"]))
        return jsonify(ok=True, identical=result == item["result"], result=result)

    @bp.post("/api/operations/suites/<ident>/stage")
    def stage(ident):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or set(body)-{"stage", "note"} or body.get("stage") not in ("paper_observation", "reviewed") or not 10 <= len(str(body.get("note") or "")) <= 1000:
            raise ValueError("Choose paper_observation or reviewed and record a 10–1000 character review note")
        with store().db() as db:
            row = db.execute("SELECT stage FROM suites WHERE id=?", (ident,)).fetchone()
            if not row or {"offline_evaluated": "paper_observation", "paper_observation": "reviewed"}.get(row[0]) != body["stage"]:
                raise ValueError("Stages must progress from offline evaluation to paper observation to reviewed")
            changed = db.execute("UPDATE suites SET stage=? WHERE id=? AND stage=?", (body["stage"], ident, row[0]))
            if changed.rowcount != 1:
                raise ValueError("Review stage changed during this request; refresh before continuing")
            db.execute("INSERT INTO transitions(suite_id,at,stage,note) VALUES(?,?,?,?)", (ident, utc(), body["stage"], body["note"]))
        return jsonify(ok=True, note="Research review stage recorded. No runtime policy or live setting was changed.")

    @bp.post("/api/operations/scenarios/<ident>")
    def scenarios(ident):
        from desk_workbench import experiment_parameters, replay
        raw = desk._load_json(desk.DATA_DIR/"workbench.json", {})
        item = next((e for e in raw.get("experiments", []) if e["id"] == ident), None)
        if not item:
            raise ValueError("Select a retained workbench experiment")
        if fingerprint({k: item[k] for k in ("parameters", "captured_policy", "capture", "engine_version")}) != ident:
            raise ValueError("Experiment fingerprint mismatch")
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or set(body)-set(item["parameters"]):
            raise ValueError("Use the existing experiment parameter names")
        params = experiment_parameters(dict(item["parameters"], **body))
        baseline, candidate = replay(item["capture"], item["parameters"]), replay(item["capture"], params)
        return jsonify(ok=True, baseline=baseline, candidate=candidate, parameters=params,
                       pnl_difference=candidate["scenario_pnl_sum"]-baseline["scenario_pnl_sum"], immutable_capture=ident)

    app.register_blueprint(bp)
    app.config["OPERATIONS_AVAILABLE"] = True
