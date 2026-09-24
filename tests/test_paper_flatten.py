"""Paper close-all must never reach a configured live broker."""
import copy
import socket

import pytest

import app as desk
import broker_alpaca
import broker_ibkr


BASE = "http://127.0.0.1:5056"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    desk._CORRUPT_PATHS.clear()
    monkeypatch.setattr(desk, "_MARKS", {})
    monkeypatch.setattr(desk, "_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("No network in paper flatten"))
    monkeypatch.setattr(desk, "fetch_last_price", lambda ticker: {"LONG": 110., "SHORT": 45.}.get(ticker))
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("Paper flatten must not invoke broker hooks")

    for name in ("broker_flatten_if_configured", "live_broker_place_order", "_broker_public_status"):
        monkeypatch.setattr(desk, name, forbidden)
    for module in (broker_alpaca, broker_ibkr):
        monkeypatch.setattr(module, "is_configured", lambda: True)
        for name in ("flatten_broker", "cancel_all_orders", "place_order", "get_open_orders", "get_positions"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden)
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    cfg = dict(desk.load_config(), mode="live_manual", session_active=True,
               paper_research_enabled=False, paper_auto_approve=False, slip_bps=0, fee_bps=0)
    desk.save_config(cfg)
    ledger = desk.load_ledger()
    ledger.update(
        cash=10_000., equity=10_200.,
        positions=[{"ticker": "LONG", "side": "long", "shares": 3, "avg_price": 100.},
                   {"ticker": "SHORT", "side": "short", "shares": 2, "avg_price": 50.}],
        fills=[], daily={},
        broker_fills=[{"id": "keep-fill", "order_id": "existing", "status": "partially_filled"}],
        broker_fills_archive=[{"id": "keep-archive"}],
        broker_daily={desk._today_str(): {"trades": 7, "realized": 11.}},
        broker_positions=[{"ticker": "BROKER", "qty": 4}],
        pending_broker_orders=[{"order_id": "existing", "filled_qty": 2, "remaining_qty": 8}],
    )
    desk.save_ledger(ledger)
    desk.save_signals([{"id": "live-ticket", "workspace": "live", "status": "broker_pending"}])
    return copy.deepcopy(ledger), copy.deepcopy(cfg), calls


@pytest.mark.parametrize("provider", ["ibkr", "alpaca"])
@pytest.mark.parametrize("via_api", [False, True])
def test_paper_flatten_closes_local_positions_and_preserves_broker_state(isolated, monkeypatch, provider, via_api):
    before, cfg_before, calls = isolated
    monkeypatch.setenv("BROKER_PROVIDER", provider)
    signals_before = desk.SIGNALS_PATH.read_bytes()
    if via_api:
        response = desk.app.test_client().post('/api/ledger/flatten', base_url=BASE, json={"workspace": "paper"})
        assert response.status_code == 200, response.get_json()
        result = response.get_json()
        assert "broker" not in result
    else:
        result = desk.paper_flatten_all(cfg_before)
    assert result["ok"] and result["flatten_complete"] and result["local_ok"]
    assert result["workspace"] == "paper" and result["count"] == 2
    assert result["broker_flatten"]["attempted"] is False
    after = desk.load_ledger()
    assert after["positions"] == []
    assert after["cash"] == after["equity"] == 10_240.
    assert {(f["ticker"], f["side"], f["shares"]) for f in after["fills"]} == {
        ("LONG", "sell", 3), ("SHORT", "buy", 2),
    }
    assert all(f["simulated"] for f in after["fills"])
    for key, value in before.items():
        if key.startswith("broker_") or key == "pending_broker_orders":
            assert after[key] == value
    assert desk.SIGNALS_PATH.read_bytes() == signals_before
    assert desk.load_config() == cfg_before
    assert calls == []


def test_paper_flatten_is_complete_when_only_broker_positions_remain(isolated):
    before, cfg_before, calls = isolated
    before["positions"] = []
    desk.save_ledger(before)
    result = desk.paper_flatten_all(cfg_before)
    assert result["ok"] and result["flatten_complete"] and result["count"] == 0
    assert result["workspace"] == "paper" and not result["broker_flatten"]["attempted"]
    assert desk.load_ledger() == before and calls == []


def test_paper_flatten_reports_local_failure_without_broker_attempt(isolated, monkeypatch):
    before, cfg_before, calls = isolated
    before["positions"] = [{"ticker": "BAD", "side": "long", "shares": 2, "avg_price": 0}]
    desk.save_ledger(before)
    monkeypatch.setattr(desk, "fetch_last_price", lambda ticker: None)
    response = desk.app.test_client().post('/api/ledger/flatten', base_url=BASE, json={})
    result = response.get_json()
    assert response.status_code == 207
    assert not result["ok"] and not result["flatten_complete"]
    assert result["closed"][0]["error"] == "no_price"
    assert desk.load_ledger() == before and desk.load_config() == cfg_before and calls == []


def test_paper_flatten_endpoint_rejects_live_workspace(isolated):
    before, cfg_before, calls = isolated
    response = desk.app.test_client().post('/api/ledger/flatten', base_url=BASE, json={"workspace": "live"})
    assert response.status_code == 400
    assert desk.load_ledger() == before and desk.load_config() == cfg_before and calls == []
