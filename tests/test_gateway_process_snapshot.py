import json
from types import SimpleNamespace as NS

import pytest
import broker_ibkr as broker


def snapshot(*rows, java=()):
    return {"ok": True, "processes": list(rows) or [process("explorer", 1)], "java": list(java)}


def process(name, pid=2, title=""):
    return {"ProcessName": name, "Id": pid, "MainWindowTitle": title}


def test_login_windows_and_headless_gateway_block_duplicate_launch():
    assert broker._gateway_process_snapshot_matches(snapshot(process("ibgateway"))) == ["ibgateway.exe"]
    assert broker._gateway_process_snapshot_matches(snapshot(process("javaw", title="IBKR Gateway Login"))) == ["javaw.exe"]
    assert broker._gateway_process_snapshot_matches(snapshot(java=[
        {"Name": "javaw.exe", "CommandLine": r"javaw.exe -cp C:\Jts\ibgateway.jar"}])) == ["javaw.exe"]


def test_browser_titles_and_reused_pid_are_not_gateway():
    data = snapshot(process("chrome", 51, "IBKR Gateway - help"))
    assert broker._gateway_process_snapshot_matches(data, {"pid": 51, "pid_image": "ibgateway.exe"}) == []
    assert broker._gateway_process_snapshot_matches(snapshot(process("launcher", 51)),
                                                     {"pid": 51, "pid_image": "launcher.exe"}) == ["launcher.exe"]


@pytest.mark.parametrize("data", [None, {}, {"ok": True, "processes": [], "java": []},
    {"ok": True, "processes": [{}], "java": []}, snapshot(java=[{"Name": "javaw.exe", "CommandLine": None}])])
def test_incomplete_scan_never_reports_gateway_absent(data):
    assert broker._gateway_process_snapshot_matches(data) is None


def test_fast_probe_requires_complete_success(monkeypatch):
    import subprocess
    monkeypatch.setattr(broker.os, "name", "nt")
    monkeypatch.setattr(broker, "_read_gateway_stamp", lambda: {})
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: NS(returncode=0, stdout=json.dumps(snapshot())))
    assert broker._running_gateway_processes() == []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: NS(returncode=1, stdout=json.dumps(snapshot())))
    assert broker._running_gateway_processes() is None
