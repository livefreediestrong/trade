"""The upkeep watchdog never opens IB Gateway on its own schedule."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def upkeep(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / "tools" / "desk_upkeep.py"
    spec = importlib.util.spec_from_file_location("desk_upkeep_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "UPKEEP", tmp_path)
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    return module


def test_broker_disconnect_asks_the_desk_instead_of_running_the_launcher(upkeep, monkeypatch):
    monkeypatch.setattr(upkeep, "health", lambda timeout=10.0: {
        "app_id": "tomahawk-desk", "broker": {"configured": True, "connected": False}})
    monkeypatch.setattr(upkeep, "run_launcher", lambda reason: pytest.fail("launcher must not run"))
    asked = []
    monkeypatch.setattr(upkeep, "ensure_gateway_via_desk",
                        lambda: asked.append(1) or {"ok": False, "launched": False, "automatic_launch_held": True})
    for _ in range(3):
        result = upkeep.watchdog()
        assert result["action"] == "ensure_gateway" and result["gateway"]["automatic_launch_held"]
    assert len(asked) == 3


def test_desk_restart_launcher_never_opens_gateway(upkeep, monkeypatch):
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(upkeep.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    upkeep.run_launcher("desk unreachable")
    assert "-NoBroker" in calls[0]


def test_desk_request_is_marked_automatic(upkeep, monkeypatch):
    import json
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return json.dumps({"ok": False, "launched": False, "note": "held"}).encode()

    def fake_urlopen(req, timeout):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return Resp()

    monkeypatch.setattr(upkeep.urllib.request, "urlopen", fake_urlopen)
    assert upkeep.ensure_gateway_via_desk()["note"] == "held"
    assert seen["url"].endswith("/api/broker-ensure-gateway")
    assert seen["body"] == {"launch_if_down": True, "automatic": True}


@pytest.mark.parametrize("body,automatic", [
    ({"launch_if_down": True}, False),           # desk button: owner action
    ({"launch_if_down": True, "automatic": True}, True),  # upkeep watchdog
    ({"automatic": "yes"}, False),               # only a real boolean marks automatic
])
def test_ensure_gateway_endpoint_passes_automatic_flag(monkeypatch, body, automatic):
    import app as desk
    import broker_ibkr

    monkeypatch.setenv("BROKER_PROVIDER", "ibkr")
    calls = []
    monkeypatch.setattr(broker_ibkr, "ensure_gateway",
                        lambda **kw: calls.append(kw) or {"ok": True, "launched": False, "note": "x"})
    response = desk.app.test_client().post("/api/broker-ensure-gateway", base_url="http://127.0.0.1:5056", json=body)
    assert response.status_code == 200
    assert calls == [{"launch_if_down": True, "automatic": automatic}]
