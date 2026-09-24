"""Exercise provenance, market units, evaluation isolation and source recovery."""
import copy
from datetime import datetime, timedelta, timezone
import json
import socket
import zipfile

import pytest
import app as desk
import data_sources
import desk_operations as ops
import market_catalog as markets
import market_universe
import moss_policy
import paper_loop
import release_tools

NOW = datetime(2026, 9, 23, 20, tzinfo=timezone.utc)
BASE = "http://127.0.0.1:5056"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL", "DECISIONS", "LESSONS"):
        monkeypatch.setattr(desk, name+"_PATH", tmp_path/(name.lower()+".json"))
    monkeypatch.setattr(desk, "_CORRUPT_PATHS", set())
    monkeypatch.setattr(markets, "_CACHE", {})
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *a, **k: pytest.fail("Broker write forbidden"))
    monkeypatch.setattr(desk, "_broker_book_cached", lambda: {"ok": True, "account_id": "TEST", "day_pnl_usd": None, "risk_ready": False})
    return desk.app.test_client()


def payload(symbol="VOD.L", kind="EQUITY", currency="GBp", zone="Europe/London"):
    return {"chart": {"result": [{"meta": {"symbol": symbol, "instrumentType": kind, "currency": currency,
        "exchangeName": "LSE", "exchangeTimezoneName": zone, "regularMarketTime": NOW.timestamp()-30, "regularMarketPrice": 75},
        "timestamp": [(NOW-timedelta(days=2)).timestamp(), (NOW-timedelta(hours=1)).timestamp()],
        "indicators": {"quote": [{"open": [70, 74], "high": [73, 76], "low": [69, 73], "close": [72, 75], "volume": [100, 200]}]}}]}}


@pytest.mark.parametrize("symbol,kind,currency,zone", [("AAPL", "EQUITY", "USD", "America/New_York"),
    ("7203.T", "EQUITY", "JPY", "Asia/Tokyo"), ("BTC-USD", "CRYPTOCURRENCY", "USD", "UTC"),
    ("EURUSD=X", "CURRENCY", "USD", "Europe/London"), ("ES=F", "FUTURE", "USD", "America/New_York"),
    ("VTI", "ETF", "USD", "America/New_York"), ("^GSPC", "INDEX", "USD", "America/New_York"),
    ("VTSAX", "MUTUALFUND", "USD", "America/New_York")])
def test_global_history_keeps_identity_and_native_units(symbol, kind, currency, zone):
    result = markets.parse_history(symbol, payload(symbol, kind, currency, zone), NOW)
    assert result["instrument"]["currency"] == currency
    assert result["instrument"]["id"] == "YF:"+symbol
    assert not result["instrument"]["execution"] and not result["instrument"]["paper_execution"]
    assert result["instrument"]["timezone"] == zone
    assert result["quality"].startswith("Historical")


def test_pence_not_pounds_and_provider_symbols_not_rewritten():
    result = markets.parse_history("VOD.L", payload(), NOW)
    assert result["price"] == 75 and result["quote_unit"] == "GBp (pence, not pounds)"
    assert data_sources.yahoo_symbol("VOD.L") == "VOD.L"
    assert data_sources.yahoo_symbol("BRK.B") == "BRK-B"
    assert paper_loop.equity_loop_symbols(["VOD.L", "SAP.DE", "BTC-USD", "YF:AAPL", "7203.T", "EURUSD=X", "AAPL", "BRK.B"]) == ["AAPL", "BRK.B"]


def test_future_wrong_identity_unknown_units_and_malformed_bars_fail():
    for key, value in [("symbol", "OTHER"), ("currency", None), ("exchangeTimezoneName", "Not/AZone"), ("instrumentType", "BOND")]:
        raw = payload(); raw["chart"]["result"][0]["meta"][key] = value
        with pytest.raises(ValueError): markets.parse_history("VOD.L", raw, NOW)
    raw = payload(); res = raw["chart"]["result"][0]
    res["meta"]["regularMarketTime"] = NOW.timestamp()+1
    res["timestamp"][1] = NOW.timestamp()+60
    parsed = markets.parse_history("VOD.L", raw, NOW)
    assert parsed["price"] is None and parsed["market_time"] is None and parsed["rejected_bars"] == 1
    res["indicators"]["quote"][0]["high"][0] = 1
    with pytest.raises(ValueError): markets.parse_history("VOD.L", raw, NOW)


def test_us_scope_excludes_foreign_and_crypto_results(monkeypatch):
    monkeypatch.setattr(markets, "fetch", lambda *a: {"quotes": [
        {"symbol": "TM", "quoteType": "EQUITY", "exchange": "NYQ"},
        {"symbol": "7203.T", "quoteType": "EQUITY", "exchange": "JPX"},
        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY", "exchange": "CCC"}]})
    assert [r["symbol"] for r in markets.search("Toyota", [])["results"]] == ["TM"]
    assert len(markets.search("Toyota", [], "global")["results"]) == 3


def test_follow_research_does_not_change_trading_watchlist_or_mode(isolated, monkeypatch):
    cfg = dict(desk.load_config(), mode="live_manual", watchlist=["AAPL"]); desk.save_config(cfg)
    before = desk.CONFIG_PATH.read_bytes()
    monkeypatch.setattr(markets, "history", lambda value: markets.parse_history("VOD.L", payload(), NOW))
    response = isolated.post("/api/markets/watchlist", json={"symbol": "VOD.L"}, base_url=BASE)
    assert response.status_code == 200 and response.json["watchlist"] == ["YF:VOD.L"]
    assert desk.CONFIG_PATH.read_bytes() == before
    assert ops.EvidenceStore(desk.DATA_DIR).list("market_observation")[0]["payload"]["instrument"]["currency"] == "GBp"
    assert isolated.post("/api/markets/watchlist", json={"symbol": "AAPL", "arm": True}, base_url=BASE).status_code == 400


def test_wider_rotation_covers_us_directory_without_duplicates(isolated):
    rows = [{"symbol": "X"+chr(65+i)} for i in range(14)]
    desk._save_json(desk.DATA_DIR/"market_universe.json", {"symbols": rows, "updated_at": NOW.isoformat()})
    p = {"symbols": ["AAPL", "SPY"], "universe": "broad_us", "candidates_per_cycle": 6}
    first = market_universe.candidates(desk, p, 0, NOW)
    second = market_universe.candidates(desk, p, 6, NOW)
    assert len(first) == 6 and len(second) == 6 and first[0] == "AAPL" and second[0] == "SPY"
    assert not set(first[1:]).intersection(second[1:])
    with pytest.raises(ValueError): moss_policy.validate(dict(moss_policy.DEFAULTS, candidates_per_cycle=9))


def test_existing_watchlist_find_uses_verified_directory_before_model(isolated, monkeypatch):
    desk._save_json(desk.DATA_DIR/"market_universe.json", {"symbols": [{"symbol": "FIVE", "name": "Five Below common stock"}]})
    monkeypatch.setattr(desk, "watchlist_find_gemini", lambda *a, **k: pytest.fail("Unnecessary model call"))
    result = desk.watchlist_find("Five Below", ["AAPL"], mode="suggest")
    assert result["suggest"] == ["FIVE"] and result["source"] == "local"


def test_new_write_routes_reject_bad_shapes(isolated):
    assert isolated.post('/api/operations/suites/missing/stage', json=['bad'], base_url=BASE).status_code == 400
    assert isolated.post('/api/markets/watchlist', json={'symbol':'AAPL','remove':'yes'}, base_url=BASE).status_code == 400


def test_evidence_revisions_preserve_original_and_scope_links(tmp_path):
    store = ops.EvidenceStore(tmp_path)
    signal = {"id": "s", "ticker": "AAPL", "review_identity": {"account_id": "A", "paper_mode": False}}
    original = store.put("signal", "live:A", "s", signal)
    changed = store.put("signal", "live:A", "s", dict(signal, status="corrected"))
    assert changed != original and "status" not in store.get(original)["payload"]
    other = store.put("signal", "live:B", "s", dict(signal, account_id="B"))
    fill = store.put("execution", "live:A", "e", {"order_ref": "s", "ticker": "AAPL", "asset_type": "STK"})
    links = ops.dossier(store, fill)["related"]
    assert {r["id"] for r in links} == {original, changed} and other not in {r["id"] for r in links}
    unknown = store.put("execution", "unverified:live", "e", {"order_ref": "s"})
    assert ops.dossier(store, unknown)["missing"]
    option = store.put("execution", "live:A", "o", {"order_ref": "s", "asset_type": "OPT", "con_id": 99})
    assert not ops.dossier(store, option)["related"]


def test_tampered_evidence_rejected(tmp_path):
    store = ops.EvidenceStore(tmp_path); ident = store.put("decision", "paper:local", "d", {"price": 10})
    with store.db() as db: db.execute("UPDATE records SET payload='{}' WHERE id=?", (ident,))
    with pytest.raises(ValueError, match="fingerprint"): store.get(ident)


def test_attention_dedup_review_keeps_block_and_reopen(tmp_path):
    store = ops.EvidenceStore(tmp_path)
    items = ops.attention({"ok": True, "account_id": "A", "day_pnl_usd": None}, {}, {}, [])
    first = store.incidents(items)[0]
    with store.db() as db: db.execute("UPDATE incidents SET reviewed_at=?", (ops.utc(),))
    repeated = store.incidents(items)
    assert len(repeated) == 1 and repeated[0]["first_seen"] == first["first_seen"] and repeated[0]["reviewed_at"]
    assert not repeated[0]["resolved_at"]
    assert store.incidents([])[0]["resolved_at"]
    assert store.incidents(items)[0]["reviewed_at"] is None
    assert not ops.attention({"ok": True, "day_pnl_usd": 0, "risk_ready": True}, {}, {}, [])


def qualified_event(i):
    start = datetime(2026, 9, 23, 13, 40, tzinfo=timezone.utc)+timedelta(minutes=11*i)
    end = start+timedelta(minutes=10)
    def q(at): return {"price": 100, "market_time": at.isoformat(), "fresh": True, "source": "recorded feed"}
    return {"id": str(i), "ts": start.isoformat(), "outcome_ts": end.isoformat(), "outcome_market_time": end.isoformat(),
            "quote": q(start), "outcome_quote": q(end), "ticker": "AAPL", "source": "moss_paper", "moss_policy_version": moss_policy.VERSION,
            "moss_quality": {"ok": True}, "horizon_min": 10, "intended_side": "buy", "llm_model": "recorded-model", "prompt_version": "v1",
            "input_hash": "digest", "scoring_version": "horizon-net-v2", "outcome_status": "scored", "outcome_executable_move_bps": 20,
            "mid": 100, "outcome_mid": 100.3, "confidence": .5+i*.01}


def test_evaluation_same_frozen_inputs_purges_and_excludes_live_mock_future():
    rows = [qualified_event(i) for i in range(20)]
    rows += [dict(qualified_event(22), source="broker"), dict(qualified_event(23), mock=True), qualified_event(50)]
    result = ops.evaluate(rows, 5, NOW)
    assert result == ops.evaluate(copy.deepcopy(rows), 5, NOW)
    assert result["qualified"] == 20 and len(result["variants"]) == 4
    assert result["variants"][0]["splits"]["test"]["mean_net_bps"] == 15
    assert set(result["split_ids"]["train"]).isdisjoint(result["split_ids"]["test"])
    assert result["status"] == "insufficient_evidence" and sum(result["excluded"].values()) == 3
    assert ops.evaluate(rows, 30, NOW)["variants"][0]["splits"]["test"]["mean_net_bps"] == -10


def test_suite_capture_replay_and_stage_cannot_arm(isolated, monkeypatch):
    import moss_paper
    monkeypatch.setattr(moss_paper, "history", lambda _: [qualified_event(i) for i in range(20)])
    cfg = dict(desk.load_config(), mode="live_manual"); desk.save_config(cfg)
    before = desk.CONFIG_PATH.read_bytes()
    first = isolated.post("/api/operations/suites", json={"extra_friction_bps": 5}, base_url=BASE)
    assert first.status_code == 200
    ident = first.json["suites"][0]["id"]
    assert isolated.get(f"/api/operations/suites/{ident}/replay", base_url=BASE).json["identical"]
    second = isolated.post("/api/operations/suites", json={}, base_url=BASE)
    assert second.json["suites"][0]["reused_test_outcomes"] == 4
    for stage in ("live", "reviewed"):
        assert isolated.post(f"/api/operations/suites/{ident}/stage", json={"stage": stage, "note": "Review note long enough"}, base_url=BASE).status_code == 400
    assert isolated.post(f"/api/operations/suites/{ident}/stage", json={"stage": "paper_observation", "note": "New observations required"}, base_url=BASE).status_code == 200
    assert desk.CONFIG_PATH.read_bytes() == before


def test_release_roundtrip_does_not_rollback_durable_state(tmp_path, monkeypatch):
    root = tmp_path/"app"; root.mkdir(); (root/"app.py").write_text("value = 1\n")
    (root/".env").write_text("SECRET=private")
    checks = root/"tests"; checks.mkdir()
    (checks/"graphics.cjs").write_text("require('node:assert').ok(true);\n")
    data = root/"data"; data.mkdir(); (data/"ledger.json").write_text('{"filled":true}')
    archive = tmp_path/"release.zip"; release_tools.pack(root, archive)
    assert release_tools.verify(archive)["file_count"] == 2
    (checks/"graphics.cjs").write_text("changed")
    (root/"app.py").write_text("value = 2\n")
    assert not release_tools.restore(archive, root)["applied"]
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a: __import__('errno').ECONNREFUSED)
    release_tools.restore(archive, root, apply=True)
    assert (root/"app.py").read_text() == "value = 1\n"
    assert (checks/"graphics.cjs").read_text() == "require('node:assert').ok(true);\n"
    assert (data/"ledger.json").read_text() == '{"filled":true}' and (root/".env").read_text() == "SECRET=private"
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a: 0)
    with pytest.raises(ValueError, match="Stop the desk"): release_tools.restore(archive, root, apply=True)


def test_release_rejects_path_escape_and_tampering(tmp_path):
    for name in ("../app.py", "data/ledger.json", ".env", "C:/app.py", "tools/../../data/x.py"):
        assert not release_tools.allowed(name)
    root = tmp_path/"app"; root.mkdir(); (root/"app.py").write_text("x=1")
    archive = tmp_path/"release.zip"; release_tools.pack(root, archive)
    with zipfile.ZipFile(archive, "a") as z: z.writestr("extra.py", "x=2")
    with pytest.raises(ValueError): release_tools.verify(archive)
