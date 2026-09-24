"""Captured research, journal boundaries, and immutable broker order terms."""
import copy
import csv
import io
import json
import socket

import pytest
import app as desk
import desk_workbench as wb
import order_terms
import market_capture

BASE = "http://127.0.0.1:5056"


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL", "DECISIONS"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    monkeypatch.setattr(desk, "_CORRUPT_PATHS", set())
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    return desk.app.test_client()


def event(**kwargs):
    return dict({"id":"1", "event":"decision", "ts":"2026-09-23T14:00:00+00:00", "decision":"buy",
                 "ticker":"TEST", "llm_model":"model-v1", "prompt_version":"v3", "horizon_min":20,
                 "confidence":.8, "mid":100, "outcome_move_bps":100,
                 "outcome_executable_move_bps":88, "outcome_status":"scored"}, **kwargs)


@pytest.mark.parametrize("value", [0,-1,True,float("nan"),float("inf"),"abc",None])
def test_order_rejects_invalid_limit(value):
    with pytest.raises(ValueError):
        order_terms.canonical_order({"side":"buy","suggested_shares":2}, {"type":"limit","limit_price":value})


def test_order_exact_price_quantity_and_cost_boundary():
    order = order_terms.canonical_order({"side":"buy","suggested_shares":3}, {"type":"limit","limit_price":"1.13"})
    assert order["notional_bound"] == 3.39
    for options in ({"type":"stop"}, {"type":"limit","limit_price":1.001}, {"max_total":10}, {"shares":1}, []):
        with pytest.raises(ValueError):
            order_terms.canonical_order({"side":"buy","suggested_shares":3}, options)


def test_journal_filter_export_and_cumulative_fill_are_separate():
    signal = {"id":"s", "workspace":"live", "ticker":"TEST", "status":"approved", "created_at":"2026-09-23T14:00:00+00:00", "source":"=HYPERLINK(1)"}
    fill = {"id":"f", "signal_id":"s", "order_id":"o", "shares":5, "price":100, "ticker":"TEST", "ts":signal["created_at"]}
    rows = wb.journal_rows([signal], {"broker_fills":[fill, dict(fill)]})
    assert len(rows) == 2 and {r["kind"] for r in rows} == {"order","fill"}
    selected = wb.filter_journal(rows, {"workspace":"live","kind":"fill","ticker":"test","from":"2026-09-23T13:00:00Z"})
    assert len(selected) == 1 and selected[0]["shares"] == 5
    parsed = list(csv.DictReader(io.StringIO(wb.csv_export(selected))))
    assert parsed[0]["strategy"].startswith("'=HYPERLINK")
    assert parsed[0]["fee_usd"] == ""  # Never fabricate broker costs.
    with pytest.raises(ValueError): wb.filter_journal(rows,{"from":"2026-09-23"})


def test_evaluation_excludes_unscored_mock_and_mixes_no_horizons_or_versions():
    groups = wb.calibration([event(), event(id="2",decision="hold"),event(id="3",mock=True),
                             event(id="4",outcome_status="missed_horizon_window"),
                             event(id="5",horizon_min=5),event(id="6",prompt_version="v4")])
    first = groups[0]
    assert len(groups)==3 and first["scored"]==1 and first["actionable"]==3
    assert first["coverage"] == pytest.approx(1/3) and first["abstention_rate"] == .25
    assert first["brier"] == pytest.approx(.04)
    assert market_capture.scorecard([event(outcome="helped"),event(decision="hold",outcome="helped")],[])['outcome_coverage'] == 1


def test_replay_fixed_capital_partial_fill_and_sell_direction():
    params = wb.experiment_parameters({"capital":1000,"position_pct":100,"fill_pct":50,"spread_bps":10,"fee_bps":1,"delay_bps":5})
    capture = [event(),event(id="2",decision="sell",outcome_move_bps=-100),event(id="3",horizon_min=5),event(id="4",mock=True)]
    before = copy.deepcopy(capture)
    result = wb.replay(capture,params)
    assert result == wb.replay(list(reversed(capture)),params) and capture == before
    assert result["eligible"] == 2 and result["excluded"]==2
    assert result["trades"][0]["filled_shares"]==5
    assert result["trades"][0]["net_bps"]==83 and result["trades"][1]["net_bps"]==83
    assert result["scenario_pnl_sum"] == 8.3


def test_replay_current_producer_direction_and_legacy_precedence():
    params = wb.experiment_parameters({'capital':1000, 'position_pct':100})
    buy = event(intended_side='buy', outcome_move_bps=32)
    buy.pop('decision')
    sell = event(id='sell', intended_side='sell', decision='buy', outcome_move_bps=-32)
    result = wb.replay([buy, sell], params)
    assert result['eligible'] == 2 and result['excluded'] == 0
    assert [r['side'] for r in result['trades']] == ['buy', 'sell']
    assert result['trades'][0]['net_bps'] == result['trades'][1]['net_bps']
    invalid = event(decision='unknown')
    assert wb.replay([invalid], params)['excluded'] == 1


def test_experiment_roundtrip_hash_and_separate_settings(files):
    desk._save_json(desk.DECISIONS_PATH,{"events":[event()]})
    cfg = desk.load_config()
    cfg.update(mode="live_manual",paper_auto_approve=True)
    desk.save_config(cfg)
    original=desk.CONFIG_PATH.read_bytes()
    response = files.post("/api/workbench/experiments",json={"capital":5000},base_url=BASE)
    assert response.status_code == 200
    experiment=response.get_json()["experiments"][0]
    replay=files.get("/api/workbench/experiments/"+experiment["id"],base_url=BASE).get_json()
    assert replay["identical"] and replay["experiment"]["capture"][0]["llm_model"]=="model-v1"
    assert desk.CONFIG_PATH.read_bytes()==original
    path=desk.DATA_DIR/"workbench.json"
    changed=json.loads(path.read_text());changed["experiments"][0]["capture"][0]["mid"]=1
    desk._save_json(path,changed)
    assert files.get("/api/workbench/experiments/"+experiment["id"],base_url=BASE).status_code==400


def test_watchlists_never_apply_without_explicit_config_action(files):
    cfg=desk.load_config(); original=desk.CONFIG_PATH.read_bytes()
    result=files.post("/api/workbench/watchlists",json={"name":"Core","symbols":"AAPL MSFT"},base_url=BASE)
    assert result.status_code==200 and result.get_json()["watchlists"]["Core"]==["AAPL","MSFT"]
    assert desk.CONFIG_PATH.read_bytes()==original


def test_cache_never_labels_old_symbol_as_new(monkeypatch):
    cfg={}
    monkeypatch.setattr(desk,"_STATE_AUX",{"data_key":desk._state_aux_key(cfg,["AAPL"],"AAPL"),
                       "key":desk._state_aux_key(cfg,["MSFT"],"MSFT"),"at":__import__('time').time(),
                       "data":{"research_context":{"ticker":"AAPL"}},"refreshing":True})
    assert desk._state_aux_snapshot(cfg,["MSFT"],"MSFT")=={}
    assert desk._state_aux_snapshot(cfg,["AAPL"],"AAPL")["research_context"]["ticker"]=="AAPL"


def test_json_compression_preserves_exact_payload_and_opt_out():
    import gzip
    from flask import Response
    raw = json.dumps({"quotes":[{"symbol":"TEST","value":100}] * 1000}).encode()
    with desk.app.test_request_context("/api/state", headers={"Accept-Encoding":"gzip"}):
        reply=desk._compress_json_snapshot(Response(raw,mimetype="application/json"))
        assert gzip.decompress(reply.data)==raw and len(reply.data)<len(raw)/5
        assert "Accept-Encoding" in reply.headers["Vary"]
    with desk.app.test_request_context("/api/state", headers={"Accept-Encoding":"gzip;q=0"}):
        reply=desk._compress_json_snapshot(Response(raw,mimetype="application/json"))
        assert reply.data==raw and not reply.headers.get("Content-Encoding")
    with desk.app.test_request_context("/api/loop/stream", headers={"Accept-Encoding":"gzip"}):
        reply=desk._compress_json_snapshot(Response(iter([b'data: {}\n\n']),mimetype="text/event-stream"))
        assert reply.is_streamed and not reply.headers.get("Content-Encoding")
