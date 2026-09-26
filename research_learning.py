"""Auditable research-ranking model trained on qualified recorded outcomes.

Beta(2,2) prior, bounded ranking coefficients, no execution authority. These are
real fitted statistical parameters, separate from a foundation model's weights.
"""
from datetime import datetime, timezone
from desk_workbench import finite, timestamp, fingerprint

VERSION = "research-ranker-beta-v1"


def train(events, now=None):
    now = now or datetime.now(timezone.utc)
    groups, evidence, seen = {}, [], set()
    for event in events:
        if not isinstance(event, dict):
            continue
        key = str(event.get("id") or "")
        if not key or key in seen:
            continue
        seen.add(key)  # Latest record wins, including a withdrawn/invalid score.
        side = event.get("intended_side") or event.get("decision") or event.get("side")
        net = finite(event.get("outcome_executable_move_bps"))
        start, end = timestamp(event.get("ts")), timestamp(event.get("outcome_ts"))
        horizon = finite(event.get("horizon_min"))
        if (side not in ("buy", "sell") or net is None or
            event.get("outcome_status") != "scored" or event.get("scoring_version") != "horizon-net-v2" or
            event.get("mock") or event.get("brain_mode") == "mock" or event.get("routed") or
            event.get("error") or event.get("execution_block") or not event.get("input_hash") or
            not event.get("llm_model") or not event.get("prompt_version") or
            start is None or end is None or not start < end <= now.timestamp() or not horizon or horizon <= 0):
            continue
        # Match the scheduled horizon; late samples must not train the wrong task.
        recorded_tolerance = finite(event.get("outcome_tolerance_sec"))
        tolerance = 120 if recorded_tolerance is None else min(120, max(0, recorded_tolerance))
        if abs(end - start - horizon * 60) > tolerance:
            continue
        scope = (str(event.get("ticker") or ""), event["llm_model"], event["prompt_version"],
                 str(event.get("workspace") or "research"), horizon, side, str(event.get("verdict") or "unknown"))
        bucket = groups.setdefault(scope, [])
        bucket.append(net)
        evidence.append({"id": key, "scope": scope, "input_hash": event["input_hash"], "net_bps": net, "outcome_ts": event["outcome_ts"]})
    weights = []
    for scope, values in sorted(groups.items()):
        wins = sum(v > 0 for v in values)
        alpha, beta = 2 + wins, 2 + len(values) - wins
        mean = sum(values) / len(values)
        coefficient = max(.5, min(1.5, 2 * alpha / (alpha + beta))) if len(values) >= 20 else 1.0
        if mean <= 0:
            coefficient = min(1., coefficient)
        weights.append({"ticker": scope[0], "model": scope[1], "prompt_version": scope[2],
                        "workspace": scope[3], "horizon_min": scope[4], "side": scope[5], "setup": scope[6],
                        "samples": len(values), "positive": wins, "alpha": alpha, "beta": beta,
                        "mean_net_bps": round(mean, 4), "weight": round(coefficient, 4),
                        "active_for_ranking": len(values) >= 20,
                        "evidence": "Descriptive training sample; no held-out profitability claim."})
    return {"version": VERSION, "trained_at": now.isoformat(), "qualified_samples": len(evidence),
            "excluded": len(events) - len(evidence), "evidence_hash": fingerprint(sorted(evidence, key=lambda e:e["id"])),
            "weights": weights, "prior": {"alpha": 2, "beta": 2},
            "purpose": "Research attention only; no live sizing or execution changes."}
