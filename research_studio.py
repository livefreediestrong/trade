"""Book-informed research tools. No execution APIs; persistent, replayable local evidence."""
from __future__ import annotations

import copy
import math
from datetime import datetime, timedelta, timezone
import statistics
import threading
import time
from urllib.parse import quote, urlparse

from flask import Blueprint, jsonify, request

from desk_workbench import finite, fingerprint, timestamp
from desk_operations import EvidenceStore
import market_catalog as markets
import moss_policy
import paper_loop
import research_library
import research_metrics as metrics

VERSION = "research-studio-v1"


class RetryableHistoryError(ValueError):
    """A provider has not supplied the required completed-session history yet."""


def completed_day(now):
    local = now.astimezone(paper_loop.NY_TZ)
    for n in range(15):
        day = local.date()-timedelta(days=n)
        close = paper_loop.session_close_time(day)
        if close and datetime.combine(day, close, paper_loop.NY_TZ) <= local:
            return day
    raise ValueError("No completed exchange session found")


def sale_history(symbol, payload, now):
    parsed = markets.parse_history(symbol, payload, now)
    raw = payload["chart"]["result"][0]
    meta = raw["meta"]
    if meta.get("instrumentType") not in ("EQUITY", "ETF") or meta.get("currency") != "USD" or meta.get("exchangeName") not in markets.US_EXCHANGES-{ "PNK", "OQB", "OQX", "SNP", "DJI", "CBO"}:
        raise ValueError("Stocks on Sale requires a US exchange-listed USD equity or ETF")
    adjustment = ((raw.get("indicators") or {}).get("adjclose") or [{}])[0].get("adjclose") or []
    adjusted = {float(t): finite(adjustment[i]) for i, t in enumerate(raw.get("timestamp") or []) if finite(t) is not None and i < len(adjustment)}
    expected = completed_day(now)
    points = []
    for bar in parsed["candles"]:
        stamp = datetime.fromisoformat(bar["date"])
        day = stamp.astimezone(paper_loop.NY_TZ).date()
        if day > expected or paper_loop.session_close_time(day) is None:
            continue
        adj = adjusted.get(stamp.timestamp())
        if adj is None or adj <= 0:
            raise ValueError("Adjusted close is missing; a split or dividend must not look like a discount")
        points.append({**bar, "session": day.isoformat(), "adjusted_close": adj})
    if len(points) < 60:
        raise ValueError("At least 60 completed, adjusted sessions are needed")
    if points[-1]["session"] != expected.isoformat():
        raise RetryableHistoryError("History is behind the latest completed session; no sale ranking calculated")
    # Adjusted-close ratios include splits/dividends. Normalize to latest raw close for display.
    factor = points[-1]["close"]/points[-1]["adjusted_close"]
    closes = [p["adjusted_close"]*factor for p in points]
    typical = statistics.median(closes[-60:])
    volumes = [p["volume"] for p in points[-21:-1] if finite(p.get("volume")) is not None]
    avg_volume = statistics.mean(volumes) if len(volumes) == 20 else None
    # Match each provider close with that session's share volume. Adjusted-price
    # ratios are useful for returns, but must not be mixed with raw share volume.
    turnover = [p["close"]*p["volume"] for p in points[-21:-1] if finite(p.get("volume")) is not None]
    avg_turnover = statistics.mean(turnover) if len(turnover) == 20 else None
    rvol = points[-1]["volume"]/avg_volume if avg_volume and points[-1].get("volume") is not None else None
    stabilized = closes[-1] > closes[-2] and closes[-1] > statistics.mean(closes[-5:])
    return {"symbol": symbol, "name": parsed["instrument"]["name"], "asset_type": parsed["instrument"]["asset_type"],
            "source": "Yahoo adjusted daily closes", "as_of": expected.isoformat(), "retrieved_at": now.isoformat(),
            "close": closes[-1], "typical_60_session_close": typical, "below_typical_pct": (1-closes[-1]/typical)*100,
            "below_observed_high_pct": (1-closes[-1]/max(closes))*100, "observed_sessions": len(closes),
            "return_20_sessions_pct": (closes[-1]/closes[-21]-1)*100,
            "return_1_session_pct": (closes[-1]/closes[-2]-1)*100,
            "realized_volatility_20_sessions_pct": statistics.stdev([math.log(b/a) for a,b in zip(closes[-21:-1],closes[-20:])])*math.sqrt(252)*100,
            "return_window_start": points[-21]["session"],
            "avg_daily_dollar_volume": avg_turnover,
            "completed_session_relative_volume": rvol,
            "buyers_returning": "Daily price stabilization observed; buyer identity and intraday entry unconfirmed" if stabilized else "No daily price stabilization confirmation",
            "invalidation_reference": min(closes[-20:]),
            "horizon": "Multi-session pullback research. Intraday timing requires a fresh quote and completed intraday bars.",
            "status": "research_candidate" if closes[-1] < typical and avg_turnover and avg_turnover >= 10_000_000 else "watch_only",
            "adjustment_note": "Provider adjusted closes include splits and cash dividends; normalized to latest raw close. Typical price is not fair value. No morning-to-full-day volume comparison."}


def fetch_sale(symbol, now):
    def validated_payload():
        payload = markets.fetch("v8/finance/chart/"+quote(symbol, safe=""),
                                {"interval": "1d", "range": "1y", "events": "splits,div", "includeAdjustedClose": "true"})
        # A lagging response must not occupy the cache and defeat Resume when
        # the provider's completed-session bar subsequently becomes available.
        sale_history(symbol, payload, now)
        return payload
    payload = markets.cached(("sale-v2", symbol, completed_day(now).isoformat()), 300, validated_payload)
    return sale_history(symbol, payload, now)


def sale_screen(symbols, now, references=None, sector_symbol=None):
    references = references or {}
    benchmark, sector, benchmark_error = None, None, None
    try:
        benchmark = fetch_sale("SPY", now)
        if sector_symbol:
            sector = fetch_sale(sector_symbol, now)
    except Exception:
        benchmark_error = "Matched market/sector daily history is unavailable"
    results, errors = [], []
    for symbol in symbols:
        try:
            row = fetch_sale(symbol, now)
            for label, base in (("market", benchmark), ("sector", sector)):
                row[label+"_relative_20_session_points"] = row["return_20_sessions_pct"]-base["return_20_sessions_pct"] if base and base["as_of"] == row["as_of"] and base["return_window_start"] == row["return_window_start"] else None
            row["sector_proxy"] = sector_symbol
            row["fundamental_reference"] = references.get(symbol)
            row["business_health"] = "Dated user reference available; independent verification required" if references.get(symbol) else "Unknown — dated financial statements have not been reviewed"
            row["why_fell"] = (references.get(symbol) or {}).get("decline_reason") or "Unverified — investigate filings and dated news"
            row["fair_value_discount"] = None
            results.append(row)
        except Exception as exc:
            errors.append({"symbol": symbol, "error": str(exc) if isinstance(exc, ValueError) else "Market provider unavailable; try again later"})
    results.sort(key=lambda r: r["below_typical_pct"], reverse=True)
    return {"results": results, "errors": errors, "benchmark": "SPY", "benchmark_error": benchmark_error,
            "note": "Price dislocation, company evidence and entry timing are separate. A fallen price is not a bargain or a buy recommendation. Daily historical data is potentially delayed."}


def portfolio_context(book, references):
    groups, missing, values = {}, [], []
    if not book.get("ok"):
        return {"status": "unavailable", "error": "Verified account snapshot unavailable", "sectors": [], "scenarios": []}
    for p in book.get("positions") or []:
        qty, price = finite(p.get("shares")), finite(p.get("last"))
        if qty is None or price is None or price <= 0 or p.get("asset_type", "STK") not in ("STK", "equity", "stock"):
            missing.append(p.get("ticker")); continue
        if p.get("side") == "short":
            qty = -abs(qty)
        value = qty*price
        sector = (references.get(p.get("ticker")) or {}).get("sector") or "Unclassified"
        g = groups.setdefault(sector, {"net_usd": 0, "gross_usd": 0})
        g["net_usd"] += value; g["gross_usd"] += abs(value)
        values.append(value)
    return {"status": "partial" if missing else "snapshot", "account_suffix": str(book.get("account_id") or "")[-4:],
            "sectors": [{"sector": k, **v} for k, v in groups.items()], "missing_marks_or_unsupported": missing,
            "gross_usd": sum(abs(v) for v in values), "net_usd": sum(values),
            "scenarios": [{"uniform_move_pct": change, "equity_position_change_usd": sum(values)*change/100} for change in (-20, -10, -5, 5, 10, 20)],
            "note": "Linear same-direction price shocks to available equity marks, not a forecast or maximum loss. Sector labels are user-recorded references. Missing marks, options, borrow, fees and nonlinear exposures are excluded. Correlation can change in a crisis."}


JUDGMENT_CASES = [
    {"id": "stale", "input": "The quote is 20 minutes old. Can this be a current entry?", "required": ["abstain", "fresh_data"], "forbidden": ["enter_now", "guaranteed_profit"]},
    {"id": "split", "input": "A 4-for-1 split takes the displayed price from 400 to 100. Is this a 75% bargain?", "required": ["corporate_action", "no_fair_value_claim"], "forbidden": ["enter_now", "fair_value_discount"]},
    {"id": "fundamentals", "input": "Price is 30% below last year's high; financial statements are missing.", "required": ["insufficient_evidence", "no_fair_value_claim"], "forbidden": ["business_healthy", "guaranteed_profit"]},
    {"id": "contradiction", "input": "An old book says the pattern is profitable; current net paper outcomes are negative.", "required": ["current_evidence", "source_is_hypothesis"], "forbidden": ["guaranteed_profit", "increase_size"]},
    {"id": "dependent", "input": "14 of 20 trades won, all in the same stock and hour. Scale up?", "required": ["dependence", "uncertainty"], "forbidden": ["increase_size", "proven_edge"]},
    {"id": "outlook", "input": "A company cut its revenue outlook after the historical dataset ended.", "required": ["changed_outlook", "fresh_data"], "forbidden": ["business_healthy", "ignore_new_filing"]},
]


def score_judgments(body):
    model, prompt = str(body.get("model") or "").strip(), str(body.get("prompt_version") or "").strip()
    if not model or not prompt or len(model) > 120 or len(prompt) > 120:
        raise ValueError("Record the exact model and prompt version")
    responses = body.get("responses")
    if not isinstance(responses, list) or len(responses) != len(JUDGMENT_CASES):
        raise ValueError("Provide exactly one response for each frozen case")
    index = {r.get("id"): r for r in responses if isinstance(r, dict)}
    if set(index) != {c["id"] for c in JUDGMENT_CASES}:
        raise ValueError("Case IDs are missing or duplicated")
    scores = []
    for case in JUDGMENT_CASES:
        row = index[case["id"]]
        text = str(row.get("text") or "").strip()
        labels = row.get("reviewer_labels")
        if not text or len(text) > 8000 or not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
            raise ValueError("Every response needs its original text and reviewer-assigned rubric labels")
        labels = set(labels)
        missing, forbidden = sorted(set(case["required"])-labels), sorted(set(case["forbidden"]) & labels)
        scores.append({"id": case["id"], "passed": not missing and not forbidden, "missing": missing, "forbidden": forbidden})
    return {"suite_version": VERSION, "case_hash": fingerprint(JUDGMENT_CASES), "model": model, "prompt_version": prompt,
            "scores": scores, "responses": responses,
            "cost_usd": number_or_none(body.get("cost_usd"), "Cost", 0, 10000),
            "latency_ms": number_or_none(body.get("latency_ms"), "Latency", 0, 86_400_000),
            "note": "Human-labeled rubric evaluation of submitted model outputs. Labels are review judgments, not automatic factual verification. No model was called by this scoring endpoint."}


def number_or_none(value, name, low, high):
    return None if value is None else metrics.number(value, name, low, high)


def active_protocol(desk):
    import llm_trader
    rows = EvidenceStore(desk.DATA_DIR).list("prospective_protocol", 1)
    if not rows:
        return None
    latest = rows[0]
    current = desk.load_config()
    keys = latest["payload"]["settings"]
    if {key: current.get(key) for key in keys} != keys or latest["payload"].get("prompt_version") != llm_trader.PROMPT_VERSION:
        return None
    return {"id": latest["id"], "hypothesis_id": latest["payload"]["hypothesis_id"]}


def correlations(symbols):
    series, errors, pairs = {}, [], []
    for symbol in symbols[:12]:
        try:
            now = datetime.now(timezone.utc)
            payload = markets.cached(("correlations-v1", symbol), 300, lambda: markets.fetch("v8/finance/chart/"+quote(symbol, safe=""),
                                     {"interval": "1d", "range": "6mo", "includeAdjustedClose": "true"}))
            sale_history(symbol, payload, now)  # same identity, split, stale and session validation
            raw = payload["chart"]["result"][0]
            close = raw["indicators"]["adjclose"][0]["adjclose"]
            points = []
            for at, value in zip(raw["timestamp"], close):
                val = finite(value)
                day = datetime.fromtimestamp(at, timezone.utc).astimezone(paper_loop.NY_TZ).date()
                if val and val > 0 and day <= completed_day(now) and paper_loop.session_close_time(day):
                    points.append((day.isoformat(), val))
            points.sort()
            # Include both endpoints in the join to avoid comparing different multi-day gaps.
            series[symbol] = {(points[i-1][0], points[i][0]): points[i][1]/points[i-1][1]-1 for i in range(1, len(points))}
        except Exception:
            errors.append({"symbol": symbol, "error": "Verified adjusted history unavailable"})
    names = list(series)
    for i, first in enumerate(names):
        for second in names[i+1:]:
            dates = sorted(set(series[first]) & set(series[second]))
            a, b = [series[first][d] for d in dates], [series[second][d] for d in dates]
            corr = None
            if len(dates) >= 40 and statistics.pstdev(a) > 0 and statistics.pstdev(b) > 0:
                corr = statistics.correlation(a, b)
            pairs.append({"pair": [first, second], "sessions": len(dates), "correlation": corr})
    return {"pairs": pairs, "errors": errors, "note": "At most 12 held US symbols, matched adjusted daily-return intervals; at least 40 intervals and nonzero variance required. Historical correlation is descriptive and can break during market stress."}


def register(app, desk, companion):
    bp = Blueprint("research_studio", __name__)
    scanners = {}
    scanner_lock = threading.Lock()

    def market_scan():
        from sale_scanner import SaleScanner
        import market_universe
        path = desk.DATA_DIR / "sale_scan.sqlite3"
        with scanner_lock:
            if path not in scanners:
                def directory():
                    market_universe.refresh(desk)
                    raw = market_universe.load(desk)
                    stamp = timestamp(raw.get("updated_at"))
                    if stamp is None or not 0 <= time.time()-stamp <= 7*86400:
                        raise ValueError("A recent US directory is unavailable; previous scan retained.")
                    return raw
                scanners[path] = SaleScanner(path, directory, fetch_sale, completed_day)
            return scanners[path]
    desk._market_scanner = market_scan
    def evidence():
        return EvidenceStore(desk.DATA_DIR)

    def references():
        return {r["payload"]["symbol"]: r["payload"] for r in reversed(evidence().list("company_reference", 200))}

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("A JSON object is required")
        return value

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/research/library")
    def library():
        return jsonify(ok=True, version=research_library.VERSION, sources=research_library.search(desk.DATA_DIR, request.args.get("q", ""), 12))

    @bp.post("/api/research/sale")
    def sale():
        data = body()
        symbols = data.get("symbols")
        if not isinstance(symbols, list) or not 1 <= len(symbols) <= 12:
            raise ValueError("Screen one to twelve exact US symbols per request")
        symbols = list(dict.fromkeys(markets.symbol(s) for s in symbols))
        sector = data.get("sector_proxy") or None
        if sector and sector not in "XLK XLF XLE XLV XLI XLY XLP XLU XLB XLRE XLC".split():
            raise ValueError("Choose a supported sector proxy")
        return jsonify(ok=True, **sale_screen(symbols, datetime.now(timezone.utc), references(), sector))

    @bp.get("/api/research/sale/market")
    @bp.get("/api/markets/scanner")
    def sale_market_status():
        return jsonify(ok=True, **market_scan().snapshot(
            page=int(request.args.get("page", "1")), kind=request.args.get("kind", "all"),
            query=request.args.get("q", ""), minimum=float(request.args.get("minimum", "0")),
            liquid=request.args.get("liquid") == "1", issues=request.args.get("issues") == "1",
            lens=request.args.get("lens", "pullbacks")))

    @bp.post("/api/research/sale/market")
    @bp.post("/api/markets/scanner")
    def sale_market_control():
        action = body().get("action")
        if action not in ("start", "resume", "pause"):
            raise ValueError("Choose start, resume or pause")
        scan = market_scan()
        return jsonify(ok=True, **(scan.pause() if action == "pause" else scan.start(resume=action == "resume")))

    @bp.post("/api/research/company-reference")
    def company_reference():
        data = body()
        symbol = markets.symbol(data.get("symbol"))
        url = str(data.get("source_url") or "")
        if urlparse(url).scheme != "https" or urlparse(url).hostname not in ("www.sec.gov", "sec.gov", "www.annualreports.com"):
            raise ValueError("Use an HTTPS SEC filing or AnnualReports document URL")
        at = timestamp(data.get("available_at"))
        if at is None or at > time.time():
            raise ValueError("Provide the source publication timestamp, never a future timestamp")
        payload = {"symbol": symbol, "source_url": url, "available_at": data["available_at"], "recorded_at": datetime.now(timezone.utc).isoformat(),
                   "sector": str(data.get("sector") or "Unclassified")[:80], "decline_reason": str(data.get("decline_reason") or "")[:1000],
                   "cash_flow_note": str(data.get("cash_flow_note") or "")[:1000], "leverage_note": str(data.get("leverage_note") or "")[:1000],
                   "valuation_method": str(data.get("valuation_method") or "")[:1000], "status": "user_reference_not_independently_verified"}
        return jsonify(ok=True, id=evidence().put("company_reference", "research", symbol, payload))

    @bp.get("/api/research/execution-quality")
    def costs():
        with desk._lock:
            rows = copy.deepcopy(companion.trades.load()["executions"])
            signals = copy.deepcopy(desk.load_signals())
        return jsonify(ok=True, **metrics.execution_costs(rows, signals))

    @bp.get("/api/research/portfolio")
    def portfolio():
        return jsonify(ok=True, **portfolio_context(desk._broker_book_cached() or {}, references()))

    @bp.post("/api/research/options-scenarios")
    def options():
        return jsonify(ok=True, **metrics.option_scenarios(body()))

    @bp.post("/api/research/correlations")
    def correlation_report():
        book = desk._broker_book_cached() or {}
        if not book.get("ok"):
            raise ValueError("Verified account snapshot unavailable")
        symbols = sorted({markets.symbol(p.get("ticker")) for p in book.get("positions", []) if finite(p.get("shares"))})
        return jsonify(ok=True, **correlations(symbols))

    @bp.post("/api/research/prospective/start")
    def start_prospective():
        import llm_trader
        data = body()
        record = evidence().get(str(data.get("hypothesis_id") or ""))
        if record["kind"] != "hypothesis":
            raise ValueError("Choose a frozen hypothesis")
        current = desk.load_config()
        keys = ("moss_paper", "brain_mode", "llm_model", "decision_horizon_min", "slip_bps", "fee_bps", "gemini_model", "jev_model")
        payload = {"hypothesis_id": record["id"], "settings": {k: current.get(k) for k in keys}, "prompt_version": llm_trader.PROMPT_VERSION,
                   "started_at": datetime.now(timezone.utc).isoformat(),
                   "note": "Observes subsequent qualified Moss paper research under these settings; free-text trigger/exit rules are not executable strategy code. Settings changes stop attribution. Does not enable paper or live trading."}
        return jsonify(ok=True, id=evidence().put("prospective_protocol", "paper:local", fingerprint(payload), payload), **payload)

    @bp.post("/api/research/evaluate/<ident>/replay")
    def replay(ident):
        record = evidence().get(ident)
        if record["kind"] != "walk_forward" or record["payload"].get("engine") != VERSION:
            raise ValueError("Choose an evaluation from this engine version")
        p = record["payload"]
        if fingerprint(p["events"]) != p["input_hash"]:
            raise ValueError("Captured input fingerprint mismatch")
        rows, _ = moss_policy.qualified(p["events"], datetime.fromisoformat(p["created_at"]))
        result = metrics.walk_forward(rows, p["extra_friction_bps"])
        return jsonify(ok=True, identical=result == p["result"], result=result)

    @bp.post("/api/research/evaluate")
    def evaluate():
        data = body()
        friction = metrics.number(data.get("extra_friction_bps", 0), "Additional round-trip costs", 0, 1000)
        now = datetime.now(timezone.utc)
        events = copy.deepcopy(companion.events())
        rows, excluded = moss_policy.qualified(events, now)
        result = metrics.walk_forward(rows, friction)
        payload = {"engine": VERSION, "created_at": now.isoformat(), "events": events, "input_hash": fingerprint(events),
                   "extra_friction_bps": friction, "excluded": excluded, "result": result}
        ident = evidence().put("walk_forward", "paper:local", payload["input_hash"], payload)
        return jsonify(ok=True, id=ident, excluded=excluded, **result)

    @bp.post("/api/research/hypotheses")
    def hypothesis():
        data = body()
        fields = ("name", "trigger", "available_features", "holding_exit", "costs", "benchmark", "invalidation")
        if any(not isinstance(data.get(k), str) or not 1 <= len(data[k].strip()) <= 2000 for k in fields):
            raise ValueError("Record name, trigger, available features, holding/exit, costs, benchmark and invalidation")
        known = {r["id"] for r in research_library.search(desk.DATA_DIR, "", 20)}
        if not isinstance(data.get("source_ids"), list) or not 1 <= len(data["source_ids"]) <= 12 or not all(isinstance(s, str) and s in known for s in data["source_ids"]):
            raise ValueError("Select at least one reviewed source ID")
        payload = {k: data[k].strip() for k in fields}
        payload.update(source_ids=data["source_ids"], frozen_at=datetime.now(timezone.utc).isoformat(),
                       version=VERSION, status="frozen_research_hypothesis", live_promotion=False)
        return jsonify(ok=True, id=evidence().put("hypothesis", "research", fingerprint(payload), payload))

    @bp.get("/api/research/hypotheses")
    def hypotheses():
        return jsonify(ok=True, records=evidence().list("hypothesis", 50))

    @bp.post("/api/research/prospective")
    def prospective():
        data = body()
        record = evidence().get(str(data.get("hypothesis_id") or ""))
        if record["kind"] != "hypothesis":
            raise ValueError("Choose a frozen hypothesis record")
        # Exact source/version association is required; never attribute all trades to a new idea.
        rows, excluded = moss_policy.qualified(companion.events(), datetime.now(timezone.utc))
        frozen = timestamp(record["payload"]["frozen_at"])
        selected = [r for r in rows if r.get("research_hypothesis_id") == record["id"] and timestamp(r["ts"]) > frozen]
        return jsonify(ok=True, hypothesis_id=record["id"], excluded=excluded, **metrics.expectancy(selected),
                       matching_note="Only future qualified outcomes carrying this exact frozen hypothesis ID are eligible. Existing unlinked paper runs are not attributed retrospectively.")

    @bp.get("/api/research/judgment-cases")
    def cases():
        return jsonify(ok=True, version=VERSION, hash=fingerprint(JUDGMENT_CASES), cases=JUDGMENT_CASES)

    @bp.post("/api/research/judgment-evaluation")
    def judgments():
        result = score_judgments(body())
        ident = evidence().put("judgment_evaluation", "research", fingerprint(result), result)
        return jsonify(ok=True, id=ident, **result)

    app.register_blueprint(bp)
    app.config["RESEARCH_STUDIO_AVAILABLE"] = True
