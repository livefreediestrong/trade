"""Descriptive market-capture coverage metrics for research review."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any


def _ts(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError, OSError):
        return None


def scorecard(decisions: list[dict[str, Any]] | None, fills: list[dict[str, Any]] | None, *, now: float | None = None) -> dict[str, Any]:
    rows = [d for d in decisions or [] if isinstance(d, dict) and d.get("event") in (None, "decision", "intent")]
    filled_ids = {str(f.get("signal_id") or f.get("intent_id") or "") for f in fills or [] if isinstance(f, dict)}
    actionable = [d for d in rows if str(d.get("decision") or d.get("side") or "").lower() in ("buy", "sell")]
    outcomes = [d for d in rows if d.get("outcome")]
    favorable_unfilled = [
        d for d in actionable
        if str(d.get("id") or d.get("intent_id") or "") not in filled_ids
        and str(d.get("outcome")).lower() == "helped"
    ]
    blocked = [d for d in actionable if d.get("gated") or d.get("blocked") or d.get("data_error")]
    outcome_counts = Counter(str(d.get("outcome") or "unmeasured") for d in rows)
    capture_rate = len([d for d in actionable if str(d.get("id") or d.get("intent_id") or "") in filled_ids]) / len(actionable) if actionable else None
    return {
        "decisions": len(rows),
        "actionable": len(actionable),
        "fills": len(fills or []),
        "outcomes": len(outcomes),
        "outcome_coverage": round(sum(bool(d.get("outcome")) for d in actionable) / len(actionable), 4) if actionable else None,
        "capture_rate": round(capture_rate, 4) if capture_rate is not None else None,
        "missed_favorable_calls": len(favorable_unfilled),
        "blocked_actionable": len(blocked),
        "outcomes_by_label": dict(outcome_counts),
        "scope": {
            "horizon_outcomes": True,
            "execution_capture": True,
            "missed_opportunity": True,
            "intraday_path_mfe_mae": "conditional_quote_path",
        },
        "note": "Descriptive paper/research coverage; executable costs and path metrics require captured quote paths.",
    }
