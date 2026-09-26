"""Read-only presentation receipts. Nothing in this module authorizes a trade."""
import hashlib
import json
from datetime import datetime, timezone

import lessons
from fox_workspace import broker_view


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]


def decision_detail(signal):
    s = signal or {}
    flags = list(s.get("research_flags") or [])
    missing = [str(s[k]) for k in ("data_error", "llm_error", "execution_block", "reject_reason") if s.get(k)]
    if "setup_losing_record" in flags:
        missing.append("Matching setup has a losing after-cost record")
    if s.get("net_reward_risk") is not None:
        missing.append(f"Modeled reward / risk after costs: {s['net_reward_risk']}")
    next_condition = ("Resolve the recorded data or execution blocker, then obtain a new assessment."
                      if any(s.get(k) for k in ("data_error", "llm_error", "execution_block", "reject_reason")) else
                      "Collect new qualified after-cost evidence before reconsidering this losing setup."
                      if "setup_losing_record" in flags else
                      "A new assessment with a fresh quote, sufficient after-cost edge and all account checks passing.")
    return {"signal_id": s.get("id"), "evidence_id": s.get("decision_record_id"),
            "missing": missing, "next_condition": next_condition,
            "invalidation": {"stop": s.get("stop"), "target": s.get("target")},
            "confidence_note": "Model confidence is not a calibrated probability of winning."}


def recall_view(desk, signal):
    receipt = (signal or {}).get("learning_context")
    if not receipt:
        return {"available": False, "note": "Original memory receipt was not retained for this assessment."}
    try:
        current = {r["id"]: r for r in lessons.recent(desk.LESSONS_PATH, lessons.MAX_LESSONS)}
    except Exception:
        current = {}
    rows = []
    for r in receipt.get("evidence_rows") or []:
        latest = current.get(r.get("id"))
        status = ("unavailable" if not latest else "withdrawn" if latest.get("outcome_status") != "scored" or latest.get("mock") or latest.get("routed") else
                  "corrected since recall" if (latest.get("revision") or 1) != (r.get("revision") or 1) else "current revision")
        rows.append({**r, "current_status": status})
    return {**receipt, "available": True, "evidence_rows": rows,
            "note": "Receipt captured before the model call. All rows contributed to recalled aggregates or ticker history; the model receives at most 12 detailed examples. Corrected or withdrawn evidence must be reassessed; hypotheses remain unproven."}


def presentation(desk, cfg, agent, fox, research, signal, report, now):
    broker = broker_view(desk, cfg)
    # Expose simultaneous blockers even outside market hours. A closed market does
    # not establish healthy P&L; an enabled switch does not establish readiness.
    blockers = []
    if not broker["risk_ready"]:
        blockers.append(broker["error"] or "Current broker daily P&L is unavailable; new risk is blocked.")
    if agent.get("market_open") is False:
        blockers.append("Regular US trading session is closed.")
    if not agent.get("enabled") or not agent.get("session_active"):
        blockers.append("Automatic trading is paused.")
    if agent.get("phase") in ("blocked", "error", "reconciling"):
        blockers.append(agent.get("message") or "Agent requires attention.")
    evidence = (report or {}).get("evidence") or {}
    verdict = (evidence.get("outcomes") or {}).get("verdict") or {}
    quality = {"data": {"state": "current" if research.get("fresh_sources") else "unavailable",
                         "detail": research.get("summary")},
               "research": {"state": verdict.get("state") or "unavailable",
                             "detail": verdict.get("title") or "Qualified after-cost evidence is not available."},
               "execution": {"state": "blocked" if blockers else "checks required",
                              "detail": " ".join(blockers) if blockers else "Every proposed order still needs final account, quote and risk checks."}}
    case = None
    if report and evidence:
        models = report.get("models") or {}
        parts = {}
        for role in ("changing_woman", "fox"):
            m = models.get(role) or {}
            checked = m.get("checked_result") or {}
            parts[role] = {"status": m.get("status") or "not run", "findings": checked.get("findings") or [],
                           "challenge": checked.get("challenge") or [], "uncertainties": checked.get("uncertainties") or [],
                           "hypotheses": checked.get("hypotheses") or [], "flagged_count": len((m.get("checks") or {}).get("flagged") or [])}
        case = {"id": "nightly:" + str(report.get("day")), "version": digest(evidence), "at": report.get("completed_at"),
                "title": "Shared after-close investigation", "summary": report.get("fact_summary"),
                "roles": parts, "href": "/desk/fox#nightly-review",
                "note": "Both roles received this frozen evidence version. Fox's recorded challenge is shown separately; a timed animation is not evidence of work."}
    detail = decision_detail(signal)
    scope = digest({"identity": cfg.get("broker_identity"), "day": now.date().isoformat()})
    # Exclude ages and polling timestamps from identity: unchanged polls are silent.
    change = {"fox": fox.get("headline"), "state": fox.get("state"), "blockers": blockers,
              "case": {"id": case["id"], "version": case["version"], "roles": case["roles"]} if case else None,
              "decision": detail, "research_state": quality["research"]["state"],
              "source_counts": [research.get("fresh_sources"), research.get("fresh_headlines")]}
    return {"scope": scope, "event_id": digest(change), "as_of": now.isoformat(), "quality": quality,
            "blockers": blockers, "case": case, "decision": detail, "recall": recall_view(desk, signal),
            "what": fox.get("headline"), "why": " ".join(blockers) or (signal or {}).get("reason") or "Waiting for a new recorded assessment.",
            "next": "Restore current broker P&L and account checks, then reassess the saved session and market window." if not broker["risk_ready"] else detail["next_condition"]}
