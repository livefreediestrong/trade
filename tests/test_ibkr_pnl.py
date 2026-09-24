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
    monkeypatch.setattr(broker, '_PNL_WATCH', {'ui_status': None, 'last_error_code': None, 'soft_reconnect_count': 0, 'last_soft_reconnect_at': 0.0, 'last_scheduled_refresh_at': 0.0, 'pending_soft_reconnect': False, 'pending_reason': None})
    monkeypatch.setattr(broker, '_SOFT_RECONNECT_AFTER', 0.0)
    monkeypatch.setattr(broker, '_PNL_SILENT_RETRY_SEC', 60.0)
    monkeypatch.setattr(broker, '_PNL_FIRST_CALLBACK_WAIT_SEC', 3.0)
    monkeypatch.setattr(broker, '_PNL_SOFT_RECONNECT_WAIT_SEC', 90.0)
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


def test_refresh_broker_pnl_resubscribes_without_inventing_ready(pnl_gateway, monkeypatch):
    fake, clock, _, _, canceled = pnl_gateway
    fake.disconnect = lambda: setattr(fake, "_disconnected", True)
    monkeypatch.setattr(broker, "_CLIENT", fake)
    monkeypatch.setattr(broker, "_CLIENT_SETTINGS", broker._settings())
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.0)
    monkeypatch.setattr(broker, "_PNL_SILENT_RETRY_SEC", 60.0)
    monkeypatch.setattr(broker, "_PNL_FIRST_CALLBACK_WAIT_SEC", 3.0)
    monkeypatch.setattr(broker, "_PNL_SOFT_RECONNECT_WAIT_SEC", 90.0)
    # First call establishes a silent subscription.
    assert not broker.get_account.__wrapped__()["risk_ready"]
    # Manual refresh clears subscription; still no callback => still not ready.
    result = broker.refresh_broker_pnl.__wrapped__(soft_reconnect=False)
    assert canceled == ["TEST"]
    assert result["refresh"]["gateway_restarted"] is False
    assert result["refresh"]["soft_reconnect"] is False
    assert not result["risk_ready"] and result["account"]["day_pnl"] is None
    # Soft reconnect arms a socket rebuild; get_account path must still require a callback.
    def fake_ib():
        broker._RESYNC_REQUIRED = False
        broker._SERVER_UNAVAILABLE = False
        broker._CLIENT = fake
        broker._CONNECTION.update(connected=True, error=None)
        return fake
    monkeypatch.setattr(broker, "_ib", fake_ib)
    result = broker.refresh_broker_pnl.__wrapped__(soft_reconnect=True)
    assert result["refresh"]["soft_reconnect"] is True
    assert "api_disconnected" in result["refresh"]["actions"] or "manual_soft_reconnect" in result["refresh"]["actions"]
    assert not result["risk_ready"]
    # Only a genuine callback may unlock risk.
    current = broker._PNL[(id(fake), "TEST")]["value"]
    current.dailyPnL = 0.0
    broker._pnl_update(current)
    ready = broker.get_account.__wrapped__()
    assert ready["risk_ready"] and ready["account"]["day_pnl"] == 0.0


def test_auto_soft_reconnect_after_repeated_silent_retries(pnl_gateway, monkeypatch):
    fake, clock, _, _, canceled = pnl_gateway
    disconnected = []
    fake.disconnect = lambda: disconnected.append(True)
    monkeypatch.setattr(broker, "_CLIENT", fake)
    monkeypatch.setattr(broker, "_CLIENT_SETTINGS", broker._settings())
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.0)
    def fake_ib():
        broker._RESYNC_REQUIRED = False
        broker._SERVER_UNAVAILABLE = False
        broker._CLIENT = fake
        broker._CONNECTION.update(connected=True, error=None)
        return fake
    monkeypatch.setattr(broker, "_ib", fake_ib)
    broker.get_account.__wrapped__()
    clock[0] = 161.
    broker.get_account.__wrapped__()  # retry 1 via cancel
    assert canceled == ["TEST"]
    clock[0] = 222.
    # Force retries>=2 and waiting>=90 for auto soft reconnect.
    key = (id(fake), "TEST")
    broker._PNL[key]["retries"] = 2
    broker._PNL[key]["started_at"] = 100.
    broker._PNL[key]["requested_at"] = 161.
    result = broker.get_account.__wrapped__()
    assert disconnected, "expected API soft disconnect"
    assert not result["risk_ready"]
    assert broker._SOFT_RECONNECT_AFTER > clock[0]

def test_error_2100_marks_pending_soft_reconnect_without_inventing_ready(pnl_gateway, monkeypatch):
    fake, clock, _, _, _ = pnl_gateway
    fake.disconnect = lambda: None
    fake.client = type("C", (), {"reqAccountUpdates": staticmethod(lambda *a, **k: None)})()
    fake.reqAccountUpdates = lambda account: None
    monkeypatch.setattr(broker, "_CLIENT", fake)
    monkeypatch.setattr(broker, "_CLIENT_SETTINGS", broker._settings())
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.0)
    monkeypatch.setattr(broker, "_ACCOUNT_UNSUBSCRIBED", False)
    monkeypatch.setattr(broker, "_ACCOUNT_RESUBSCRIBE_AFTER", 0.0)
    assert not broker.get_account.__wrapped__()["risk_ready"]
    broker._server_error(-1, 2100, "API client has been unsubscribed from account data.")
    assert broker._ACCOUNT_UNSUBSCRIBED is True
    assert broker._PNL_WATCH.get("pending_resubscribe") is True
    assert broker._PNL_WATCH["ui_status"] == "Recovering Daily P&L..."
    assert not broker._PNL_UPDATED
    def fake_ib():
        broker._RESYNC_REQUIRED = False
        broker._SERVER_UNAVAILABLE = False
        broker._CLIENT = fake
        broker._CONNECTION.update(connected=True, error=None)
        return fake
    monkeypatch.setattr(broker, "_ib", fake_ib)
    result = broker.get_account.__wrapped__()
    assert not result["risk_ready"]
    # In-socket resubscribe preferred; soft reconnect only if that stays silent.
    assert result["pnl_diagnostics"].get("ui_status") in ("Recovering Daily P&L...", None) or not result["risk_ready"]
    # Only a real callback unlocks Ready.
    current = broker._PNL[(id(fake), "TEST")]["value"]
    current.dailyPnL = 1.5
    broker._pnl_update(current)
    ready = broker.get_account.__wrapped__()
    assert ready["risk_ready"] and ready["account"]["day_pnl"] == 1.5
    assert ready["pnl_diagnostics"].get("ui_status") in (None, "")


def test_maybe_scheduled_soft_refresh_respects_interval_and_ready(pnl_gateway, monkeypatch):
    fake, clock, _, _, _ = pnl_gateway
    fake.disconnect = lambda: None
    monkeypatch.setattr(broker, "_CLIENT", fake)
    monkeypatch.setattr(broker, "_CLIENT_SETTINGS", broker._settings())
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.0)
    def fake_ib():
        broker._RESYNC_REQUIRED = False
        broker._SERVER_UNAVAILABLE = False
        broker._CLIENT = fake
        broker._CONNECTION.update(connected=True, error=None)
        return fake
    monkeypatch.setattr(broker, "_ib", fake_ib)
    assert broker.maybe_scheduled_soft_refresh(interval_minutes=5, auto_live=True, risk_ready=True) is None
    assert broker.maybe_scheduled_soft_refresh(interval_minutes=0, auto_live=True, risk_ready=False) is None
    monkeypatch.setitem(broker._PNL_WATCH, "last_scheduled_refresh_at", None)
    # First observation only arms the timer (avoids startup 2100 storms).
    assert broker.maybe_scheduled_soft_refresh(interval_minutes=5, auto_live=True, risk_ready=False) is None
    clock[0] += 5 * 60 + 1
    first = broker.maybe_scheduled_soft_refresh(interval_minutes=5, auto_live=True, risk_ready=False)
    assert first is not None and first["refresh"]["gateway_restarted"] is False
    assert not first["risk_ready"]
    second = broker.maybe_scheduled_soft_refresh(interval_minutes=5, auto_live=True, risk_ready=False)
    assert second is None  # interval gate


def test_ensure_gateway_ok_when_port_open(monkeypatch):
    monkeypatch.setenv("IB_GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("IB_GATEWAY_PORT", "4002")
    monkeypatch.setattr(broker, "_port_open", lambda host, port, timeout=0.6: True)
    result = broker.ensure_gateway(launch_if_down=True)
    assert result["ok"] and result["port_open"] and not result["launched"]



def _gateway_env(monkeypatch, tmp_path, launched):
    monkeypatch.setenv("IB_GATEWAY_HOST", "127.0.0.1")
    monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    exe = tmp_path / "ibgateway.exe"
    exe.write_text("")
    monkeypatch.setattr(broker, "_port_open", lambda host, port, timeout=0.6: False)
    monkeypatch.setattr(broker, "_gateway_exe_candidates", lambda: [str(exe)])
    monkeypatch.setattr(broker, "_launch_gateway", lambda exe, host, port, result: launched.append(exe) or dict(result, launched=True))
    monkeypatch.setattr(broker, "_GATEWAY_ABSENT_SINCE", [None])
    return exe


def test_ensure_gateway_never_stacks_a_second_gateway(monkeypatch, tmp_path):
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: ["ibgateway.exe"])
    result = broker.ensure_gateway(launch_if_down=True)
    assert not launched and not result["launched"] and "already running" in result["note"]


def test_ensure_gateway_cooldown_blocks_back_to_back_launches(monkeypatch, tmp_path):
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: [])
    first = broker.ensure_gateway(launch_if_down=True)
    second = broker.ensure_gateway(launch_if_down=True)
    assert len(launched) == 1 and first["launched"] and not second["launched"]
    assert "waiting for sign-in" in second["note"]


def _write_stamp(tmp_path, **values):
    import json
    (tmp_path / "gateway_launch.json").write_text(json.dumps(values), encoding="utf-8")


def _clock(monkeypatch, start=1_000_000.0):
    now = [start]
    monkeypatch.setattr(broker.time, "time", lambda: now[0])
    return now


def test_automatic_launch_never_reopens_a_gateway_closed_before_sign_in(monkeypatch, tmp_path):
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: [])
    now = _clock(monkeypatch)
    # Launched an hour ago; the API was never seen afterwards (window closed / no sign-in).
    _write_stamp(tmp_path, at=now[0] - 3600, api_seen_at=now[0] - 7200, source="launcher")
    for _ in range(3):
        now[0] += 600
        result = broker.ensure_gateway(launch_if_down=True, automatic=True)
        assert not result["launched"] and result.get("automatic_launch_held")
        assert "closed before it signed in" in result["note"]
    assert not launched
    # The owner's button still opens it.
    assert broker.ensure_gateway(launch_if_down=True)["launched"] and len(launched) == 1


def test_automatic_launch_waits_out_gateway_self_restart_then_launches_once(monkeypatch, tmp_path):
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: [])
    now = _clock(monkeypatch)
    # Signed in after its last launch, then went away.
    _write_stamp(tmp_path, at=now[0] - 7200, api_seen_at=now[0] - 3600)
    first = broker.ensure_gateway(launch_if_down=True, automatic=True)
    assert not first["launched"] and "confirming" in first["note"]
    now[0] += broker.GATEWAY_ABSENT_DEBOUNCE_SEC - 1
    assert not broker.ensure_gateway(launch_if_down=True, automatic=True)["launched"]
    now[0] += 2
    assert broker.ensure_gateway(launch_if_down=True, automatic=True)["launched"]
    # That window was then closed without signing in: no further automatic launches.
    for _ in range(3):
        now[0] += 1800
        assert not broker.ensure_gateway(launch_if_down=True, automatic=True)["launched"]
    assert len(launched) == 1


def test_gateway_reappearing_resets_the_absence_debounce(monkeypatch, tmp_path):
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    running = [[]]
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: running[0])
    now = _clock(monkeypatch)
    _write_stamp(tmp_path, at=now[0] - 7200, api_seen_at=now[0] - 3600)
    broker.ensure_gateway(launch_if_down=True, automatic=True)  # absence observed
    running[0] = ["ibgateway.exe"]  # Gateway restarted itself
    now[0] += 60
    broker.ensure_gateway(launch_if_down=True, automatic=True)
    running[0] = []
    now[0] += 60
    assert not broker.ensure_gateway(launch_if_down=True, automatic=True)["launched"]
    assert not launched


def test_port_open_records_sign_in_and_rearms_automatic_relaunch(monkeypatch, tmp_path):
    import json
    launched = []
    _gateway_env(monkeypatch, tmp_path, launched)
    monkeypatch.setattr(broker, "_running_gateway_processes", lambda: [])
    now = _clock(monkeypatch)
    _write_stamp(tmp_path, at=now[0] - 100, source="launcher")
    monkeypatch.setattr(broker, "_port_open", lambda host, port, timeout=0.6: True)
    assert broker.ensure_gateway(launch_if_down=True, automatic=True)["ok"]
    stamp = json.loads((tmp_path / "gateway_launch.json").read_text(encoding="utf-8"))
    assert stamp["api_seen_at"] == now[0] and stamp["source"] == "launcher"
    monkeypatch.setattr(broker, "_port_open", lambda host, port, timeout=0.6: False)
    broker.ensure_gateway(launch_if_down=True, automatic=True)
    now[0] += broker.GATEWAY_ABSENT_DEBOUNCE_SEC + 1
    assert broker.ensure_gateway(launch_if_down=True, automatic=True)["launched"]


def test_java_hosted_gateway_counts_as_running():
    listing = "\n".join([
        r"javaw.exe|C:\Jts\ibgateway\1030\jre\bin\javaw.exe -cp jts4launch.jar ibgateway.GWClient",
        r"java.exe|C:\IBC\java.exe -cp C:\IBC\IBC.jar ibcalpha.ibc.IbcGateway",
        r"java.exe|C:\Program Files\Java\bin\java.exe -jar some-other-app.jar",
    ])
    assert broker._java_gateway_matches(listing) == ["javaw.exe", "java.exe"]
    assert broker._java_gateway_matches("") == []


def test_live_agent_gateway_check_is_automatic():
    from pathlib import Path
    src = Path(broker.__file__).with_name("live_agent.py").read_text(encoding="utf-8-sig")
    assert "ensure(launch_if_down=True, automatic=True)" in src
    assert "ensure(launch_if_down=True)\n" not in src
