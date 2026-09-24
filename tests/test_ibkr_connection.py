"""Persistent Gateway ownership tests with no network or real account access."""
import sys
import threading
import pytest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace as NS

import broker_ibkr as broker


class Event:
    def __init__(self):
        self.handlers = []
    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self
    def emit(self):
        for fn in self.handlers:
            fn()


def test_concurrent_reads_reuse_one_owned_connection_and_reconnect(monkeypatch):
    monkeypatch.setenv('BROKER_PROVIDER', 'ibkr')
    monkeypatch.setenv('IBKR_LIVE', 'false')
    monkeypatch.setenv('IB_GATEWAY_PORT', '4002')
    monkeypatch.setenv('IB_CLIENT_ID', '37')
    monkeypatch.setenv('IBKR_ACCOUNT', '')
    for name, value in (('_CLIENT', None), ('_CLIENT_SETTINGS', None), ('_VERIFIED', {}), ('_PNL', {}),
                        ('_CONNECTION', {'connected':False,'error':None})):
        monkeypatch.setattr(broker, name, value)
    instances = []
    threads = set()
    class Client:
        def __init__(self):
            self.connected = False
            self.disconnectedEvent = Event()
            instances.append(self)
        def touch(self): threads.add(threading.get_ident())
        def connect(self, *args, **kwargs):
            self.touch()
            self.connected = True
        def isConnected(self):
            self.touch()
            return self.connected
        def disconnect(self):
            self.touch()
            self.connected = False
            self.disconnectedEvent.emit()
        def managedAccounts(self):
            self.touch()
            return ['DU123']
        def positions(self, account):
            self.touch()
            return []
        def sleep(self, interval): self.touch()
    monkeypatch.setitem(sys.modules, 'ib_insync', NS(IB=Client))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: broker.verify_execution_context(), range(8)))
    assert all(result['ok'] for result in results)
    assert len(instances) == 1 and instances[0].connected
    assert broker.get_positions() == {'ok': True, 'positions': []}
    assert len(threads) == 1 and threading.get_ident() not in threads
    assert broker.public_status()['connected']
    broker._on_api_thread(instances[0].disconnect)()
    assert broker.paper_mode() is None and not broker.public_status()['connected']
    assert broker.verify_execution_context()['ok']
    assert len(instances) == 2 and instances[1].connected
    # Endpoint/client changes cannot reuse a previously verified identity/socket.
    monkeypatch.setenv('IB_CLIENT_ID', '38')
    assert broker.paper_mode() is None
    assert broker.verify_execution_context()['ok']
    assert len(instances) == 3 and not instances[1].connected
    broker._on_api_thread(instances[-1].disconnect)()


def test_account_book_preserves_balances_without_daily_pnl(monkeypatch):
    import app as desk
    import broker_alpaca as adapter
    monkeypatch.setenv('BROKER_PROVIDER', 'alpaca')
    monkeypatch.setattr(desk, '_BROKER_BOOK_CACHE', {'at':0.,'val':None})
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: True)
    result = {'ok': True, 'risk_ready': False, 'risk_error': 'Daily P&L unavailable',
              'pnl_diagnostics': {'status':'waiting', 'callbacks':0},
              'account': {'id':'TEST', 'equity':123., 'cash':100., 'buying_power':100., 'day_pnl':None,
                          'account_window_pnl': {'realized':0., 'unrealized':0., 'status':'reported'}}}
    monkeypatch.setattr(adapter, 'get_account', lambda: result)
    monkeypatch.setattr(adapter, 'get_positions', lambda: {'ok': True, 'positions': []})
    view = desk._broker_book_cached()
    assert view['ok'] and view['equity'] == 123. and view['account_id'] == 'TEST'
    assert not view['risk_ready'] and view['day_pnl_usd'] is None
    assert 'unavailable' in view['risk_error']
    assert view['account_window_pnl']['realized'] == 0.
    assert view['pnl_diagnostics']['callbacks'] == 0
    assert desk._broker_day_pnl()[2]  # Readable balances never grant execution permission.


def test_gateway_outage_backs_off_between_dashboard_reads(monkeypatch):
    clock = [100.]
    attempts = []
    class Client:
        def __init__(self): self.disconnectedEvent = Event()
        def connect(self, *args, **kwargs):
            attempts.append(kwargs['clientId'])
            raise TimeoutError()
        def disconnect(self): pass
    for name, value in (('_CLIENT', None), ('_CLIENT_SETTINGS', None), ('_SERVER_UNAVAILABLE', False),
                        ('_RECONNECT_AFTER', 0.), ('_FAILED_SETTINGS', None),
                        ('_CONNECTION', {'connected':False, 'error':None})):
        monkeypatch.setattr(broker, name, value)
    monkeypatch.setattr(broker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setitem(sys.modules, 'ib_insync', NS(IB=Client))
    monkeypatch.setenv('IB_CLIENT_ID', '37')
    with pytest.raises(ConnectionError, match='synchronization timed out'): broker._ib()
    for _ in range(5):
        with pytest.raises(ConnectionError, match='synchronization timed out'): broker._ib()
    assert attempts == [37]
    clock[0] = 106.
    with pytest.raises(ConnectionError): broker._ib()
    assert attempts == [37, 37]
    monkeypatch.setenv('IB_CLIENT_ID', '38')
    with pytest.raises(ConnectionError): broker._ib()
    assert attempts[-1] == 38  # A changed endpoint/client configuration can retry immediately.
