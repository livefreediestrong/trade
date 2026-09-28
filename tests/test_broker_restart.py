"""Exercise orderly restart with the real owner queue; never connect to a broker."""
from concurrent.futures import ThreadPoolExecutor
import itertools
import queue
import threading
import time
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker


@pytest.fixture
def owned(monkeypatch):
    for name, value in (("_CLIENT", None), ("_CLIENT_SETTINGS", None), ("_WORKER", None),
                        ("_TASKS", queue.PriorityQueue()), ("_TASK_SEQUENCE", itertools.count()),
                        ("_STOP", threading.Event()), ("_QUIESCING", threading.Event()),
                        ("_CLOSE_FUTURE", None), ("_PNL", {}), ("_PNL_UPDATED", {}),
                        ("_ACCOUNT_READY", {}), ("_ACCOUNT_EQUITY", {}), ("_API_PULSE", {}),
                        ("_VERIFIED", {}), ("_CONNECTION", {}),
                        ("_WORKER_ACTIVITY", {"operation": None, "started": None, "completed": 0})):
        monkeypatch.setattr(broker, name, value, raising=False)
    events, threads, traces = [], set(), []
    connected = [True]
    def touch(kind):
        events.append(kind)
        threads.add(threading.get_ident())
    def cancel(account):
        touch("cancel_pnl")
    def disconnect():
        touch("disconnect")
        connected[0] = False
    client = NS(cancelPnL=cancel, disconnect=disconnect, isConnected=lambda: connected[0],
                sleep=lambda seconds: touch("pump"))
    monkeypatch.setattr(broker, "_pnl_trace", lambda event, **kw: traces.append((event, kw)))
    monkeypatch.setattr(broker, "_ib", lambda: pytest.fail("No connection may be opened"))
    def attach():
        broker._CLIENT = client
        broker._PNL[(id(client), "PRIVATE")] = {"value": object()}
    yield NS(client=client, events=events, threads=threads, connected=connected,
             traces=traces, attach=broker._on_api_thread(attach))
    broker._STOP.set()
    if broker._WORKER:
        broker._WORKER.join(2)
        assert not broker._WORKER.is_alive()


def test_restart_cancels_data_on_owner_before_disconnect_and_rejects_late_work(owned):
    owned.attach()
    owner = broker._WORKER
    assert broker.disconnect_for_restart(timeout=1)
    owner.join(1)
    assert not owner.is_alive()
    assert owned.events.count("cancel_pnl") == 1
    assert owned.events.index("cancel_pnl") < owned.events.index("disconnect")
    assert "pump" in owned.events[owned.events.index("cancel_pnl") + 1:owned.events.index("disconnect")]
    assert owned.threads == {owner.ident}
    assert broker._CLIENT is None and not broker._PNL
    assert broker.disconnect_for_restart(timeout=.1)  # Duplicate restart is idempotent.
    with pytest.raises(ConnectionError, match="closing"):
        owned.attach()
    assert broker._WORKER is owner and not owned.connected[0]
    assert "PRIVATE" not in repr(owned.traces)


def test_restart_without_owner_does_not_create_connection(owned):
    assert broker.disconnect_for_restart(timeout=.1)
    assert broker._WORKER is None and owned.events == []


def test_restart_does_not_touch_or_claim_close_of_an_orphaned_socket(owned, monkeypatch):
    monkeypatch.setattr(broker, "_CLIENT", owned.client)
    assert not broker.disconnect_for_restart(timeout=.1)
    assert owned.events == [] and owned.connected[0]


def test_timeout_does_not_interrupt_active_work_or_replay_canceled_cleanup(owned):
    owned.attach()
    entered, release = threading.Event(), threading.Event()
    def active_read():
        entered.set()
        assert release.wait(2)
        return "finished"
    with ThreadPoolExecutor(1) as pool:
        active = pool.submit(broker._on_api_thread(active_read))
        assert entered.wait(1)
        try:
            assert not broker.disconnect_for_restart(timeout=.02)
            assert owned.connected[0] and "cancel_pnl" not in owned.events
            assert not broker._QUIESCING.is_set()
        finally:
            release.set()
        assert active.result(1) == "finished"
    assert broker._on_api_thread(lambda: "fresh")() == "fresh"
    assert owned.connected[0]  # The expired cleanup may not run later.
    assert broker.disconnect_for_restart(timeout=1)


def test_cleanup_finishes_active_write_then_rejects_queued_operations(owned):
    owned.attach()
    entered, release = threading.Event(), threading.Event()
    writes = []
    def active_write():
        entered.set()
        assert release.wait(2)
        writes.append("completed")
    def queued_write():
        writes.append("must not run")
    with ThreadPoolExecutor(3) as pool:
        active = pool.submit(broker._on_api_thread(active_write))
        assert entered.wait(1)
        queued = pool.submit(broker._on_api_thread(queued_write))
        closing = pool.submit(broker.disconnect_for_restart, timeout=1)
        try:
            deadline = time.monotonic() + .5
            while not broker._QUIESCING.is_set() and time.monotonic() < deadline:
                time.sleep(.001)
            assert broker._QUIESCING.is_set()
            assert owned.connected[0] and not writes
        finally:
            release.set()
        active.result(1)
        assert closing.result(1)
        with pytest.raises(ConnectionError, match="closing"):
            queued.result(1)
    assert writes == ["completed"]


@pytest.mark.parametrize("failure", [RuntimeError, TimeoutError])
def test_cleanup_failure_keeps_process_recoverable_and_never_claims_success(owned, failure):
    owned.attach()
    def failed():
        raise failure("private wire detail")
    owned.client.disconnect = failed
    assert not broker.disconnect_for_restart(timeout=1)
    assert not broker._QUIESCING.is_set()
    assert owned.connected[0]
    assert not broker._PNL and not broker._PNL_UPDATED
    assert "private wire" not in repr(owned.traces)
    owned.client.disconnect = lambda: owned.connected.__setitem__(0, False)
    assert broker.disconnect_for_restart(timeout=1)


def test_running_cleanup_at_deadline_finishes_and_allows_restart(owned):
    # Cleanup that already owns the worker will close the socket anyway.
    # Refusing the restart then would leave a running desk with no broker.
    owned.attach()
    entered, release = threading.Event(), threading.Event()
    def delayed_disconnect():
        entered.set()
        assert release.wait(2)
        owned.connected[0] = False
    owned.client.disconnect = delayed_disconnect
    with ThreadPoolExecutor(1) as pool:
        attempt = pool.submit(broker.disconnect_for_restart, timeout=.05)
        assert entered.wait(1)
        release.set()
        assert attempt.result(2) is True
        broker._WORKER.join(1)
    assert not owned.connected[0]


def test_hung_running_cleanup_still_allows_restart_after_grace(owned, monkeypatch):
    monkeypatch.setattr(broker, "RESTART_CLEANUP_GRACE_SEC", .05)
    owned.attach()
    entered, release = threading.Event(), threading.Event()
    def stuck_disconnect():
        entered.set()
        release.wait(2)
        owned.connected[0] = False
    owned.client.disconnect = stuck_disconnect
    try:
        assert broker.disconnect_for_restart(timeout=.05) is True
        assert entered.is_set()
    finally:
        release.set()
        broker._WORKER.join(1)


@pytest.fixture
def stopping(monkeypatch):
    import app as desk
    journal, exits = [], []
    monkeypatch.setattr(desk.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(desk, "load_ledger", lambda: {"pending_broker_orders": []})
    monkeypatch.setattr(desk, "append_journal", lambda event, payload: journal.append((event, payload)))
    monkeypatch.setattr(desk, "release_instance_lock", lambda: exits.append("released"))
    monkeypatch.setattr(desk.os, "_exit", lambda code: exits.append(code))
    return NS(desk=desk, journal=journal, exits=exits)


def test_process_exit_requires_successful_broker_cleanup(stopping, monkeypatch):
    monkeypatch.setattr(broker, "disconnect_for_restart", lambda **kw: False, raising=False)
    stopping.desk._exit_desk_process()
    assert stopping.exits == []
    assert stopping.journal[-1][0] == "app_stop_cancelled"


def test_process_exit_closes_connection_before_releasing_instance(stopping, monkeypatch):
    calls = []
    monkeypatch.setattr(broker, "disconnect_for_restart", lambda **kw: calls.append("closed") or True, raising=False)
    monkeypatch.setattr(stopping.desk, "release_instance_lock", lambda: calls.append("released"))
    monkeypatch.setattr(stopping.desk.os, "_exit", lambda code: calls.append("exited"))
    stopping.desk._exit_desk_process()
    assert calls == ["closed", "released", "exited"]


def test_unresolved_submission_prevents_cleanup_as_well_as_exit(stopping, monkeypatch):
    monkeypatch.setattr(stopping.desk, "load_ledger", lambda: {"pending_broker_orders": [{}]})
    monkeypatch.setattr(broker, "disconnect_for_restart", lambda **kw: pytest.fail("Must preserve connection"), raising=False)
    stopping.desk._exit_desk_process()
    assert stopping.exits == []
