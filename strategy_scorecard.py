"""Strategy scorecard: how each kind of setup has done, in one table (docs/STRATEGY_SCORECARD.md).

Built from the desk's own scored calls in lessons memory (lessons.py). Each buy or sell
call is scored on the price move over its look-ahead window after estimated trading
costs. That is a measurement of the call, not account profit. Display only: nothing
here changes a gate. Fox's own `_evidence_block` already stops losing setups.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any

import lessons

MIN_CALLS = 30  # fewer scored calls than this cannot separate skill from luck

VERDICT_LABELS = {"PASS": "Screener PASS", "WATCH": "Watch-list idea", "AVOID": "Avoid idea"}
TIMING_LABELS = {"early": "early entry", "fair": "fair timing", "late": "late entry", "chasing": "chasing a move"}


def setup_label(key: str) -> str:
    verdict, _, timing = str(key or "?|?").partition("|")
    return f"{VERDICT_LABELS.get(verdict.upper(), verdict.title() or 'Unknown')}, {TIMING_LABELS.get(timing.lower(), timing or 'unknown timing')}"


def _net_bps(row: dict[str, Any]) -> float | None:
    for value in (row.get("outcome_executable_move_bps"),):
        try:
            n = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(n):
            return n
    return None


def _verdict(calls: int, avg: float | None) -> tuple[str, str]:
    if avg is None:
        return "unscored", "No after-cost results yet."
    if calls < MIN_CALLS:
        return "too_few", f"Too few calls to judge ({calls} of {MIN_CALLS} needed)."
    if avg <= 0:
        return "losing", "Losing after costs. Following these calls would have cost money."
    return "positive", "Positive after costs so far. Keep testing before trusting it."


def scorecard(rows: list[dict[str, Any]], *, days: int = 30, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat()
    scored = [r for r in rows if r.get("outcome_status") == "scored" and not r.get("mock") and not r.get("routed")
              and str(r.get("ts") or "") >= cutoff]
    groups: dict[str, list[dict[str, Any]]] = {}
    stayed_out = {"calls": 0, "right": 0}
    for row in scored:
        side = row.get("side")
        if side in ("buy", "sell"):
            groups.setdefault(f"{row.get('setup')}|{side}", []).append(row)
        elif side == "flat":
            stayed_out["calls"] += 1
            stayed_out["right"] += int(row.get("outcome") in ("helped", "flat"))
    table = []
    for key, items in groups.items():
        setup, _, side = key.rpartition("|")
        moves = [m for m in (_net_bps(r) for r in items) if m is not None]
        wins = sum(1 for m in moves if m > 0)
        avg = round(sum(moves) / len(moves), 1) if moves else None
        status, plain = _verdict(len(moves), avg)
        table.append({
            "setup": setup, "side": side, "label": f"{setup_label(setup)} · {'buy' if side == 'buy' else 'sell'} calls",
            "calls": len(items), "scored": len(moves), "win_rate": round(wins / len(moves), 3) if moves else None,
            "avg_net_bps": avg, "total_net_bps": round(sum(moves), 1) if moves else None,
            "status": status, "plain": plain,
            "example": (f"On a $1,000 trade, {avg:+.1f} bps is about {'-' if avg < 0 else '+'}${abs(avg) / 10:.2f} per call after costs."
                        if avg is not None else None),
        })
    order = {"losing": 0, "positive": 1, "too_few": 2, "unscored": 3}
    table.sort(key=lambda r: (order[r["status"]], -r["scored"]))
    losing = sum(1 for r in table if r["status"] == "losing")
    positive = sum(1 for r in table if r["status"] == "positive")
    summary = (f"{len(table)} kinds of buy/sell calls in the last {days} days: {positive} positive after costs, "
               f"{losing} losing, {len(table) - positive - losing} still too new to judge."
               if table else f"No scored buy or sell calls in the last {days} days yet.")
    if stayed_out["calls"]:
        summary += (f" Staying out was right {stayed_out['right']} of {stayed_out['calls']} times "
                    f"(the price did not move enough to pay for a trade).")
    return {"ok": True, "days": days, "as_of": now.isoformat(), "summary": summary, "rows": table,
            "stayed_out": stayed_out, "min_calls": MIN_CALLS,
            "note": ("Scores each call on the price move over its look-ahead window after estimated costs, the same "
                     "measure Fox uses to stop losing setups. It is not account profit and does not include SPY."),
            "display_only": True}


def register(app, desk) -> None:
    from flask import jsonify, request

    @app.get("/api/strategy-scorecard")
    def strategy_scorecard_route():
        try:
            days = max(1, min(365, int(request.args.get("days") or 30)))
        except ValueError:
            return jsonify({"ok": False, "error": "days must be a whole number"}), 400
        return jsonify(scorecard(lessons.recent(desk.LESSONS_PATH, 5000), days=days))
