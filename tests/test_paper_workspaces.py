"""Separate paper experiments from the main broker session using real local paths."""
import copy
import datetime as dt
import socket

import pytest

import app as desk
import data_sources as ds

BASE = "http://127.0.0.1:5056"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    desk._CORRUPT_PATHS.clear()
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **kw: pytest.fail("No network in workspace tests"))
    monkeypatch.setattr(desk, "fetch_last_price", lambda ticker: 100.)
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *a, **kw: pytest.fail("Paper must never submit to a broker"))
    monkeypatch.setattr(desk, "_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(desk, "_MARKS", {})
    cfg = dict(desk.load_config(), mode="live_manual", session_active=False, rth_only=False,
               paper_research_enabled=True, paper_auto_approve=True)
    desk.save_config(cfg)


def signal(**updates):
    now = dt.datetime.now(dt.timezone.utc)
    sig = dict(id="live-idea", ticker="TEST", side="buy", status="pending", suggested_shares=1,
               signal_price=100., confidence=.9, verdict="PASS", ts=now.isoformat(),
               created_at=now.isoformat(), expires_at=(now + dt.timedelta(minutes=15)).isoformat(),
               llm_model="mock-momentum", quote=ds.quote_snapshot(100, now, "fixture"))
    sig.update(updates)
    return sig


def test_auto_paper_fills_own_id_while_main_live_session_is_stopped():
    original = desk.load_config()
    result = desk.ingest_signal(signal())
    records = desk.load_signals()
    live = next(s for s in records if s["workspace"] == "live")
    paper = next(s for s in records if s["workspace"] == "paper")
    assert result["id"] == live["id"] == "live-idea"
    assert live["status"] == "pending" and "fill" not in live
    assert paper["id"] != live["id"] and paper["source_signal_id"] == live["id"]
    assert paper["status"] == "approved", paper
    ledger = desk.load_ledger()
    assert len(ledger["fills"]) == 1 and len(ledger["positions"]) == 1
    assert not ledger.get("broker_fills") and not ledger.get("pending_broker_orders")
    assert desk.load_config() == original


def test_disabling_auto_approve_keeps_paper_for_manual_review():
    cfg = desk.load_config()
    cfg["paper_auto_approve"] = False
    desk.save_config(cfg)
    desk.ingest_signal(signal())
    paper = next(s for s in desk.load_signals() if s["workspace"] == "paper")
    assert paper["status"] == "pending"
    response = desk.app.test_client().post(f'/api/signals/{paper["id"]}/approve', base_url=BASE, json={})
    assert response.status_code == 200, response.get_json()
    assert len(desk.load_ledger()["fills"]) == 1
    assert next(s for s in desk.load_signals() if s["workspace"] == "live")["status"] == "pending"


@pytest.mark.parametrize("changes", [{"side": "hold"}, {"llm_error": "offline"}, {"synthetic": True},
                                   {"quote": ds.quote_snapshot(100, 1, "old")}])
def test_auto_paper_retains_nonactionable_research_without_fill(changes):
    desk.ingest_signal(signal(**changes))
    assert not desk.load_ledger().get("fills")
    assert all(s["status"] == "pending" for s in desk.load_signals())


def test_manual_research_button_never_auto_fills():
    desk.ingest_signal(signal(), research_only=True)
    assert len(desk.load_signals()) == 2
    assert not desk.load_ledger().get("fills")


def test_paper_controls_do_not_change_live_mode_or_identity_or_ledger():
    original = desk.load_config()
    ledger = desk.load_ledger()
    client = desk.app.test_client()
    response = client.post('/api/paper-research', base_url=BASE,
                           json={"enabled": False, "auto_approve": False, "risk_preset": "low"})
    assert response.status_code == 200
    expected = dict(original, paper_research_enabled=False, paper_auto_approve=False, paper_risk_preset="low")
    assert desk.load_config() == expected and desk.load_ledger() == ledger
    for payload in ({"enabled": "true"}, {"auto_approve": 1}, {"mode": "auto_live"}, {"risk_preset": "bad"}, {"risk_preset": []}):
        assert client.post('/api/paper-research', base_url=BASE, json=payload).status_code == 400
        assert desk.load_config() == expected


def test_live_start_and_stop_preserve_paper_state():
    cfg = desk.load_config()
    ledger = desk.load_ledger()
    ledger.update(positions=[{"ticker": "TEST", "side": "long", "shares": 1, "avg_price": 100}],
                  fills=[{"id": "keep-this-fill"}])
    desk.save_ledger(ledger)
    client = desk.app.test_client()
    response = client.post('/api/session/start', base_url=BASE,
                           json={"beginning_bank_usd": 1, "make_today_usd": 1, "confirm_reset": True})
    assert response.status_code == 200
    after = desk.load_config()
    assert after["session_active"] and after["mode"] == "live_manual"
    for key in ("paper_equity", "paper_cash", "paper_research_enabled", "paper_auto_approve", "paper_risk_preset"):
        assert after[key] == cfg[key]
    assert desk.load_ledger() == ledger
    assert client.post('/api/session/stop', base_url=BASE, json={}).status_code == 200
    assert desk.load_config()["paper_research_enabled"]
    assert desk.load_ledger() == ledger


def test_paper_stop_blocks_new_risk_even_if_live_active():
    cfg = desk.load_config()
    snapshot = desk.paper_research_config(cfg)
    desk.save_config(dict(cfg, session_active=True, paper_research_enabled=False))
    ok, reason = desk.can_take_trade(snapshot, desk.load_ledger(), 100)
    assert not ok and "Session not active" in reason


def test_old_pending_does_not_block_new_research_and_is_retained_as_history():
    cfg = desk.load_config()
    old = signal(id="old", workspace="live", quote=ds.quote_snapshot(100, 1, "old"))
    working = signal(id="working", workspace="live", status="broker_pending")
    assert not desk._pending_scan_blocks(old, cfg)
    assert desk._pending_scan_blocks(working, cfg)
    assert not desk._pending_scan_blocks(signal(workspace="live"), cfg)  # mock brain
    desk.save_signals([old, working])
    desk.ingest_signal(signal(), research_only=True)
    records = {s["id"]: s for s in desk.load_signals()}
    assert records["old"]["status"] == "expired" and records["old"]["superseded_by"] == "live-idea"
    assert records["working"]["status"] == "broker_pending"


def test_desk_calls_and_opportunities_retain_workspace():
    cfg = desk.load_config()
    live = signal(id="live", workspace="live")
    paper = signal(id="paper", workspace="paper")
    assert desk._latest_desk_call(cfg, [paper, live], {})["id"] == "live"
    assert desk._latest_desk_call(desk.paper_research_config(cfg), [live, paper], {})["id"] == "paper"
    assert {s["workspace"] for s in desk._ranked_opportunities([live, paper], [], {})} == {"live", "paper"}


def test_ticker_cleanup_only_cancels_paper():
    desk.save_signals([signal(id="live", workspace="live"), signal(id="paper", workspace="paper")])
    assert desk._cancel_pending_signals_for_ticker("TEST", "test") == 1
    assert {s["id"]: s["status"] for s in desk.load_signals()} == {"live": "pending", "paper": "expired"}


def test_direct_paper_fill_refuses_live_candidate():
    result = desk.paper_fill(signal(workspace="live"), desk.paper_research_config(desk.load_config()), source="test")
    assert not result["ok"] and "Live ideas" in result["error"]
    assert not desk.load_ledger().get("fills")


def test_paper_results_do_not_inherit_live_goal_and_include_scanner_experiments(monkeypatch):
    cfg = dict(desk.load_config(), daily_profit_target_usd=50, max_session_loss_usd=10)
    desk.save_config(cfg)
    desk.ingest_signal(signal())
    paper_cfg = desk.paper_research_config(cfg)
    assert paper_cfg["max_session_loss_usd"] is None
    assert desk.daily_target_progress(paper_cfg, desk.load_ledger())["target_usd"] is None
    monkeypatch.setattr(desk._decision_ring, "latest", lambda *args: [])
    recap = desk.daily_recap(paper_cfg, desk.load_ledger())
    assert recap["decisions"] == 1 and recap["fills"] == 1
    latest = desk._latest_desk_call(paper_cfg, desk.load_signals(), {})
    assert latest["workspace"] == "paper" and latest["filled"]
