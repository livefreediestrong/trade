"""History test for Fox's buy rule (docs/LIVE_AGENT.md, "History test").

Replays the PASS rule on ~2 years of hourly bars for Fox's own stock list (backtest.py:
decide at 10:30 ET, exit at stop/target/close, no look-ahead) and reports three cost lines:
before costs, with $0 commission (slippage only), and at this account's real costs.

Owner decision (2026-09-28): automatic buys also require this test to pass at the
account's real costs (day-clustered 95% lower bound above zero and consistent
walk-forward folds), and Fox does no research while the latest test says the rule loses.
A missing or stale (> STALE_DAYS) result fails closed for buys. Exits are never affected.
"""
from __future__ import annotations

import collections
import json
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

STALE_DAYS = 30
SLIP_BPS = 5.0          # per side, same as the backtest default
MIN_TRADES = 200
_lock = threading.Lock()
_state: dict[str, Any] = {"running": False, "progress": 0, "total": 0, "message": ""}


def _path(desk) -> Path:
    return Path(desk.DATA_DIR) / "rule_history.json"


def line(trades: list[dict[str, Any]], cost_bps: float) -> dict[str, Any]:
    """Average, win rate and a day-clustered 95% lower bound, in bps per trade after costs."""
    if not trades:
        return {"trades": 0}
    by_day: dict[str, list[float]] = collections.defaultdict(list)
    for t in trades:
        by_day[t["day"]].append(t["ret"] * 1e4 - cost_bps)
    values = [v for day in by_day.values() for v in day]
    means = [sum(v) / len(v) for v in by_day.values()]
    avg = sum(values) / len(values)
    lower = None
    if len(means) > 2:
        m = sum(means) / len(means)
        sd = math.sqrt(sum((x - m) ** 2 for x in means) / (len(means) - 1))
        lower = round(m - 1.96 * sd / math.sqrt(len(means)), 1)
    return {"trades": len(values), "days": len(by_day), "avg_bps": round(avg, 1),
            "win_rate": round(sum(v > 0 for v in values) / len(values), 3), "lower95_bps": lower}


def summarize(trades: list[dict[str, Any]], cost_bps_today: float | None, *, walk=None) -> dict[str, Any]:
    import backtest
    rule = [t for t in trades if t["rule"]]
    zero = 2 * SLIP_BPS + 1.0  # slippage both sides + ~regulatory fees
    lines = {
        "before_costs": line(rule, 0.0),
        "zero_commission": line(rule, zero),
        "your_costs": line(rule, cost_bps_today) if cost_bps_today is not None else {"trades": len(rule), "unknown": True},
        "random_entry_before_costs": line(trades, 0.0),
    }
    wf = walk or backtest.evaluate_walk_forward(trades, {})
    yours = lines["your_costs"]
    reasons = []
    zero_line = lines["zero_commission"]
    if zero_line.get("trades", 0) >= MIN_TRADES and (zero_line.get("lower95_bps") is None or zero_line["lower95_bps"] <= 0):
        # Decisive whatever the account pays: it loses with no commission at all.
        reasons.append(f"it loses money even with $0 commission ({zero_line['avg_bps']:+.1f} bps a trade)")
    elif yours.get("unknown"):
        reasons.append("this account's costs are unknown (broker account not verified)")
    elif yours["trades"] < MIN_TRADES:
        reasons.append(f"only {yours['trades']} historical trades (needs {MIN_TRADES})")
    elif yours.get("lower95_bps") is None or yours["lower95_bps"] <= 0:
        reasons.append(f"at your costs the rule averaged {yours['avg_bps']:+.1f} bps a trade "
                       f"(cautious estimate {yours.get('lower95_bps')} bps), so it loses money")
    if wf.get("conclusion") != "consistent_out_of_sample":
        reasons.append(f"it made money in only {wf.get('positive_rule_folds')} of {wf.get('tested_folds')} later test periods")
    return {"lines": lines, "passed": not reasons, "reasons": reasons,
            "walk_forward": {k: wf.get(k) for k in ("tested_folds", "positive_rule_folds", "rule_beats_baseline_folds", "conclusion")}}


def plain(result: dict[str, Any] | None) -> str:
    if not result:
        return "No history test yet. Run it on the Real account page; automatic buys stay locked until it passes."
    s = result.get("summary") or {}
    lines = s.get("lines") or {}
    b, z, y = lines.get("before_costs", {}), lines.get("zero_commission", {}), lines.get("your_costs", {})
    head = (f"On {b.get('trades', 0):,} historical trades ({result.get('period', {}).get('start')} to "
            f"{result.get('period', {}).get('end')}, {len(result.get('tickers') or [])} stocks) the buy rule averaged "
            f"{b.get('avg_bps', 0):+.1f} bps a trade before costs, {z.get('avg_bps', 0):+.1f} bps with $0 commission")
    head += "." if y.get("unknown") else f", and {y.get('avg_bps', 0):+.1f} bps at your costs today."
    if s.get("passed"):
        return head + " It passes the history test."
    return head + " It fails the history test: " + "; ".join(s.get("reasons") or []) + "."


def latest(desk) -> dict[str, Any] | None:
    try:
        return json.loads(_path(desk).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def gate(desk, *, now: datetime | None = None) -> dict[str, Any]:
    """{'passed', 'state': passed|failed|missing|stale, 'plain'} — fails closed."""
    result = latest(desk)
    if not result:
        return {"passed": False, "state": "missing", "plain": plain(None)}
    try:
        finished = datetime.fromisoformat(result["finished_at"])
    except (KeyError, TypeError, ValueError):
        return {"passed": False, "state": "missing", "plain": plain(None)}
    if (now or datetime.now(timezone.utc)) - finished > timedelta(days=STALE_DAYS):
        return {"passed": False, "state": "stale",
                "plain": f"The history test is more than {STALE_DAYS} days old. Run it again; automatic buys stay locked until then."}
    passed = bool((result.get("summary") or {}).get("passed"))
    return {"passed": passed, "state": "passed" if passed else "failed", "plain": plain(result),
            "finished_at": result["finished_at"]}


def status(desk) -> dict[str, Any]:
    with _lock:
        state = dict(_state)
    return {"ok": True, "job": state, "result": latest(desk), "gate": gate(desk)}


def run(desk, symbols: list[str], params: dict[str, Any], cost_bps_today,
        fetch: Callable[[str], Any] | None = None) -> dict[str, Any]:
    """`cost_bps_today` may be a callable, read after the download so a just-started
    desk has had time to verify the account."""
    import backtest
    fetch = fetch or backtest._fetch
    with _lock:
        _state.update(running=True, progress=0, total=len(symbols), message="Downloading 2 years of hourly prices…")
    try:
        trades, used = [], []
        with ThreadPoolExecutor(max_workers=6) as pool:
            for i, (sym, df) in enumerate(zip(symbols, pool.map(fetch, symbols))):
                with _lock:
                    _state.update(progress=i + 1, message=f"Replaying {sym} ({i + 1} of {len(symbols)})…")
                if df is None or len(df) < 400:
                    continue
                for t in backtest.simulate_ticker(df, {**params, "slip_bps": 0.0, "fee_bps": 0.0}):
                    t["ticker"] = sym
                    trades.append(t)
                used.append(sym)
        if callable(cost_bps_today):
            cost_bps_today = cost_bps_today()
        days = sorted({t["day"] for t in trades})
        result = {"finished_at": datetime.now(timezone.utc).isoformat(), "tickers": used,
                  "skipped": [s for s in symbols if s not in used],
                  "period": {"start": days[0] if days else None, "end": days[-1] if days else None},
                  "params": params, "cost_bps_today": cost_bps_today,
                  "summary": summarize(trades, cost_bps_today)}
        path = _path(desk)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(result, indent=1), encoding="utf-8")
        tmp.replace(path)
        return result
    finally:
        with _lock:
            _state.update(running=False, message="Done")


def start(desk, symbols, params, cost_bps_today) -> tuple[bool, str]:
    with _lock:
        if _state.get("running"):
            return False, "A history test is already running."
        _state.update(running=True, progress=0, total=len(symbols), message="Starting…", started=time.time())

    def worker():
        try:
            run(desk, symbols, params, cost_bps_today)
        except Exception as exc:  # noqa: BLE001
            with _lock:
                _state.update(running=False, message=f"Failed: {str(exc)[:160]}")

    threading.Thread(target=worker, name="rule-history", daemon=True).start()
    return True, "started"


def register(app, desk, agent) -> None:
    from flask import jsonify

    @app.get("/api/live-agent/history-test")
    def rule_history_status():
        return jsonify(status(desk))

    @app.post("/api/live-agent/history-test")
    def rule_history_start():
        cfg = desk.load_config()
        policy = agent.policy_for(cfg)
        import live_agent
        symbols = live_agent.research_universe(cfg, policy, include_unavailable=True)
        if not symbols:
            return jsonify({"ok": False, "error": "Fox has no stock list to test"}), 400
        preset = desk.get_preset(cfg.get("risk_preset"))
        params = {"stop_pct": 0.8 * float(preset.get("stop_r") or 1.0), "target_r": float(preset.get("target_r") or 2.5)}
        def cost_today():
            cost = agent.round_trip_cost_bps(desk.load_config())
            return round(cost + 2 * SLIP_BPS, 1) if math.isfinite(cost) else None
        started, message = start(desk, symbols, params, cost_today)
        if not started:
            return jsonify({"ok": False, "error": message}), 409
        desk.append_journal("rule_history_started", {"symbols": len(symbols), "params": params})
        return jsonify(status(desk))
