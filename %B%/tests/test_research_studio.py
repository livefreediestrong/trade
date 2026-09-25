"""Exercise research data quality and math without network or broker writes."""
import copy
from datetime import datetime, timedelta, timezone
import json
import socket

import pytest
import app as desk
import research_library as library
import research_metrics as metrics
import research_studio as studio
import market_catalog as markets
import paper_loop

NOW = datetime(2026, 9, 23, 22, tzinfo=timezone.utc)
BASE = "http://127.0.0.1:5056"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL", "DECISIONS", "LESSONS"):
        monkeypatch.setattr(desk, name+"_PATH", tmp_path/(name.lower()+".json"))
    monkeypatch.setattr(desk, "_CORRUPT_PATHS", set())
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *a, **k: pytest.fail("Broker write forbidden"))
    monkeypatch.setattr(desk, "_broker_book_cached", lambda: {"ok": True, "account_id": "TEST", "positions": []})
    monkeypatch.setattr(desk._research_companion, "events", lambda: [])
    return desk.app.test_client()


def history(symbol="AAPL"):
    days = []
    day = NOW.date()-timedelta(days=180)
    while day <= NOW.date():
        if paper_loop.session_close_time(day):
            days.append(datetime.combine(day, paper_loop.RTH_OPEN, paper_loop.NY_TZ).timestamp())
        day += timedelta(days=1)
    n = len(days)
    close = [100.]*n
    return {"chart": {"result": [{"meta": {"symbol": symbol, "instrumentType": "EQUITY", "currency": "USD", "exchangeName": "NMS", "exchangeTimezoneName": "America/New_York", "regularMarketTime": days[-1], "regularMarketPrice": 100}, "timestamp": days,
            "indicators": {"quote": [{"open": close[:], "high": [101.]*n, "low": [99.]*n, "close": close[:], "volume": [1e6]*n}], "adjclose": [{"adjclose": close[:]}]}}]}}


def test_source_retrieval_deduplicated_and_limits_survive(tmp_path):
    first = library.search(tmp_path, "implementation shortfall")
    assert first[0]["author"] == "Joel Hasbrouck" and first[0]["year"] == 2007
    assert len(first[0]["content_sha256"]) == 64
    assert len(library.search(tmp_path, "", 20)) == 12
    assert len(library.search(tmp_path, "", 20)) == 12
    assert library.search(tmp_path, '" OR * : ') == []
    assert "not_trading_evidence" in library.context(tmp_path, "memory")["kind"]


def test_split_never_looks_like_a_discount_and_today_after_close_is_complete():
    raw = history()
    q = raw["chart"]["result"][0]["indicators"]
    # Older raw bars 4x but provided adjusted closes have already accounted for the split.
    for key in ("open", "high", "low", "close"):
        q["quote"][0][key][:-1] = [x*4 for x in q["quote"][0][key][:-1]]
    result = studio.sale_history("AAPL", raw, NOW)
    assert result["below_typical_pct"] == 0
    assert result["below_observed_high_pct"] == 0
    assert result["as_of"] == "2026-09-23"
    assert result["status"] == "watch_only"


@pytest.mark.parametrize("change", ["future", "stale", "missing_adjusted", "wrong_identity", "foreign", "bad_bar"])
def test_bad_history_cannot_rank_a_sale(change):
    raw = history();r = raw["chart"]["result"][0]
    if change == "future": r["timestamp"][-1] = NOW.timestamp()+3600
    elif change == "stale": r["timestamp"] = r["timestamp"][:-1]
    elif change == "missing_adjusted": r["indicators"]["adjclose"][0]["adjclose"][-1] = None
    elif change == "wrong_identity": r["meta"]["symbol"] = "WRONG"
    elif change == "foreign": r["meta"]["exchangeName"] = "LSE"
    elif change == "bad_bar": r["indicators"]["quote"][0]["high"][-1] = 1
    with pytest.raises(ValueError): studio.sale_history("AAPL", raw, NOW)


def test_sale_high_volume_selloff_does_not_mean_buyers(monkeypatch):
    raw = history();r = raw["chart"]["result"][0]
    for k in ("open", "close", "low"): r["indicators"]["quote"][0][k][-1] = 80
    r["indicators"]["adjclose"][0]["adjclose"][-1] = 80
    r["indicators"]["quote"][0]["volume"][-1] = 5e6
    result = studio.sale_history("AAPL", raw, NOW)
    assert result["below_typical_pct"] == pytest.approx(20)
    assert result["buyers_returning"].startswith("No")
    monkeypatch.setattr(studio, "fetch_sale", lambda sym, now: dict(result, symbol=sym))
    result = studio.sale_screen(["AAPL"], NOW)["results"][0]
    assert result["fair_value_discount"] is None and result["business_health"].startswith("Unknown")


def test_liquidity_averages_matched_daily_turnover():
    raw = history()
    indicators = raw['chart']['result'][0]['indicators']
    quote = indicators['quote'][0]
    prices = [20.]*10 + [10.]*10
    for key in ('open', 'high', 'low', 'close'):
        quote[key][-21:-1] = prices
    quote['volume'][-21:-1] = [200_000]*10 + [1_200_000]*10
    indicators['adjclose'][0]['adjclose'][-21:-1] = prices
    result = studio.sale_history('AAPL', raw, NOW)
    assert result['avg_daily_dollar_volume'] == 8_000_000
    assert result['status'] == 'watch_only'


def test_lagging_history_is_not_cached_across_retry(monkeypatch):
    stale = history()
    stale['chart']['result'][0]['timestamp'].pop()
    responses = [stale, history()]
    calls = []
    monkeypatch.setattr(markets, '_CACHE', {})
    monkeypatch.setattr(markets, 'fetch', lambda *a, **k: calls.append(1) or responses.pop(0))
    with pytest.raises(studio.RetryableHistoryError):
        studio.fetch_sale('AAPL', NOW)
    assert studio.fetch_sale('AAPL', NOW)['as_of'] == '2026-09-23'
    assert len(calls) == 2
    studio.fetch_sale('AAPL', NOW)
    assert len(calls) == 2


def simple_rows(n=120):
    rows = []
    for i in range(n):
        at = NOW-timedelta(days=n-i, hours=3)
        rows.append({"id": str(i), "ts": at.isoformat(), "outcome_ts": (at+timedelta(minutes=10)).isoformat(),
                     "outcome_executable_move_bps": 30 if i%3 else -40, "confidence": .7 if i%2 else .9})
    return rows


def test_cluster_uncertainty_requires_days_not_just_trades():
    rows = simple_rows(20)
    for r in rows: r["ts"] = NOW.isoformat()
    result = metrics.expectancy(rows)
    assert result["outcomes"] == 20 and result["day_cluster_95_interval_bps"] is None
    assert metrics.expectancy(simple_rows()) == metrics.expectancy(simple_rows())


def test_walk_forward_purges_overlap_and_keeps_variants():
    rows = simple_rows()
    rows[39]["outcome_ts"] = rows[41]["ts"]
    result = metrics.walk_forward(rows, 50)
    assert len(result["windows"]) == 4
    assert all(len(w["trials"]) == 4 for w in result["windows"])
    assert "39" not in result["windows"][0]["train_ids"]
    assert result["overall"]["mean_net_bps"] < 0
    assert result == metrics.walk_forward(copy.deepcopy(rows), 50)


def test_walk_forward_test_outcomes_do_not_choose_threshold():
    rows = simple_rows();baseline = metrics.walk_forward(rows)
    for row in rows[100:]: row["outcome_executable_move_bps"] = 1e6
    changed = metrics.walk_forward(rows)
    assert baseline["windows"][-1]["chosen_threshold"] == changed["windows"][-1]["chosen_threshold"]


def test_execution_signed_cost_and_workspace_isolation():
    q = {"bid": 99, "ask": 101, "price": 100, "fresh": True, "source": "feed", "market_time": NOW.isoformat()}
    benchmark = metrics.quote_benchmark(q, NOW.isoformat())
    signals = [{"id": "s", "ticker": "AAPL", "review_identity": {"account_id": "A", "paper_mode": False}, "review_order": {"shares": 5}, "execution_benchmarks": {"decision": benchmark, "arrival": benchmark}}]
    row = {"order_ref": "s", "account_id": "A", "paper_mode": False, "verified": True, "ticker": "AAPL", "asset_type": "STK", "ts": (NOW+timedelta(seconds=1)).isoformat(), "price": 101, "shares": 2, "side": "buy", "currency": "USD", "execution_id": "1", "commission": None}
    result = metrics.execution_costs([row, dict(row, paper_mode=True, side="sell", price=99, execution_id="2")], signals)
    assert len(result["groups"]) == 2
    assert result["executions"][0]["arrival_slippage_bps"] == 100
    assert result["executions"][1]["arrival_slippage_bps"] is None  # paper identity does not match live reference
    assert result["linked_fill_rates"][0]["observed_fill_fraction"] == .4
    signals[0]["review_identity"]["paper_mode"] = True
    assert metrics.execution_costs([dict(row, paper_mode=True, side="sell", price=99)], signals)["executions"][0]["arrival_slippage_bps"] == 100
    assert result["groups"][0]["fees_missing_or_other_currency"] == 1


def test_missing_or_stale_quote_is_not_reconstructed():
    q = metrics.quote_benchmark({"price": 100, "market_time": (NOW-timedelta(seconds=31)).isoformat(), "fresh": True, "source": "feed"}, NOW.isoformat())
    assert q["mid"] is None and q["last"] is None
    assert not metrics.quote_benchmark({"bid": 99, "ask": 101, "market_time": NOW.isoformat(), "fresh": False, "source": "feed"}, NOW.isoformat())["fresh_at_capture"]


def test_option_math_parity_zero_time_volatility_and_multi_leg():
    c = metrics.option_value(100, 100, 365, .2, .05, 0, "call")
    p = metrics.option_value(100, 100, 365, .2, .05, 0, "put")
    import math
    assert c == pytest.approx(10.45058, abs=.0001)
    assert c-p == pytest.approx(100-100*math.exp(-.05))
    assert metrics.option_value(110, 100, 0, .2, .05, 0, "call") == 10
    assert metrics.option_value(100, 100, 365, 0, 0, 0, "put") == 0
    result = metrics.option_scenarios({"spot": 100, "days": 30, "iv_pct": 30, "legs": [{"right": "call", "side": "buy", "strike": 100}, {"right": "call", "side": "sell", "strike": 110}]})
    assert 0 < result["baseline_model_value_usd"] < 1000
    assert len(result["scenarios"]) == 45
    assert all(s["change_from_model_usd"] == pytest.approx(0) for s in result["scenarios"] if s["underlying_change_pct"] == s["elapsed_days"] == s["iv_change_points"] == 0)


@pytest.mark.parametrize("field,value", [("spot", 0), ("days", -1), ("iv_pct", float("nan")), ("spot", True)])
def test_scenario_rejects_invalid_inputs(field, value):
    data = {"spot": 100, "days": 30, "iv_pct": 30, "legs": [{"right": "call", "side": "buy", "strike": 100}]}
    data[field] = value
    with pytest.raises(ValueError): metrics.option_scenarios(data)


def test_portfolio_shocks_signed_exposure_missing_marks():
    book = {"ok": True, "positions": [{"ticker": "A", "shares": 10, "last": 100}, {"ticker": "B", "shares": 5, "last": 100, "side": "short"}, {"ticker": "C", "shares": 4, "last": None}]}
    result = studio.portfolio_context(book, {})
    assert result["net_usd"] == 500 and result["gross_usd"] == 1500
    assert result["missing_marks_or_unsupported"] == ["C"] and result["status"] == "partial"
    assert result["scenarios"][0]["equity_position_change_usd"] == -100


def test_api_capture_replay_hypothesis_and_settings_boundary(isolated):
    cfg = dict(desk.load_config(), mode="live_manual");desk.save_config(cfg)
    before = desk.CONFIG_PATH.read_bytes()
    source = isolated.get("/api/research/library?q=spread", base_url=BASE).json["sources"][0]
    data = {k: "Exact test specification" for k in ("name", "trigger", "available_features", "holding_exit", "costs", "benchmark", "invalidation")}
    data["source_ids"] = [source["id"]]
    frozen = isolated.post("/api/research/hypotheses", json=data, base_url=BASE)
    assert frozen.status_code == 200
    ident = frozen.json["id"]
    start = isolated.post("/api/research/prospective/start", json={"hypothesis_id": ident}, base_url=BASE)
    assert start.status_code == 200 and studio.active_protocol(desk)["hypothesis_id"] == ident
    future = isolated.post("/api/research/prospective", json={"hypothesis_id": ident}, base_url=BASE)
    assert future.json["outcomes"] == 0
    result = isolated.post("/api/research/evaluate", json={"extra_friction_bps": 5}, base_url=BASE)
    assert result.status_code == 200
    replay = isolated.post("/api/research/evaluate/"+result.json["id"]+"/replay", json={}, base_url=BASE)
    assert replay.json["identical"]
    assert desk.CONFIG_PATH.read_bytes() == before
    cfg["slip_bps"] = 123;desk.save_config(cfg)
    assert studio.active_protocol(desk) is None


def test_judgment_rubric_tracks_failures_and_does_not_claim_model_calls(isolated):
    cases = isolated.get("/api/research/judgment-cases", base_url=BASE).json["cases"]
    responses = [{"id": c["id"], "text": "Retained model output", "reviewer_labels": c["required"]} for c in cases]
    responses[0]["reviewer_labels"] = ["enter_now"]
    response = isolated.post("/api/research/judgment-evaluation", json={"model": "recorded-model", "prompt_version": "p1", "responses": responses}, base_url=BASE)
    assert response.status_code == 200 and not response.json["scores"][0]["passed"]
    assert response.json["cost_usd"] is None and "No model was called" in response.json["note"]


def test_templates_all_controls_connected_and_no_prototype_numbers(isolated):
    page = isolated.get("/", base_url=BASE).get_data(as_text=True)
    assert "nadzeeɫ" in page and 'id="moss-woman"' in page and 'id="moss-fox"' in page
    assert 'id="research-studio"' in page and 'id="desk-live"' in page
    assert "$128,430" not in page
    assert isolated.get("/static/changing-woman-atlas.png", base_url=BASE).status_code == 200


def test_bad_source_ids_are_rejected_without_server_error(isolated):
    data = {k: "A specification" for k in ("name", "trigger", "available_features", "holding_exit", "costs", "benchmark", "invalidation")}
    data["source_ids"] = [{}]
    assert isolated.post("/api/research/hypotheses", json=data, base_url=BASE).status_code == 400


def test_invalid_confidence_excluded_without_changing_original_rows():
    rows = simple_rows(10)
    rows[0]["confidence"] = None; rows[1]["confidence"] = 8; rows[2]["confidence"] = ".7"
    before = copy.deepcopy(rows)
    result = metrics.walk_forward(rows)
    assert result["excluded_confidence"] == 2 and rows == before


def test_comparison_requires_matched_start_as_well_as_end(monkeypatch):
    def fake(symbol, now):
        return {"symbol": symbol, "as_of": "2026-09-23", "return_window_start": "2026-08-20" if symbol == "SPY" else "2026-08-21", "return_20_sessions_pct": -10, "below_typical_pct": 10}
    monkeypatch.setattr(studio, "fetch_sale", fake)
    row = studio.sale_screen(["AAPL"], NOW)["results"][0]
    assert row["market_relative_20_session_points"] is None


def test_fundamental_reference_never_treats_a_future_date_as_available(isolated):
    response = isolated.post("/api/research/company-reference", json={"symbol": "AAPL", "source_url": "https://www.sec.gov/Archives/example", "available_at": "2099-01-01T00:00:00Z"}, base_url=BASE)
    assert response.status_code == 400


def test_journal_retains_captured_quote_when_signal_is_later_absent(isolated):
    import real_trade_journal
    at = datetime.now(timezone.utc)-timedelta(seconds=1)
    benchmark = metrics.quote_benchmark({"bid": 99, "ask": 101, "price": 100, "fresh": True, "source": "recorded", "market_time": at.isoformat()}, at.isoformat())
    desk.save_signals([{"id": "s", "ticker": "AAPL", "review_identity": {"account_id": "U-TEST", "paper_mode": False}, "execution_benchmarks": {"arrival": benchmark}, "review_order": {"shares": 3, "type": "limit"}}])
    row = {"account_id": "U-TEST", "paper_mode": False, "execution_id": "captured.01", "verified": True, "order_ref": "s", "ticker": "AAPL", "asset_type": "STK", "ts": datetime.now(timezone.utc).isoformat(), "price": 101, "shares": 1, "side": "buy", "currency": "USD"}
    journal = real_trade_journal.TradeJournal(desk)
    journal.merge({"ok": True, "identity": {"account_id": "U-TEST", "paper_mode": False}, "executions": [row]})
    desk.save_signals([])
    journal.merge({"ok": True, "identity": {"account_id": "U-TEST", "paper_mode": False}, "executions": [row]})
    result = metrics.execution_costs(journal.load()["executions"], [])
    assert result["executions"][0]["arrival_slippage_bps"] == 100
    assert result["executions"][0]["order_type"] == "limit"
