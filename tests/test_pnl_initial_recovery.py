"""Bounded read-only input refresh after a retained P&L observer is attached."""
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker
from test_pnl_stream_safety import stream, assert_blocked


@pytest.fixture
def refresh(stream):
    calls = []
    original_request = stream.fake.reqPnL
    def request(account):
        calls.append(("pnl", account))
        return original_request(account)
    def positions():
        assert (id(stream.fake), "TEST") in broker._PNL
        assert stream.clock[0] >= 103.
        calls.append(("positions",))
        return []
    stream.fake.reqPnL = request
    stream.fake.reqPositions = positions
    stream.fake.client = NS(reqAccountUpdates=lambda subscribed, account: calls.append(("raw_account", subscribed, account)))
    stream.fake.reqAccountUpdates = lambda account: calls.append(("account", account))
    stream.calls = calls
    return stream


def test_refresh_follows_subscription_and_each_stage_runs_once(refresh):
    assert_blocked(refresh.account())
    assert refresh.calls == [("pnl", "TEST"), ("positions",)]
    refresh.clock[0] += 14
    assert_blocked(refresh.account())
    assert len(refresh.calls) == 2
    refresh.clock[0] += 1.1
    result = refresh.account()
    assert_blocked(result)
    assert refresh.calls[-1:] == [("account", "TEST")]
    assert result["pnl_diagnostics"]["initial_recovery"] == {
        "positions": "completed", "account_download": "completed"}
    for _ in range(3):
        assert_blocked(refresh.account())
    assert len(refresh.calls) == 3
    assert not broker._PNL_UPDATED  # Completed downloads never invent zero P&L.


@pytest.mark.parametrize("stage", ["positions", "account"])
def test_actual_callback_during_refresh_can_recover_pnl(refresh, stage):
    def callback(*args):
        refresh.callback(-2.75)
    if stage == "positions":
        refresh.fake.reqPositions = callback
    else:
        assert_blocked(refresh.account())
        refresh.clock[0] += 16
        refresh.fake.reqAccountUpdates = callback
    result = refresh.account()
    assert result["risk_ready"] and result["account"]["day_pnl"] == -2.75
    assert result["pnl_diagnostics"]["callbacks"] == 1
    stamp = broker._PNL_UPDATED["TEST"]
    calls = list(refresh.calls)
    refresh.clock[0] += 180
    assert refresh.account()["risk_ready"]
    assert refresh.calls == calls and refresh.canceled == []
    assert broker._PNL_UPDATED["TEST"] == stamp


def test_healthy_callback_in_initial_wait_skips_all_refreshes(refresh):
    def pump(seconds):
        refresh.clock[0] += seconds
        refresh.callback(0.)
    refresh.fake.sleep = pump
    assert refresh.account()["risk_ready"]
    assert refresh.calls == [("pnl", "TEST")]
    refresh.clock[0] += 180
    assert refresh.account()["risk_ready"]
    assert refresh.calls == [("pnl", "TEST")]


def test_refresh_timeouts_stay_blocked_and_do_not_repeat(refresh):
    attempts = []
    def timeout(*args):
        attempts.append(args)
        raise TimeoutError("snapshot timed out")
    refresh.fake.reqPositions = timeout
    assert_blocked(refresh.account())
    assert_blocked(refresh.account())
    assert len(attempts) == 1
    refresh.clock[0] += 16
    refresh.fake.reqAccountUpdates = timeout
    result = refresh.account()
    assert_blocked(result)
    assert result["pnl_diagnostics"]["initial_recovery"] == {
        "positions": "failed", "account_download": "failed", "last_error": "snapshot timed out"}
    refresh.clock[0] = 161.
    assert_blocked(refresh.account())
    assert len(attempts) == 2  # The replacement P&L request does not reset stages.


def test_stages_survive_retry_and_old_callbacks_cannot_recover(refresh):
    assert_blocked(refresh.account())
    old = broker._PNL[(id(refresh.fake), "TEST")]["value"]
    refresh.clock[0] += 16
    assert_blocked(refresh.account())
    state = broker._PNL[(id(refresh.fake), "TEST")]["initial_recovery"]
    refresh.clock[0] = 161.
    result = refresh.account()
    assert_blocked(result)
    assert result["pnl_diagnostics"]["retries"] == 1
    assert broker._PNL[(id(refresh.fake), "TEST")]["initial_recovery"] is state
    assert refresh.calls.count(("positions",)) == 1
    assert refresh.calls.count(("account", "TEST")) == 1
    old.dailyPnL = 0.
    broker._pnl_update(old)
    assert_blocked(refresh.account())
    assert not broker._PNL_UPDATED
    refresh.callback(-3.)
    assert refresh.account()["account"]["day_pnl"] == -3.


def test_server_loss_during_refresh_cannot_publish_callback(refresh):
    def outage():
        broker._server_error(-1, 1100, "lost")
        refresh.callback(0.)
    refresh.fake.reqPositions = outage
    result = refresh.account()
    assert_blocked(result)
    assert result["pnl_diagnostics"]["status"] == "recovering"
    assert not broker._PNL_UPDATED


def test_unsupported_refresh_methods_are_skipped(stream):
    assert_blocked(stream.account())
    stream.clock[0] += 16
    result = stream.account()
    assert_blocked(result)
    assert result["pnl_diagnostics"]["initial_recovery"] == {
        "positions": "unsupported", "account_download": "unsupported"}


def test_account_refresh_does_not_create_its_own_2100_recovery(refresh):
    def raw(subscribed, account):
        if not subscribed:
            broker._server_error(-1, 2100, "API client has been unsubscribed from account data")
    refresh.fake.client.reqAccountUpdates = raw
    assert_blocked(refresh.account())
    original = broker._PNL[(id(refresh.fake), "TEST")]["value"]
    refresh.clock[0] += 16
    assert_blocked(refresh.account())
    assert not broker._ACCOUNT_UNSUBSCRIBED
    assert not broker._PNL_WATCH.get("pending_resubscribe")
    assert refresh.canceled == []
    assert broker._PNL[(id(refresh.fake), "TEST")]["value"] is original
    refresh.callback(-3.5)
    assert refresh.account()["account"]["day_pnl"] == -3.5


@pytest.mark.parametrize("soft", [False, True])
def test_refresh_during_1100_preserves_socket_and_stays_blocked(refresh, soft):
    assert_blocked(refresh.account())
    refresh.callback(-5.)
    assert refresh.account()["risk_ready"]
    original = broker._PNL[(id(refresh.fake), "TEST")]["value"]
    broker._server_error(-1, 1100, "upstream connection lost")
    result = broker.refresh_broker_pnl.__wrapped__(soft_reconnect=soft)
    assert_blocked(result)
    assert result["refresh"]["held"] and result["refresh"]["actions"] == []
    assert broker._CLIENT is refresh.fake and refresh.canceled == []
    assert broker._PNL[(id(refresh.fake), "TEST")]["value"] is original
    broker._server_error(-1, 1102, "data maintained")
    assert_blocked(refresh.account())  # Restored connection is not a new P&L sample.
    refresh.callback(-8.)
    assert refresh.account()["account"]["day_pnl"] == -8.


def test_overdue_scheduler_waits_through_upstream_recovery(refresh, monkeypatch):
    calls = []
    monkeypatch.setattr(broker, "refresh_broker_pnl", lambda **kw: calls.append(kw) or {"ok": False})
    broker._PNL_WATCH["last_scheduled_refresh_at"] = 1.
    refresh.clock[0] = 600.
    broker._server_error(-1, 1100, "upstream connection lost")
    kwargs = dict(interval_minutes=5, auto_live=True, risk_ready=False)
    assert broker.maybe_scheduled_soft_refresh(**kwargs) is None
    broker._server_error(-1, 1102, "data maintained")
    refresh.clock[0] += 2
    assert broker.maybe_scheduled_soft_refresh(**kwargs) is None
    assert calls == []
    # A different recovery path just reconnected; do not immediately bounce it.
    broker._PNL_WATCH["last_soft_reconnect_at"] = 895.
    refresh.clock[0] = 901.
    assert broker.maybe_scheduled_soft_refresh(**kwargs) is None
    refresh.clock[0] = 1196.
    assert broker.maybe_scheduled_soft_refresh(**kwargs) == {"ok": False}
    assert calls == [{"soft_reconnect": True}]
