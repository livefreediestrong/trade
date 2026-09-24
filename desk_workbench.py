"""Research workspace, journal, and reproducible paper experiments.

Routes reuse the desk's persistence lock and atomic writer. This module cannot
submit broker orders or change trading configuration.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import math
import re
import threading
import time
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request

VERSION = "captured-policy-v1"


def finite(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.timestamp() if stamp.tzinfo else None
    except (ValueError, TypeError, OSError):
        return None


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def horizon_seconds(event):
    minutes = finite(event.get("horizon_min"))
    return finite(event.get("outcome_horizon_sec") or event.get("horizon_sec")) or (minutes * 60 if minutes else None)


def model_label(event):
    return str(event.get("llm_model") or event.get("brain_model") or event.get("brain") or "unknown")


def journal_rows(signals, ledger):
    """One cumulative fill per broker order; orders and fills stay distinct."""
    rows = []
    signal_map = {s.get("id"): s for s in signals}
    for signal in signals:
        response = signal.get("live_response") or {}
        terms = response.get("order") or signal.get("review_order") or {}
        rows.append({"kind": "order", "id": signal.get("id"), "order_id": response.get("order_id"),
                     "workspace": "broker_paper" if response.get("paper_mode") else signal.get("workspace") or ("paper" if signal.get("mode_at_create") in ("manual", "auto_paper") else "live"), "ticker": signal.get("ticker"),
                     "side": signal.get("side"), "status": signal.get("status"),
                     "ts": signal.get("ts") or signal.get("created_at"),
                     "strategy": signal.get("brain_model") or signal.get("brain_mode") or signal.get("source"),
                     "shares": terms.get("shares", signal.get("suggested_shares")),
                     "price": terms.get("limit"), "type": terms.get("type"), "fee_usd": None})
    seen = set()
    for workspace, fills in (("paper", ledger.get("fills") or []), ("live", ledger.get("broker_fills") or [])):
        for fill in fills:
            key = (workspace, fill.get("order_id") or fill.get("id"))
            if key in seen:
                continue
            seen.add(key)
            sig = signal_map.get(fill.get("signal_id")) or {}
            rows.append({"kind": "fill", "id": fill.get("id"), "order_id": fill.get("order_id"),
                         "workspace": "broker_paper" if workspace == "live" and fill.get("paper_mode") else workspace,
                         "ticker": fill.get("ticker"), "side": fill.get("side"),
                         "status": fill.get("broker_fill_state") or "filled", "ts": fill.get("ts"),
                         "strategy": sig.get("brain_model") or sig.get("brain_mode") or sig.get("source"),
                         "shares": fill.get("shares"), "price": fill.get("price"),
                         "fee_usd": fill.get("fee_usd"), "type": "execution"})
    return sorted(rows, key=lambda r: str(r.get("ts") or ""), reverse=True)


def filter_journal(rows, args):
    start, end = timestamp(args.get("from")), timestamp(args.get("to"))
    for key in ("from", "to"):
        if args.get(key) and timestamp(args[key]) is None:
            raise ValueError(f"{key} requires an ISO timestamp with timezone")
    if start is not None and end is not None and start > end:
        raise ValueError("Start must precede end")
    result = []
    for row in rows:
        if any(args.get(k) and str(row.get(k) or "").lower() != str(args[k]).lower()
               for k in ("workspace", "ticker", "status", "kind")):
            continue
        if args.get("strategy") and str(args["strategy"]).lower() not in str(row.get("strategy") or "").lower():
            continue
        stamp = timestamp(row.get("ts"))
        if (start is not None or end is not None) and stamp is None:
            continue
        if start is not None and stamp < start or end is not None and stamp > end:
            continue
        result.append(row)
    return result


def csv_export(rows):
    stream = io.StringIO(newline="")
    fields = ["kind", "id", "order_id", "workspace", "ticker", "side", "status", "ts", "strategy", "shares", "price", "type", "fee_usd"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        # Spreadsheet formula injection applies even to locally generated CSV.
        writer.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else v)
                         for k in fields for v in [row.get(k)]})
    return stream.getvalue()


def calibration(events):
    groups = {}
    for event in events:
        if event.get("event") not in (None, "decision"):
            continue
        key = (model_label(event), str(horizon_seconds(event) or "unspecified"),
               str(event.get("workspace") or "research"), str(event.get("prompt_version") or "unknown"))
        group = groups.setdefault(key, {"model": key[0], "horizon_sec": key[1], "workspace": key[2],
                                       "prompt_version": key[3], "decisions": 0, "actionable": 0, "scored": 0, "wins": 0,
                                       "brier_sum": 0, "confidence_samples": 0, "excluded": 0,
                                       "bins": [{"low": i / 5, "high": (i + 1) / 5, "n": 0, "wins": 0} for i in range(5)]})
        group["decisions"] += 1
        if str(event.get("intended_side") or event.get("decision") or event.get("side")).lower() not in ("buy", "sell"):
            continue
        group["actionable"] += 1
        move = finite(event.get("outcome_executable_move_bps"))
        if move is None or event.get("outcome_status") != "scored" or event.get("mock") or event.get("routed") or event.get("error") or event.get("execution_block"):
            group["excluded"] += 1
            continue
        win = int(move > 0)
        group["scored"] += 1
        group["wins"] += win
        confidence = finite(event.get("confidence"))
        if confidence is not None and 0 <= confidence <= 1:
            group["confidence_samples"] += 1
            group["brier_sum"] += (confidence - win) ** 2
            bucket = group["bins"][min(4, int(confidence * 5))]
            bucket["n"] += 1
            bucket["wins"] += win
    for group in groups.values():
        group["coverage"] = group["scored"] / group["actionable"] if group["actionable"] else None
        group["abstention_rate"] = 1 - group["actionable"] / group["decisions"]
        group["win_rate"] = group["wins"] / group["scored"] if group["scored"] else None
        n = group["confidence_samples"]
        group["brier"] = group.pop("brier_sum") / n if n else None
        group["evidence"] = "insufficient sample" if group["scored"] < 30 else "descriptive; not held-out validation"
    return list(groups.values())


def experiment_parameters(body):
    if not isinstance(body, dict):
        raise ValueError("Experiment must be an object")
    defaults = {"capital": 1000, "position_pct": 10, "spread_bps": 10, "fee_bps": 1,
                "delay_bps": 5, "fill_pct": 100, "horizon_sec": 1200}
    bounds = {"capital": (1, 1e8), "position_pct": (0.01, 100), "spread_bps": (0, 1000),
              "fee_bps": (0, 1000), "delay_bps": (0, 1000), "fill_pct": (0, 100), "horizon_sec": (1, 86400)}
    result = {}
    for key, default in defaults.items():
        value = finite(body.get(key, default))
        lo, hi = bounds[key]
        if value is None or not lo <= value <= hi:
            raise ValueError(f"{key} must be between {lo} and {hi}")
        result[key] = value
    result["name"] = str(body.get("name") or "Paper experiment").strip()[:80]
    result["strategy_version"] = str(body.get("strategy_version") or VERSION).strip()[:100]
    result["model"] = str(body.get("model") or "all")[:100]
    return result


def replay(capture, params):
    """Repeat recorded policy outputs, never rerun today's model on old news.

    Outcome moves must belong to the requested horizon. Stress costs are added
    to raw directional moves, never subtracted twice from pre-netted outcomes.
    Each decision is an independent fixed-capital scenario, not a portfolio.
    """
    trades, skipped, exclusion_reasons = [], 0, {}
    for event in sorted(capture, key=lambda e: (str(e.get("ts") or ""), str(e.get("id") or e.get("seq") or ""))):
        if event.get("event") not in (None, "decision"):
            continue
        model = model_label(event)
        if params["model"] != "all" and model != params["model"]:
            continue
        side = str(event.get("intended_side") or event.get("decision") or event.get("side") or "").lower()
        if side not in ("buy", "sell"):
            if side not in ("hold", "flat", "abstain"):
                skipped += 1
                exclusion_reasons["unsupported_direction"] = exclusion_reasons.get("unsupported_direction", 0)+1
            continue
        horizon = horizon_seconds(event)
        move = finite(event.get("outcome_move_bps"))
        price = finite((event.get("quote") or {}).get("price") or event.get("mid"))
        if (horizon != params["horizon_sec"] or move is None or not price or price <= 0
                or event.get("outcome_status") != "scored" or event.get("mock") or event.get("routed")
                or event.get("error") or event.get("execution_block")):
            skipped += 1
            exclusion_reasons["ineligible_evidence"] = exclusion_reasons.get("ineligible_evidence", 0)+1
            continue
        desired = math.floor(params["capital"] * params["position_pct"] / 100 / price)
        quantity = math.floor(desired * params["fill_pct"] / 100)
        # outcome_move_bps is the instrument's raw movement, direction applied here.
        directional = move if side == "buy" else -move
        cost = params["spread_bps"] + 2 * params["fee_bps"] + params["delay_bps"]
        trades.append({"id": event.get("id") or event.get("seq"), "ticker": event.get("ticker"),
                       "ts": event.get("ts"), "side": side, "model": model, "requested_shares": desired,
                       "filled_shares": quantity, "net_bps": round(directional - cost, 4),
                       "pnl_usd": round(quantity * price * (directional - cost) / 10000, 6)})
    return {"engine_version": VERSION, "trades": trades, "eligible": len(trades), "excluded": skipped,
            "exclusion_reasons": exclusion_reasons,
            "filled_scenarios": sum(t["filled_shares"] > 0 for t in trades),
            "scenario_pnl_sum": round(sum(t["pnl_usd"] for t in trades), 6),
            "scope": "Independent fixed-capital scenarios using recorded policy decisions; not portfolio returns or a model rerun.",
            "assumptions": "Round-trip spread + two fees + adverse delay bps; deterministic partial-fill percentage. No queue model or guaranteed live fills."}


def register(app, desk):
    bp = Blueprint("workbench", __name__)
    cache, cache_lock = {}, threading.Lock()

    def events():
        raw = desk._load_json(desk.DECISIONS_PATH, {})
        return raw.get("events", []) if isinstance(raw, dict) else raw

    def saved_path():
        return desk.DATA_DIR / "workbench.json"

    def load_saved():
        return desk._load_json(saved_path(), {"watchlists": {}, "experiments": []})

    def save_saved(data):
        if str(saved_path().resolve()) in desk._CORRUPT_PATHS:
            raise ValueError("Workbench storage is unreadable; restore it before saving")
        desk._save_json(saved_path(), data)

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/workbench/journal")
    def journal():
        with desk._lock:
            rows = journal_rows(desk.load_signals(), desk.load_ledger())
        rows = filter_journal(rows, request.args)
        if request.args.get("format") == "csv":
            return Response(csv_export(rows), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=desk-journal.csv"})
        return jsonify(ok=True, rows=rows[:1000], total=len(rows),
                       scope="Retained desk orders and cumulative fills. Not a complete broker statement.")

    @bp.get("/api/workbench/evaluation")
    def evaluation():
        with desk._lock:
            rows = copy.deepcopy(events())
        return jsonify(ok=True, groups=calibration(rows), version=VERSION,
                       note="Self-reported confidence is not a calibrated probability. Unknown horizons and missing outcomes stay explicit.")

    @bp.route("/api/workbench/watchlists", methods=["GET", "POST"])
    def watchlists():
        with desk._lock:
            saved = load_saved()
            if request.method == "POST":
                body = request.get_json(silent=True)
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                name = str(body.get("name") or "").strip()
                symbols = desk.paper_loop_mod.equity_loop_symbols(desk.parse_watchlist(body.get("symbols") or ""))
                if not name or len(name) > 60 or not symbols or len(symbols) > 50:
                    raise ValueError("Use a name up to 60 characters and 1–50 equity symbols")
                lists = saved.setdefault("watchlists", {})
                if name not in lists and len(lists) >= 30:
                    raise ValueError("At most 30 saved watchlists")
                lists[name] = symbols
                save_saved(saved)
        return jsonify(ok=True, watchlists=saved["watchlists"])

    @bp.get("/api/workbench/symbol/<symbol>")
    def symbol_context(symbol):
        symbol = symbol.upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Invalid equity symbol")
        with cache_lock:
            item = cache.get(symbol)
            if item and time.monotonic() - item[0] < 60:
                return jsonify(item[1])
        import data_sources
        bars, source = data_sources.get_daily_with_fallback(symbol, days=120)
        candles = []
        if bars is not None:
            for date, row in bars.tail(120).iterrows():
                values = {k.lower(): finite(row.get(k)) for k in ("Open", "High", "Low", "Close", "Volume")}
                if all(values[k] is not None and values[k] > 0 for k in ("open", "high", "low", "close")):
                    candles.append({"date": date.isoformat(), **values})
        news = desk.news_stream.news_for_symbol(symbol, limit=10)
        with desk._lock:
            theses = [s for s in desk.load_signals() if s.get("ticker") == symbol][:8]
        result = {"ok": True, "chart_available": bool(candles), "symbol": symbol, "candles": candles, "source": source,
                  "interval": "1d", "session": "Daily historical bars; latest bar may be incomplete",
                  "retrieved_at": datetime.now(timezone.utc).isoformat(), "news": news,
                  "theses": [{k: s.get(k) for k in ("id", "workspace", "side", "status", "thesis", "llm_thesis", "why_plain", "quote")} for s in theses],
                  "error": None if candles else "Historical bars unavailable; no synthetic chart is shown",
                  "display_only": True}
        with cache_lock:
            if len(cache) >= 50:
                cache.pop(next(iter(cache)))
            cache[symbol] = (time.monotonic(), result)
        return jsonify(result)

    @bp.route("/api/workbench/experiments", methods=["GET", "POST"])
    def experiments():
        with desk._lock:
            saved = load_saved()
            if request.method == "POST":
                params = experiment_parameters(request.get_json(silent=True))
                capture = copy.deepcopy(events())
                config = desk.load_config()
                policy = {k: config.get(k) for k in ("brain_mode", "paper_risk_preset", "slip_bps", "fee_bps", "rth_only", "macro_gates_enabled")}
                item = {"parameters": params, "captured_policy": policy, "capture": capture, "engine_version": VERSION}
                item["id"] = fingerprint(item)
                item["created_at"] = datetime.now(timezone.utc).isoformat()
                item["result"] = replay(capture, params)
                history = saved.setdefault("experiments", [])
                if not any(e["id"] == item["id"] for e in history):
                    if len(history) >= 30:
                        raise ValueError("30 experiment captures retained; export/archive them before adding more")
                    history.insert(0, item)
                    save_saved(saved)
            summaries = [{k: e[k] for k in e if k != "capture"} for e in saved.get("experiments", [])]
        return jsonify(ok=True, experiments=summaries)

    @bp.get("/api/workbench/experiments/<experiment_id>")
    def replay_experiment(experiment_id):
        with desk._lock:
            item = next((e for e in load_saved().get("experiments", []) if e["id"] == experiment_id), None)
        if not item:
            return jsonify(ok=False, error="Experiment not found"), 404
        content = {k: item[k] for k in ("parameters", "captured_policy", "capture", "engine_version")}
        if fingerprint(content) != experiment_id or item["engine_version"] != VERSION:
            raise ValueError("Capture hash or replay version mismatch; refusing a changed experiment")
        result = replay(item["capture"], item["parameters"])
        return jsonify(ok=True, experiment=item, replay=result, identical=result == item["result"])

    app.register_blueprint(bp)
