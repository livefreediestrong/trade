"""Exercise the real owner thread and priority queue with transport denied."""
from concurrent.futures import ThreadPoolExecutor
import itertools
import queue
import threading
import time
import pytest
import broker_ibkr as b


def test_slow_call_does_not_duplicate_owner_and_cancel_runs_next(monkeypatch):
    monkeypatch.setattr(b, "_CLIENT", None)
    monkeypatch.setattr(b, "_WORKER", None)
    monkeypatch.setattr(b, "_TASKS", queue.PriorityQueue())
    monkeypatch.setattr(b, "_TASK_SEQUENCE", itertools.count())
    monkeypatch.setattr(b, "_STOP", threading.Event())
    monkeypatch.setattr(b, "_WORKER_ACTIVITY", {"operation": None, "started": None, "completed": 0})
    entered, release = threading.Event(), threading.Event()
    order, owners = [], set()
    def slow_provider():
        owners.add(threading.get_ident())
        entered.set()
        assert release.wait(3)
        raise RuntimeError("provider timeout")
    def operation(name):
        def run(*args, **kwargs):
            owners.add(threading.get_ident())
            order.append(name)
            return name
        run.__name__ = name
        return b._on_api_thread(run)
    with ThreadPoolExecutor(5) as pool:
        slow = pool.submit(b._on_api_thread(slow_provider))
        assert entered.wait(2)
        owner = b._WORKER
        jobs = [pool.submit(operation("quote")), pool.submit(operation("get_account")),
                pool.submit(operation("find_order_by_signal")), pool.submit(operation("_poll"), cancel=True)]
        try:
            deadline = time.monotonic()+2
            while b._TASKS.qsize() < 4 and time.monotonic() < deadline:
                time.sleep(.005)
            assert b._TASKS.qsize() == 4 and b._WORKER is owner
            b._WORKER_ACTIVITY["started"] = time.monotonic()-181
            assert b.worker_snapshot()["stalled"] is True
        finally:
            release.set()
        try:
            slow.result(2)
        except RuntimeError:
            pass
        assert [job.result(2) for job in jobs] == ["quote", "get_account", "find_order_by_signal", "_poll"]
    b._STOP.set()
    owner.join(2)
    assert not owner.is_alive()
    assert order == ["_poll", "find_order_by_signal", "get_account", "quote"]
    assert len(owners) == 1 and b.worker_snapshot()["completed"] == 5


def test_hung_owner_times_out_and_cancels_reads_without_replacing_connection(monkeypatch):
    for name, value in (("_CLIENT", None), ("_WORKER", None), ("_TASKS", queue.PriorityQueue()),
                        ("_TASK_SEQUENCE", itertools.count()), ("_STOP", threading.Event()),
                        ("READ_CALL_TIMEOUT_SEC", .05),
                        ("_WORKER_ACTIVITY", {"operation": None, "started": None, "completed": 0})):
        monkeypatch.setattr(b, name, value)
    entered, release = threading.Event(), threading.Event()
    reads = []
    def blocked_transport():
        entered.set()
        assert release.wait(3)
    def get_account():
        reads.append(True)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(b._on_api_thread(blocked_transport))
        assert entered.wait(1)
        owner = b._WORKER
        try:
            with pytest.raises(ConnectionError, match="timed out"):
                b._on_api_thread(get_account)()
            for _ in range(20):
                with pytest.raises(ConnectionError, match="not responding"):
                    b._on_api_thread(get_account)()
            assert b._WORKER is owner and b._TASKS.qsize() == 1
        finally:
            release.set()
            pending.result(2)
        # The queued read was cancelled; a later fresh request still works.
        b._on_api_thread(get_account)()
        assert reads == [True]
        b._STOP.set()
        owner.join(2)
        assert not owner.is_alive()


def test_full_read_queue_still_admits_cancel_and_reconciliation_first(monkeypatch):
    for name,value in (("_CLIENT",None),("_WORKER",None),("_TASKS",queue.PriorityQueue()),
            ("_TASK_SEQUENCE",itertools.count()),("_STOP",threading.Event()),("_QUIESCING",threading.Event()),
            ("READ_CALL_TIMEOUT_SEC",5.),("_WORKER_ACTIVITY",{"operation":None,"started":None,"completed":0})):
        monkeypatch.setattr(b,name,value)
    entered,release=threading.Event(),threading.Event();order=[];owners=set()
    def slow_transport():
        entered.set();assert release.wait(4)
    def operation(name):
        def call(*a,**k):
            owners.add(threading.get_ident());order.append(name);return name
        call.__name__=name;return b._on_api_thread(call)
    with ThreadPoolExecutor(b.MAX_READ_QUEUE+4) as pool:
        blocked=pool.submit(b._on_api_thread(slow_transport));assert entered.wait(1)
        owner=b._WORKER
        reads=[pool.submit(operation('stock_quote')) for _ in range(b.MAX_READ_QUEUE)]
        try:
            deadline=time.monotonic()+2
            while b._TASKS.qsize()<b.MAX_READ_QUEUE and time.monotonic()<deadline:time.sleep(.005)
            assert b._TASKS.qsize()==b.MAX_READ_QUEUE
            with pytest.raises(ConnectionError,match='busy'):operation('stock_quote')()
            reconcile=pool.submit(operation('find_order_by_signal'))
            cancel=pool.submit(operation('_poll'),cancel=True)
            deadline=time.monotonic()+1
            while b._TASKS.qsize()<b.MAX_READ_QUEUE+2 and time.monotonic()<deadline:time.sleep(.005)
            assert b._TASKS.qsize()==b.MAX_READ_QUEUE+2 and b._WORKER is owner
        finally:release.set()
        try:
            blocked.result(2);assert cancel.result(2)=='_poll';assert reconcile.result(2)=='find_order_by_signal'
            assert all(job.result(2)=='stock_quote' for job in reads)
            assert order[:2]==['_poll','find_order_by_signal'] and len(owners)==1
        finally:
            b._STOP.set();owner.join(2)
    assert not owner.is_alive()
