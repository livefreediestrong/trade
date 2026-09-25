"""A running desk detects updated code on disk and stops only when it is safe to restart."""
from __future__ import annotations

import os

import pytest

import app as desk
import code_version

BASE = "http://127.0.0.1:5056"


def test_fingerprint_changes_when_source_changes(tmp_path, monkeypatch):
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "ui.js").write_text("let a=1;", encoding="utf-8")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "state.py").write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(code_version, "_digests", {})
    first = code_version.fingerprint(tmp_path)
    (tmp_path / "data" / "state.py").write_text("still ignored", encoding="utf-8")
    assert code_version.fingerprint(tmp_path) == first
    ui = tmp_path / "static" / "ui.js"
    before = ui.stat().st_mtime_ns
    ui.write_text("let a=2;", encoding="utf-8")
    # Same-size writes inside one filesystem clock tick keep the old mtime on Windows.
    os.utime(ui, ns=(before + 1_000_000_000, before + 1_000_000_000))
    assert code_version.fingerprint(tmp_path) != first


def test_status_reports_stale_code(monkeypatch):
    monkeypatch.setattr(code_version, "_state", {"started": "aaa", "current": "aaa", "checked": 0.0})
    monkeypatch.setattr(code_version, "fingerprint", lambda root=code_version.ROOT: "bbb")
    status = code_version.status(force=True)
    assert status["stale"] is True and "Reopen the desktop shortcut" in status["message"]
    assert desk._startup_status()["code_stale"] is True


@pytest.fixture
def shutdown(monkeypatch, tmp_path):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    started = []
    monkeypatch.setattr(desk.threading, "Thread", lambda target, name=None, daemon=None: type(
        "T", (), {"start": lambda self: started.append(target)})())
    return started


def test_shutdown_requires_confirmation_and_the_serving_process(shutdown, monkeypatch):
    client = desk.app.test_client()
    assert client.post("/api/desk/shutdown", base_url=BASE, json={}).status_code == 400
    monkeypatch.setattr(desk, "_SERVING", False)
    reply = client.post("/api/desk/shutdown", base_url=BASE, json={"confirm": "RESTART"})
    assert reply.status_code == 409 and not shutdown


def test_shutdown_refuses_while_a_broker_order_is_unresolved(shutdown, monkeypatch):
    monkeypatch.setattr(desk, "_SERVING", True)
    ledger = desk.load_ledger()
    ledger["pending_broker_orders"] = [{"signal": {"id": "s1"}}]
    desk.save_ledger(ledger)
    reply = desk.app.test_client().post("/api/desk/shutdown", base_url=BASE, json={"confirm": "RESTART"})
    assert reply.status_code == 409 and "unresolved" in reply.get_json()["error"] and not shutdown


def test_shutdown_stops_the_idle_serving_desk(shutdown, monkeypatch):
    monkeypatch.setattr(desk, "_SERVING", True)
    reply = desk.app.test_client().post("/api/desk/shutdown", base_url=BASE, json={"confirm": "RESTART"})
    assert reply.status_code == 200 and reply.get_json()["stopping"] is True
    assert shutdown == [desk._exit_desk_process]


def test_shutdown_rejects_cross_site_requests(shutdown, monkeypatch):
    monkeypatch.setattr(desk, "_SERVING", True)
    reply = desk.app.test_client().post("/api/desk/shutdown", base_url=BASE, json={"confirm": "RESTART"},
                                        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
    assert reply.status_code == 403 and not shutdown


def test_health_reports_code_status():
    body = desk.app.test_client().get("/api/health", base_url=BASE).get_json()
    assert set(body["code"]) >= {"started", "on_disk", "stale"}


def test_exit_is_cancelled_when_an_order_started_after_the_request(monkeypatch, tmp_path):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    monkeypatch.setattr(desk.time, "sleep", lambda s: None)
    monkeypatch.setattr(desk.os, "_exit", lambda code: pytest.fail("must not exit with an order pending"))
    ledger = desk.load_ledger()
    ledger["pending_broker_orders"] = [{"signal": {"id": "late"}}]
    desk.save_ledger(ledger)
    desk._exit_desk_process()
    assert desk.load_journal()[0]["action"] == "app_stop_cancelled"
