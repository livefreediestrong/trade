"""Live ON/OFF: OFF always removes risk; ON only reaches approve-each-order through the shared gate."""
import copy

import pytest

import app as desk
import broker_router as router
import live_switch
from test_execution_repairs import execution  # noqa: F401 - fixture reuse
from broker_fixtures import IDENTITY

BASE = "http://127.0.0.1:5056"


@pytest.fixture
def switch(execution, monkeypatch):  # noqa: F811
    broker = {"configured": True, "paper_mode": True, "broker": "alpaca"}
    monkeypatch.setattr(desk, "_broker_public_status", lambda: dict(broker))
    monkeypatch.setitem(router.__dict__, "verify_execution_context", lambda: {"ok": True, "identity": copy.deepcopy(IDENTITY)})
    monkeypatch.setitem(router.__dict__, "public_status", lambda: dict(broker))
    return desk.app.test_client(), broker


def post(client, body):
    return client.post("/api/live/master", json=body, base_url=BASE)


def test_off_returns_to_manual_and_stops_fox(switch):
    client, _ = switch
    cfg = desk.load_config()
    cfg["live_agent"] = {"enabled": True, "policy": {}}
    desk.save_config(cfg)
    data = post(client, {"live": False}).get_json()
    saved = desk.load_config()
    assert data["live"] is False and "no orders" in data["plain"]
    assert saved["mode"] == "manual" and saved["live_agent"]["enabled"] is False


def test_on_needs_the_phrase_and_only_reaches_approve_each_order(switch):
    client, _ = switch
    post(client, {"live": False})
    assert post(client, {"live": True}).status_code == 400
    assert post(client, {"live": True, "confirm": "yes"}).status_code == 400
    assert post(client, {"live": True, "confirm": "GO LIVE", "mode": "auto_live"}).status_code == 400
    data = post(client, {"live": True, "confirm": "go live"}).get_json()
    assert data["live"] is True and desk.load_config()["mode"] == "live_manual"
    assert any(row.get("action") == "live_master_on" for row in desk.load_journal())


def test_on_refuses_without_a_broker_and_never_touches_an_automatic_desk(switch):
    client, broker = switch
    post(client, {"live": False})
    broker["configured"] = False
    assert post(client, {"live": True, "confirm": "GO LIVE"}).status_code == 409
    assert desk.load_config()["mode"] == "manual"
    broker["configured"] = True
    cfg = desk.load_config()
    cfg["mode"] = "auto_live"
    desk.save_config(cfg)
    data = post(client, {"live": True, "confirm": "GO LIVE"}).get_json()
    assert data["mode"] == "auto_live" and desk.load_config()["mode"] == "auto_live"


def test_real_money_uses_the_real_token_and_plain_wording():
    view = live_switch.view({"mode": "live_manual"}, {"configured": True, "paper_mode": False})
    assert view["real_money"] and "real-money account" in view["plain"]
    assert "no orders" in live_switch.view({"mode": "manual"}, {})["plain"]
