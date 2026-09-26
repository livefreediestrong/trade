"""Local checks for known research overclaims; never alter the retained evidence."""
from __future__ import annotations

import copy
import math
import re


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def evidence_summary(evidence):
    outcomes = evidence.get("outcomes") or {}
    today = outcomes.get("today") or {}
    count, mean = number(today.get("outcomes")), number(today.get("mean_net_bps"))
    text = (f"{int(count)} qualified paper observations" if count is not None else "Paper sample count unavailable")
    text += (f", averaging {mean:+.2f} bps after recorded costs." if mean is not None else "; after-cost result unavailable.")
    verdict = outcomes.get("verdict") or {}
    return text + " " + str(verdict.get("title") or "Research conclusion unavailable") + ". Paper observations are not account returns."


def concerns(text, evidence, kind="finding"):
    """Conservative, explicit checks, not a claim to verify arbitrary natural language."""
    normalized = str(text).lower().replace("’", "'")
    issues = []
    if kind == "hypotheses":
        return issues  # Proposed tests may include future sample sizes and target values.
    caveat = re.search(r"(?:not|never|cannot|can't|do not|does not|don't).{0,60}(?:prove|establish|confirm|reflect|account|cash|realized|return)", normalized)
    if (re.search(r"(?:account returns?|cash p\s*&\s*l|realized p\s*&\s*l|realized profit|live account.{0,20}profit)", normalized)
            and not caveat):
        issues.append("Retained executions do not establish account-wide returns or realized P&L. Check the broker statement.")
    if (re.search(r"(?:proven|guaranteed|validated).{0,20}(?:profitable|profitability|edge|returns)", normalized)
            and not re.search(r"(?:not|no|unproven).{0,30}(?:proven|guaranteed|validated)", normalized)):
        issues.append("This research snapshot does not establish a profitable live strategy.")
    outcomes = evidence.get("outcomes") or {}
    counts = {number((outcomes.get(k) or {}).get("outcomes")) for k in ("today", "prior_20_observed_sessions", "all_retained")}
    qualified = outcomes.get("qualified_event_ids")
    if isinstance(qualified, list):
        counts.add(len(qualified))
    counts.discard(None)
    for match in re.finditer(r"\b(\d+) (?:total )?qualified (?:paper )?(?:observations|outcomes)\b", normalized):
        if counts and int(match[1]) not in counts:
            issues.append("The stated qualified sample count does not match the frozen evidence.")
    return list(dict.fromkeys(issues))


def checked_report(report):
    """Annotate both historical and new reports on read; preserve original model text."""
    if not isinstance(report, dict) or not isinstance(report.get("evidence"), dict):
        return copy.deepcopy(report)
    out = copy.deepcopy(report)
    evidence = out["evidence"]
    out["fact_summary"] = evidence_summary(evidence)
    for model in (out.get("models") or {}).values():
        result = model.get("result")
        if not isinstance(result, dict):
            continue
        checked = copy.deepcopy(result)
        flagged = []
        flags = concerns(result.get("summary"), evidence)
        if flags:
            flagged.append({"section": "summary", "text": result.get("summary"), "reasons": flags})
        # Quantitative headline always comes from local calculations.
        checked["summary"] = out["fact_summary"]
        for key in ("findings", "challenge", "uncertainties", "hypotheses"):
            checked[key] = []
            for row in result.get(key) or []:
                flags = concerns(row.get("text"), evidence, key)
                if flags:
                    flagged.append({"section": key, "text": row.get("text"), "reasons": flags,
                                    "evidence_ids": row.get("evidence_ids") or []})
                else:
                    checked[key].append(copy.deepcopy(row))
        model["checked_result"] = checked
        model["checks"] = {"version": "known-claims-v1", "flagged": flagged,
                           "note": "Local checks cover known account-return, sample-count and profitability overclaims. Remaining prose is AI interpretation, not verified fact."}
    return out
