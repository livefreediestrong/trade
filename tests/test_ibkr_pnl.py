"""Exercise silent, recovered and stale P&L feeds without a broker connection."""
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker


@pytest.fixture
def pnl_gateway(monkeypatch):
    clock = [100.]
    rows = []
    canceled = []
    fake = NS(pnlEvent=object(), isConnected=lambda:True, reqCurrentTime=lambda:object(),
              accountSummary=lambda account: [NS(account=account, currency='USD', tag=tag, value=value)
                  for tag, value in {'NetLiquidation':'110', 'TotalCashValue':'110', 'BuyingPower':'110'}.items()],
              accountValues=lambda account: rows,
              reqPnL=lambda account: NS(account=account, modelCode='', dailyPnL=float('nan')),
              cancelPnL=lambda account: canceled.append(account),
              sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    @contextmanager
    def session():
        yield fake
    monkeypatch.setattr(broker, '_session', session)
    monkeypatch.setattr(broker, '_identity', lambda ib: {'account_id':'TEST', 'paper_mode':False})
    monkeypatch.setattr(broker, '_PNL', {})
    monkeypatch.setattr(broker, '_PNL_UPDATED', {})
    monkeypatch.setattr(broker, '_ACCOUNT_READY', {})
    monkeypatch.setattr(broker, '_API_PULSE', {})
    monkeypatch.setattr(broker, '_SERVER_UNAVAILABLE', False)
    monkeypatch.setattr(broker, '_RESYNC_REQUIRED', False)
    monkeypatch.setattr(broker, '_VERIFIED', {})
    monkeypatch.setattr(broker, '_CONNECTION', {'connected':True, 'error':None})
    monkeypatch.setattr(broker.time, 'monotonic', lambda: clock[0])
    def add(tag, value, currency='BASE', account='TEST', model=''):
        rows.append(NS(tag=tag, value=value, currency=currency, account=account, modelCode=model))
    return fake, clock, rows, add, canceled


def test_real_ledger_zero_is_displayable_but_not_daily_pnl(pnl_gateway):
    _, _, _, add, _ = pnl_gateway
    add('AccountReady', 'true', '')
    add('$LEDGER-RealizedPnL', '0.00')
    add('$LEDGER-UnrealizedPnL', '0.00')
    result = broker.get_account.__wrapped__()
    assert result['ok'] and result['account']['equity'] == 110
    assert result['account']['account_window_pnl'] == {'realized':0., 'unrealized':0., 'status':'reported'}
    assert result['account']['day_pnl'] is None and not result['risk_ready']
    assert result['pnl_diagnostics']['status'] == 'waiting'
    assert result['pnl_diagnostics']['callbacks'] == 0
    assert 'no daily P&L update' in result['risk_error']


def test_silent_retry_preserves_elapsed_wait_and_requires_new_callback(pnl_gateway):
    fake, clock, _, _, canceled = pnl_gateway
    broker.get_account.__wrapped__()
    old = broker._PNL[(id(fake), 'TEST')]['value']
    clock[0] = 161.
    result = broker.get_account.__wrapped__()
    assert canceled == ['TEST']
    assert result['pnl_diagnostics']['retries'] == 1
    assert result['pnl_diagnostics']['waiting_seconds'] >= 61
    old.dailyPnL = 100.
    broker._pnl_update(old)
    assert not broker._PNL_UPDATED
    current = broker._PNL[(id(fake), 'TEST')]['value']
    current.dailyPnL = -8.25
    broker._pnl_update(current)
    result = broker.get_account.__wrapped__()
    assert result['risk_ready'] and result['account']['day_pnl'] == -8.25
    assert result['pnl_diagnostics']['callbacks'] == 1
    assert result['pnl_diagnostics']['status'] == 'ready'
    clock[0] += 31
    result = broker.get_account.__wrapped__()
    assert result['risk_ready'] and result['account']['day_pnl'] == -8.25
    assert result['pnl_diagnostics']['status'] == 'ready'
    assert canceled == ['TEST']  # Unchanged values do not restart a valid stream.


def test_other_model_callback_cannot_refresh_daily_account_pnl(pnl_gateway):
    fake, _, _, _, _ = pnl_gateway
    broker.get_account.__wrapped__()
    broker._pnl_update(NS(account='TEST', modelCode='MODEL', dailyPnL=0.))
    broker._pnl_update(NS(account='OTHER', modelCode='', dailyPnL=0.))
    assert not broker._PNL_UPDATED
    current = broker._PNL[(id(fake), 'TEST')]['value']
    current.dailyPnL = float('nan')
    broker._pnl_update(current)
    result = broker.get_account.__wrapped__()
    assert not result['risk_ready'] and result['pnl_diagnostics']['status'] == 'unavailable'


@pytest.mark.parametrize('loss_code', [1100, 2110, 1101])
def test_connection_loss_during_initial_wait_rejects_queued_pnl(pnl_gateway, loss_code):
    fake, clock, _, _, _ = pnl_gateway
    fired = []
    def pump(seconds):
        clock[0] += seconds
        if not fired:
            fired.append(True)
            broker._server_error(-1, loss_code, 'connection lost')
            pnl = broker._PNL[(id(fake), 'TEST')]['value']
            pnl.dailyPnL = 0.
            broker._pnl_update(pnl)
    fake.sleep = pump
    result = broker.get_account.__wrapped__()
    assert not result['risk_ready'] and result['account']['day_pnl'] is None
    assert result['pnl_diagnostics']['status'] == 'recovering'
    assert result['pnl_diagnostics']['callbacks'] == 0
    assert not broker._PNL_UPDATED


@pytest.mark.parametrize('loss_code', [1100, 2110])
def test_recovery_requires_callback_after_outage_not_queued_during_it(pnl_gateway, monkeypatch, loss_code):
    fake, clock, _, _, _ = pnl_gateway
    fake.isConnected = lambda: True
    monkeypatch.setattr(broker, '_CLIENT', fake)
    fired = []
    def pump(seconds):
        clock[0] += seconds
        if not fired:
            fired.append(True)
            broker._server_error(-1, loss_code, 'connection lost')
            pnl = broker._PNL[(id(fake), 'TEST')]['value']
            pnl.dailyPnL = 0.
            broker._pnl_update(pnl)
            broker._server_error(-1, 1102, 'data maintained')
    fake.sleep = pump
    result = broker.get_account.__wrapped__()
    assert not broker._SERVER_UNAVAILABLE and not broker._RESYNC_REQUIRED
    assert not result['risk_ready'] and not broker._PNL_UPDATED
    pnl = broker._PNL[(id(fake), 'TEST')]['value']
    broker._pnl_update(pnl)
    result = broker.get_account.__wrapped__()
    assert result['risk_ready'] and result['account']['day_pnl'] == 0.
    assert result['pnl_diagnostics']['callbacks'] == 1


def test_data_lost_recovery_invalidates_pnl_until_subscription_rebuilt(pnl_gateway, monkeypatch):
    fake, _, _, _, _ = pnl_gateway
    fake.isConnected = lambda: True
    monkeypatch.setattr(broker, '_CLIENT', fake)
    broker.get_account.__wrapped__()
    pnl = broker._PNL[(id(fake), 'TEST')]['value']
    pnl.dailyPnL = 0.
    broker._pnl_update(pnl)
    assert broker.get_account.__wrapped__()['risk_ready']
    broker._VERIFIED.update(account_id='TEST')
    broker._server_error(-1, 1101, 'data lost')
    assert not broker._PNL_UPDATED and not broker._VERIFIED
    broker._server_error(-1, 1102, 'data maintained')
    broker._pnl_update(pnl)
    assert broker._RESYNC_REQUIRED and not broker._PNL_UPDATED
    assert not broker.get_account.__wrapped__()['risk_ready']


def test_final_account_result_rechecks_connection_health(pnl_gateway):
    fake, _, _, _, _ = pnl_gateway
    broker.get_account.__wrapped__()
    pnl = broker._PNL[(id(fake), 'TEST')]['value']
    pnl.dailyPnL = 0.
    broker._pnl_update(pnl)
    def final_account_read(account):
        broker._server_error(-1, 1100, 'connection lost')
        return []
    fake.accountValues = final_account_read
    result = broker.get_account.__wrapped__()
    assert result['ok'] and result['account']['equity'] == 110.
    assert not result['risk_ready'] and result['account']['day_pnl'] is None
    assert result['pnl_diagnostics']['status'] == 'recovering'


def test_data_maintained_recovery_keeps_subscription_without_freshening_pnl(pnl_gateway, monkeypatch):
    fake, clock, _, _, _ = pnl_gateway
    broker.get_account.__wrapped__()
    subscription = broker._PNL[(id(fake), 'TEST')]
    pnl = subscription['value']
    clock[0] = 110.
    pnl.dailyPnL = 0.
    broker._pnl_update(pnl)
    fake.isConnected = lambda: True
    def unexpected_disconnect():
        pytest.fail('1102 must not disconnect a retained Gateway session')
    fake.disconnect = unexpected_disconnect
    monkeypatch.setattr(broker, '_CLIENT', fake)
    monkeypatch.setattr(broker, '_CLIENT_SETTINGS', broker._settings())
    monkeypatch.setattr(broker, '_FAILED_SETTINGS', None)
    monkeypatch.setattr(broker, '_RESYNC_REQUIRED', False)
    monkeypatch.setattr(broker, '_SERVER_UNAVAILABLE', False)
    monkeypatch.setattr(broker, '_CONNECTION', {'connected':True, 'error':None})
    # Exported trace: P&L 0 arrives, then 1102 arrives 27.297 seconds later.
    clock[0] = 137.297
    broker._server_error(-1, 1102, 'restored, data maintained')
    assert broker._ib() is fake
    assert broker._PNL[(id(fake), 'TEST')] is subscription
    assert broker._PNL_UPDATED['TEST'] == 110.
    assert subscription['callbacks'] == 1
    assert broker.get_account.__wrapped__()['account']['day_pnl'] == 0.
    clock[0] = 141.
    broker._server_error(-1, 1102, 'restored, data maintained')
    result = broker.get_account.__wrapped__()
    assert result['account']['day_pnl'] == 0. and result['risk_ready']
    assert result['pnl_diagnostics']['status'] == 'ready'
    assert broker._PNL_UPDATED['TEST'] == 110.


@pytest.mark.parametrize('loss_code', [1100, 2110])
def test_recovery_does_not_revive_pnl_invalidated_by_server_loss(pnl_gateway, monkeypatch, loss_code):
    fake, _, _, _, _ = pnl_gateway
    broker.get_account.__wrapped__()
    pnl = broker._PNL[(id(fake), 'TEST')]['value']
    pnl.dailyPnL = 0.
    broker._pnl_update(pnl)
    fake.isConnected = lambda: True
    monkeypatch.setattr(broker, '_CLIENT', fake)
    monkeypatch.setattr(broker, '_SERVER_UNAVAILABLE', False)
    monkeypatch.setattr(broker, '_RESYNC_REQUIRED', False)
    monkeypatch.setattr(broker, '_VERIFIED', {'account_id':'TEST'})
    monkeypatch.setattr(broker, '_CONNECTION', {'connected':True, 'error':None})
    broker._server_error(-1, loss_code, 'server lost')
    assert not broker._VERIFIED and not broker._PNL_UPDATED
    broker._server_error(-1, 1102, 'data maintained')
    assert not broker._RESYNC_REQUIRED
    assert not broker.get_account.__wrapped__()['risk_ready']
    broker._pnl_update(pnl)
    assert broker.get_account.__wrapped__()['risk_ready']
    broker._server_error(-1, 1101, 'data lost')
    broker._server_error(-1, 1102, 'data maintained')
    assert broker._RESYNC_REQUIRED  # A pending data-loss rebuild cannot be cleared.


@pytest.mark.parametrize('value', ['nan', 'inf', '1.7976931348623157e308', ''])
def test_invalid_ledger_values_cannot_become_zero(pnl_gateway, value):
    fake, _, _, add, _ = pnl_gateway
    add('$LEDGER-RealizedPnL', value)
    add('$LEDGER-UnrealizedPnL', value)
    assert broker._account_window_pnl(fake, 'TEST') == {'status':'unavailable', 'realized':None, 'unrealized':None}


def test_ledger_totals_require_account_and_aggregate_currency(pnl_gateway):
    fake, _, rows, add, _ = pnl_gateway
    add('$LEDGER-RealizedPnL', '500', 'USD')  # one currency segment is not the account total
    add('$LEDGER-RealizedPnL', '800', account='OTHER')
    add('$LEDGER-RealizedPnL', '900', model='MODEL')
    assert broker._account_window_pnl(fake, 'TEST')['realized'] is None
    add('$LEDGER-RealizedPnL', '-100')
    assert broker._account_window_pnl(fake, 'TEST')['realized'] == -100
    add('AccountReady', 'false', '')
    assert broker._account_window_pnl(fake, 'TEST')['status'] == 'account_resetting'
    assert broker._account_window_pnl(fake, 'TEST')['realized'] is None
    rows.clear()
    add('RealizedPnL', '-42', 'USD')  # legacy aggregate account-window tag
    assert broker._account_window_pnl(fake, 'TEST')['realized'] == -42
