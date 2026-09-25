import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import app as desk

BASE = 'http://127.0.0.1:5056'


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(desk, '_SERVING', True)
    monkeypatch.setattr(desk, '_CORRUPT_PATHS', set())
    monkeypatch.setattr(desk, '_decision_ring', SimpleNamespace(_load_error=None))
    monkeypatch.setattr(desk, '_live_agent', SimpleNamespace(cycle_lock=SimpleNamespace(locked=lambda: False)))
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: False)
    monkeypatch.setattr(desk, 'load_config', lambda: {'session_active': False})
    monkeypatch.setattr(desk, 'load_ledger', lambda: {'pending_broker_orders': []})
    monkeypatch.setattr(desk, 'append_journal', lambda *args: None)
    monkeypatch.setattr(desk, '_exit_desk_process', lambda **kw: pytest.fail('No real process exit in tests'))
    return desk.app.test_client()


def test_recovery_requires_pid_and_same_origin(isolated):
    assert isolated.post('/api/desk/recover', base_url=BASE, json={'expected_pid': -1}).status_code == 409
    assert isolated.post('/api/desk/recover', base_url=BASE, json={'expected_pid': os.getpid()},
                         headers={'Origin': 'https://evil.test'}).status_code == 403


def test_recovery_restarts_only_missing_workers(isolated, monkeypatch):
    missing, calls = ['signal-scan'], []
    monkeypatch.setattr(desk, 'worker_health', lambda: {'missing': list(missing), 'memory_error': None})
    monkeypatch.setattr(desk, '_start_scan_worker', lambda: calls.append('scan') or missing.clear())
    monkeypatch.setattr(desk, '_research_companion', SimpleNamespace(start=lambda: pytest.fail('Healthy companion restarted')))
    for _ in range(2):
        result = isolated.post('/api/desk/recover', base_url=BASE, json={'expected_pid': os.getpid()})
        assert result.status_code == 200 and result.get_json()['ok']
    assert calls == ['scan']


def test_corrupt_evidence_blocks_automatic_repair(isolated, monkeypatch):
    monkeypatch.setattr(desk, 'worker_health', lambda: {'missing': ['signal-scan'], 'memory_error': 'unreadable'})
    monkeypatch.setattr(desk, '_start_scan_worker', lambda: pytest.fail('Cannot reset evidence'))
    assert isolated.post('/api/desk/recover', base_url=BASE, json={'expected_pid': os.getpid()}).status_code == 409


def test_automatic_restart_is_deferred_for_active_session(isolated, monkeypatch):
    monkeypatch.setattr(desk, 'load_config', lambda: {'session_active': True})
    reply = isolated.post('/api/desk/shutdown', base_url=BASE,
                          json={'confirm': 'RESTART', 'automatic': True, 'expected_pid': os.getpid()})
    assert reply.status_code == 409 and reply.get_json()['deferred']


@pytest.mark.parametrize('book,orders', [
    (None, {'ok': True, 'orders': []}),
    ({'ok': True, 'risk_ready': False, 'positions': []}, {'ok': True, 'orders': []}),
    ({'ok': True, 'risk_ready': True, 'positions': [{}]}, {'ok': True, 'orders': []}),
    ({'ok': True, 'risk_ready': True, 'positions': []}, {'ok': False}),
    ({'ok': True, 'risk_ready': True, 'positions': []}, {'ok': True, 'orders': [{}]}),
])
def test_automatic_restart_waits_for_verified_empty_broker(isolated, monkeypatch, book, orders):
    import broker_router
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: True)
    monkeypatch.setattr(desk, '_broker_book_cached', lambda: book)
    monkeypatch.setattr(broker_router, 'get_open_orders', lambda: orders)
    assert desk._automatic_restart_error()


def test_automatic_exit_rechecks_permission_after_acceptance(isolated, monkeypatch):
    # Exercise the real final check with a changed session and forbid process exit.
    import inspect
    monkeypatch.undo()
    monkeypatch.setattr(desk.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(desk, '_automatic_restart_error', lambda: 'Session became active')
    journal = []
    monkeypatch.setattr(desk, 'append_journal', lambda *a: journal.append(a))
    monkeypatch.setattr(desk.os, '_exit', lambda *a: pytest.fail('Must not exit'))
    desk._exit_desk_process(automatic=True)
    assert journal[0][0] == 'app_stop_cancelled'


@pytest.fixture
def upkeep(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location('upkeep_refined', Path(__file__).resolve().parents[1]/'tools'/'desk_upkeep.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'UPKEEP', tmp_path)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: None)
    return module


def test_watchdog_defers_restart_without_running_launcher(upkeep, monkeypatch):
    monkeypatch.setattr(upkeep, 'health', lambda **kw: {'app_id': 'tomahawk-desk', 'pid': 12,
        'instance': {'source_root': str(upkeep.ROOT.resolve())}, 'code': {'stale': True},
        'broker': {'connected': True}, 'recovery': {'expected': True, 'missing': []}})
    asked = []
    monkeypatch.setattr(upkeep, 'request_recovery', lambda path, body: asked.append((path, body)) or {'ok': False, 'deferred': True})
    monkeypatch.setattr(upkeep, 'run_launcher', lambda reason: pytest.fail('Must not start a duplicate'))
    assert upkeep.watchdog()['action'] == 'restart_deferred'
    upkeep.watchdog()
    assert len(asked) == 1 and asked[0][1]['automatic'] is True


def test_watchdog_repairs_worker_without_process_restart(upkeep, monkeypatch):
    monkeypatch.setattr(upkeep, 'health', lambda **kw: {'pid': 12, 'broker': {'connected': True},
        'recovery': {'expected': True, 'missing': ['signal-scan']}})
    asked = []
    monkeypatch.setattr(upkeep, 'request_recovery', lambda path, body: asked.append(path) or {'ok': True})
    monkeypatch.setattr(upkeep, 'run_launcher', lambda reason: pytest.fail('Must not restart'))
    assert upkeep.watchdog()['action'] == 'recover_workers'
    assert asked == ['/api/desk/recover']
