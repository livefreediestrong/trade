"""Lessons memory — the desk remembers how its own calls turned out.

Every scored decision (helped / hurt / flat after the look-ahead window) becomes one
plain-English lesson built from numbers only (no extra model call, and no outside
text, so nothing here can be used for prompt injection). Before a new decision the
brain is shown the track record for the same kind of setup and this ticker, so it
stops repeating setups that keep losing.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

MAX_LESSONS = 1000
_lock = threading.Lock()


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[:MAX_LESSONS], indent=1), encoding="utf-8")
    tmp.replace(path)


def setup_key(verdict: Any, lateness: Any) -> str:
    v = str(verdict or "?").upper()
    lt = str(lateness or "?").lower()
    return f"{v}|{lt}"


def _side(ev: dict[str, Any]) -> str:
    s = str(ev.get("outcome_intended_side") or ev.get("intended_side") or "flat").lower()
    return {"long": "buy", "short": "sell", "hold": "flat"}.get(s, s)


def lesson_text(ev: dict[str, Any]) -> str:
    t = str(ev.get("ticker") or "?").upper()
    side = _side(ev)
    verdict = str(ev.get("verdict") or "?").upper()
    lateness = str(ev.get("lateness_label") or "unknown timing")
    try:
        bps = float(ev.get("outcome_move_bps") or 0)
    except (TypeError, ValueError):
        bps = 0.0
    move = f"{'up' if bps > 0 else 'down' if bps < 0 else 'flat'} {abs(bps) / 100:.2f}%"
    try:
        conf = f"{float(ev.get('confidence') or 0) * 100:.0f}%"
    except (TypeError, ValueError):
        conf = "?"
    call = {"buy": "Buy", "sell": "Sell"}.get(side, "Hold")
    outcome = str(ev.get("outcome") or "?")
    return (
        f"{t}: {call} call ({verdict}, {lateness} timing, {conf} sure) — price went {move} "
        f"over the look-ahead window, so the call {outcome}."
    )


def record_outcome(path: Path, ev: dict[str, Any]) -> dict[str, Any] | None:
    """Store one lesson for a newly scored decision (idempotent per decision id)."""
    if not ev or not ev.get("outcome"):
        return None
    row = {
        "id": str(ev.get("id") or ""),
        "ts": ev.get("outcome_ts") or ev.get("ts"),
        "ticker": str(ev.get("ticker") or "").upper(),
        "side": _side(ev),
        "setup": setup_key(ev.get("verdict"), ev.get("lateness_label")),
        "outcome": ev.get("outcome"),
        "move_bps": ev.get("outcome_move_bps"),
        "text": lesson_text(ev),
    }
    with _lock:
        rows = _read(path)
        if row["id"] and any(r.get("id") == row["id"] for r in rows):
            return None
        rows.insert(0, row)
        _write(path, rows)
    return row


def _tally(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        side = r.get("side") or "flat"
        d = out.setdefault(side, {"helped": 0, "hurt": 0, "flat": 0})
        o = r.get("outcome")
        if o in d:
            d[o] += 1
    return out


def track_record(path: Path, *, ticker: str, verdict: Any, lateness: Any) -> dict[str, Any]:
    rows = _read(path)
    key = setup_key(verdict, lateness)
    same_setup = [r for r in rows if r.get("setup") == key][:200]
    same_ticker = [r for r in rows if r.get("ticker") == str(ticker or "").upper()][:3]
    return {
        "setup": key,
        "setup_results": _tally(same_setup),
        "setup_count": len(same_setup),
        "ticker_recent": [r.get("text") for r in same_ticker],
    }


def prompt_note(record: dict[str, Any]) -> str | None:
    """Short text for the brain. None when there is no history yet."""
    if not record or (not record.get("setup_count") and not record.get("ticker_recent")):
        return None
    lines = []
    res = record.get("setup_results") or {}
    if res:
        verdict, lateness = (record.get("setup") or "?|?").split("|", 1)
        parts = []
        for side in ("buy", "sell", "flat"):
            d = res.get(side)
            if d and sum(d.values()):
                name = {"buy": "Buy", "sell": "Sell", "flat": "Hold"}[side]
                parts.append(f"{name}: {d['helped']} helped, {d['hurt']} hurt, {d['flat']} flat")
        if parts:
            lines.append(
                f"This desk's past calls on similar setups ({verdict}, {lateness} timing): "
                + "; ".join(parts) + "."
            )
    for t in record.get("ticker_recent") or []:
        lines.append("Recent on this ticker: " + t)
    lines.append(
        "Treat this as evidence: avoid repeating a call that has mostly hurt on this setup "
        "unless today's facts are clearly different."
    )
    return "\n".join(lines)


def recent(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    return _read(path)[: max(1, min(500, int(limit)))]
