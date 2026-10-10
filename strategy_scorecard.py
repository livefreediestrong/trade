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
        if isinstance(value, bool):
            continue
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
    cutoff = now - timedelta(days=days)
    scored = []
    for row in rows:
        if not isinstance(row, dict) or row.get("outcome_status") != "scored" or row.get("mock") or row.get("routed"):
            continue
        try:
            stamp = datetime.fromisoformat(str(row.get("ts") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        # Compare instants, not strings: offsets can put a call outside the window.
        if stamp.tzinfo is not None and cutoff <= stamp <= now:
            scored.append(row)
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


# Automatic real-money entries are allowed only when this pre-registered test passes.
PROMOTION_MIN_CALLS = 60
PROMOTION_MIN_SESSIONS = 10
PROMOTION_MIN_TICKERS = 10
PROMOTION_DAYS = 90


def promotion_status(rows: list[dict[str, Any]], *, hurdle_bps: float, now: datetime | None = None) -> dict[str, Any]:
    """Does the screener PASS buy rule show an edge that survives real commissions?

    Uses scored PASS buy calls (the only kind Fox can trade) from the last 90 days.
    Each trading day counts once (calls on one day are not independent), and the
    lower end of a 95% range of the daily averages must beat `hurdle_bps`, the
    round-trip commission at the account's order size. Missing data fails.
    """
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=PROMOTION_DAYS)
    by_day: dict[str, list[float]] = {}
    tickers = set()
    for row in rows:
        if (not isinstance(row, dict) or row.get("outcome_status") != "scored" or row.get("mock") or row.get("routed")
                or row.get("side") != "buy" or not str(row.get("setup") or "").upper().startswith("PASS|")):
            continue
        move = _net_bps(row)
        try:
            stamp = datetime.fromisoformat(str(row.get("ts") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        if move is None or stamp.tzinfo is None or not cutoff <= stamp <= now:
            continue
        by_day.setdefault(stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat(), []).append(move)
        if row.get("ticker"):
            tickers.add(str(row["ticker"]).upper())
    calls = sum(len(v) for v in by_day.values())
    day_means = [sum(v) / len(v) for v in by_day.values()]
    mean = sum(day_means) / len(day_means) if day_means else None
    lower = None
    if len(day_means) >= 2:
        sd = math.sqrt(sum((m - mean) ** 2 for m in day_means) / (len(day_means) - 1))
        lower = mean - 1.96 * sd / math.sqrt(len(day_means))
    hurdle = float(hurdle_bps)
    missing = []
    if calls < PROMOTION_MIN_CALLS:
        missing.append(f"{calls} of {PROMOTION_MIN_CALLS} scored PASS buy calls")
    if len(day_means) < PROMOTION_MIN_SESSIONS:
        missing.append(f"{len(day_means)} of {PROMOTION_MIN_SESSIONS} trading days")
    if len(tickers) < PROMOTION_MIN_TICKERS:
        missing.append(f"{len(tickers)} of {PROMOTION_MIN_TICKERS} different stocks")
    if lower is None or not math.isfinite(hurdle) or lower <= hurdle:
        shown = f"{lower:+.1f}" if lower is not None else "unknown"
        missing.append(f"the cautious average ({shown} bps a trade) must beat commissions ({hurdle:.0f} bps a round trip)")
    passed = not missing
    return {"passed": passed, "calls": calls, "sessions": len(day_means), "tickers": len(tickers),
            "mean_bps": round(mean, 1) if mean is not None else None,
            "lower_95_bps": round(lower, 1) if lower is not None else None,
            "hurdle_bps": round(hurdle, 1) if math.isfinite(hurdle) else None,
            "missing": missing,
            "plain": ("The PASS rule's scored record beats commissions; automatic buys may run under the other limits."
                      if passed else "Automatic buys are locked until the evidence shows an edge: " + "; ".join(missing) + ".")}
