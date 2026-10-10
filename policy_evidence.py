"""Prospective policy receipts and chronological, read-only research evaluation.

No model reruns, retrospective policy labels, execution simulation or promotion.
"""
import copy
import hashlib
import json
import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime

VERSION = "policy-receipt-v1"
SENSITIVE = re.compile(r"secret|password|token|credential|api.?key|account|identity|webhook", re.I)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def scrub(value):
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if not SENSITIVE.search(k)}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return copy.deepcopy(value)


def finite_json(value):
    """Missing numbers (NaN/inf, e.g. a 50-day average for a new listing) become null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_json(v) for v in value]
    return value


def capture(config, facts, at, code):
    facts = finite_json(facts)
    policy = finite_json(scrub(config))
    # These process options influence advice/gates but are not stored in config.
    env = {k: os.environ.get(k) for k in ("CLAUDE_MODEL", "CLAUDE_EFFORT", "CLAUDE_FALLBACKS",
            "GEMINI_MODEL", "GEMINI_THINKING_LEVEL", "SHADOW_AUDITOR", "SHADOW_GATE", "ADVISORY_PANEL",
            "ADVISORY_SOFT_SIZE", "ADVISORY_USE_GEMINI", "BRAIN_ROUTER", "MIN_DECISION_CONFIDENCE", "DECISION_HORIZON_MIN")}
    content = {"version": VERSION, "config": policy, "environment": env, "code": code}
    return {"policy": content, "policy_hash": digest(content), "captured_at": at,
            "inputs_hash": digest(facts),
            "scope": "Research-time policy and inputs. Dynamic execution gates, fills and broker lifecycle are not reproduced."}


def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def stats(rows):
    curve = peak = drawdown = 0.
    for row in rows:
        curve += row["net"]
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    return {"samples": len(rows), "mean_net_bps": round(curve / len(rows), 4) if rows else None,
            "cash_baseline_bps": 0, "mean_long_baseline_bps": round(sum(r["long"] for r in rows) / len(rows), 4) if rows else None,
            "sequence_drawdown_bps": round(drawdown, 4),
            "failures": sum(r["net"] <= 0 for r in rows)}


def evaluate(events):
    excluded, groups, seen = Counter(), defaultdict(list), set()
    for e in events:
        why = None
        receipt = e.get("policy_receipt") or {}
        start, end = stamp(e.get("ts")), stamp(e.get("outcome_market_time"))
        due, captured = stamp(e.get("outcome_due_ts")), stamp(receipt.get("captured_at"))
        try:
            intact = (receipt.get("policy", {}).get("version") == VERSION
                      and digest(receipt["policy"]) == receipt["policy_hash"]
                      and digest(e["inputs"]) == receipt["inputs_hash"])
        except (KeyError, TypeError, ValueError):
            intact = False
        side, move, cost = e.get("intended_side"), e.get("outcome_move_bps"), e.get("outcome_cost_bps")
        if not intact:
            why = "missing_or_changed_prospective_receipt"
        elif not e.get("id") or e["id"] in seen:
            why = "duplicate_or_missing_id"
        elif (not start or not end or not due or not captured or captured > start or end <= start
              or abs((end-due).total_seconds()) > 120):
            why = "missing_or_invalid_timing"
        elif (e.get("outcome_status") != "scored" or e.get("scoring_version") != "horizon-net-v2"
              or e.get("mock") or e.get("routed") or e.get("error") or e.get("execution_block")
              or side not in ("buy", "sell") or e.get("verdict") != "PASS"
              or not numeric(move) or not numeric(cost) or cost < 0):
            why = "ineligible_decision_or_outcome"
        else:
            for item in (e.get("inputs", {}).get("research_evidence") or {}).get("items", []):
                published = stamp(item.get("published_at"))
                if not published or published > start:
                    why = "undated_or_future_source"
                    break
        if why:
            excluded[why] += 1
            continue
        seen.add(e["id"])
        key = (receipt["policy_hash"], e.get("llm_model"), e.get("prompt_version"), e.get("horizon_min"))
        groups[key].append({"start": start, "end": end, "id": e["id"],
                            "net": (move if side == "buy" else -move)-cost, "long": move-cost})
    result = []
    for key, rows in groups.items():
        rows.sort(key=lambda r: (r["start"], r["id"]))
        separate = []
        for row in rows:
            if separate and row["start"] < separate[-1]["end"]:
                excluded["overlapping_horizon"] += 1
            else:
                separate.append(row)
        cut = max(1, math.floor(len(separate)*.6))
        earlier, later = stats(separate[:cut]), stats(separate[cut:])
        state = "insufficient_evidence"
        if earlier["samples"] >= 30 and later["samples"] >= 20:
            state = "no_edge" if later["mean_net_bps"] <= max(0, later["mean_long_baseline_bps"]) else "positive_sample_unqualified"
        result.append({"policy_hash": key[0], "model": key[1], "prompt": key[2], "horizon_min": key[3],
                       "state": state, "earlier": earlier, "later": later})
    return {"version": VERSION, "groups": result, "exclusions": dict(excluded),
            "state": "prospective_collection" if not result else "descriptive_validation",
            "note": "Chronological 60/40 split within identical recorded policy/model/prompt/horizon, with overlapping horizons excluded. At least 30 earlier and 20 later observations are needed. No model is retrained. Returns start at the research input snapshot; model response latency is not executable entry proof. Cash and long-only baselines use the same selected windows. Sequence drawdown is a sum of fixed-unit scenarios, not portfolio drawdown. Selection bias, market impact and broker lifecycle remain unqualified; this never promotes live trading."}
