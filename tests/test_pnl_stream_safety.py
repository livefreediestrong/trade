"""Adversarial checks for event-driven PnL validity; no broker connection."""
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker


@pytest.fixture
def stream(monkeypatch):
    clock = [100.0]
    day = ["2026-09-23"]
    canceled = []
    connected = [True]
    fake = NS(
        pnlEvent=object(),
        isConnected=lambda: connected[0],
        # Broker time must track the local clock; a skew over 5 s blocks new risk.
        reqCurrentTime=lambda: datetime.now(timezone.utc),
        accountSummary=lambda account: [
            NS(account=account, currency="USD", tag=tag, value=value)
            for tag, value in {
                "NetLiquidation": "110", "TotalCashValue": "110", "BuyingPower": "110"
            }.items()
        ],
        accountValues=lambda account: [],
        reqPnL=lambda account: NS(account=account, modelCode="", dailyPnL=float("nan")),
        cancelPnL=lambda account: canceled.append(account),
        sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    @contextmanager
    def session():
        yield fake

    monkeypatch.setattr(broker, "_session", session)
    monkeypatch.setattr(broker, "_identity", lambda ib: {"account_id": "TEST", "paper_mode": False})
    monkeypatch.setattr(broker, "_PNL", {})
    monkeypatch.setattr(broker, "_PNL_UPDATED", {})
    monkeypatch.setattr(broker, "_ACCOUNT_READY", {})
    monkeypatch.setattr(broker, "_API_PULSE", {})
    monkeypatch.setattr(broker, "_VERIFIED", {})
    monkeypatch.setattr(broker, "_CLIENT", fake)
    monkeypatch.setattr(broker, "_SERVER_UNAVAILABLE", False)
    monkeypatch.setattr(broker, "_RESYNC_REQUIRED", False)
    monkeypatch.setattr(broker, "_CONNECTION", {"connected": True, "error": None})
    monkeypatch.setattr(broker, "_PNL_WATCH", {
        "ui_status": None, "last_error_code": None, "soft_reconnect_count": 0,
        "last_soft_reconnect_at": 0.0, "last_scheduled_refresh_at": 0.0,
        "pending_soft_reconnect": False, "pending_reason": None,
    })
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.0)
    monkeypatch.setattr(broker, "_PNL_SILENT_RETRY_SEC", 60.0)
    monkeypatch.setattr(broker, "_PNL_FIRST_CALLBACK_WAIT_SEC", 3.0)
    monkeypatch.setattr(broker, "_PNL_SOFT_RECONNECT_WAIT_SEC", 90.0)
    monkeypatch.setattr(broker, '_ACCOUNT_UNSUBSCRIBED', False)
    monkeypatch.setattr(broker, '_ACCOUNT_RESUBSCRIBE_AFTER', 0.0)
    monkeypatch.setattr(broker, "_pnl_day", lambda: day[0])
    monkeypatch.setattr(broker.time, "monotonic", lambda: clock[0])

    def account():
        return broker.get_account.__wrapped__()

    def callback(value):
        pnl = broker._PNL[(id(fake), "TEST")]["value"]
        pnl.dailyPnL = value
        broker._pnl_update(pnl)
        return pnl

    def ready(value, account="TEST", model=""):
        broker._account_value_update(NS(account=account, tag="AccountReady",
                                       value=value, currency="", modelCode=model))

    return NS(fake=fake, clock=clock, day=day, canceled=canceled, connected=connected,
              account=account, callback=callback, ready=ready)


def assert_blocked(result):
    assert not result.get("risk_ready", False)
    assert result.get("account", {}).get("day_pnl") is None


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), 1.7976931348623157e308])
def test_unusable_callback_does_not_reset_recovery_backoff(stream, value, monkeypatch):
    stream.account()
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", stream.clock[0] + 600)
    monkeypatch.setattr(broker, "_ib", lambda: pytest.fail("Unexpected broker connection"))
    broker._PNL_WATCH.update(soft_reconnect_count=4, pending_soft_reconnect=True,
                             pending_reason="silent", last_error_code=2100,
                             last_scheduled_refresh_at=25.)
    stream.callback(value)
    assert_blocked(stream.account())
    assert broker._PNL_WATCH["soft_reconnect_count"] == 4
    assert broker._PNL_WATCH["pending_soft_reconnect"] is True
    assert broker._PNL_WATCH["pending_reason"] == "silent"
    assert broker._PNL_WATCH["last_error_code"] == 2100
    assert broker._PNL_WATCH["last_scheduled_refresh_at"] == 25.


@pytest.mark.parametrize("value", [0., -12.5])
def test_usable_callback_completes_recovery(stream, value):
    stream.account()
    broker._PNL_WATCH.update(soft_reconnect_count=4, pending_soft_reconnect=True,
                             pending_reason="silent", last_error_code=2100)
    stream.callback(value)
    result = stream.account()
    assert result["risk_ready"] and result["account"]["day_pnl"] == value
    assert broker._PNL_WATCH["soft_reconnect_count"] == 0
    assert broker._PNL_WATCH["pending_soft_reconnect"] is False
    assert broker._PNL_WATCH["pending_reason"] is None
    assert broker._PNL_WATCH["last_error_code"] is None


def test_silent_subscription_survives_reconnect_cooldown(stream, monkeypatch):
    stream.account()
    original = broker._PNL[(id(stream.fake), "TEST")]["value"]
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", stream.clock[0] + 600)
    monkeypatch.setattr(broker, "_arm_soft_reconnect", lambda *a: pytest.fail("Reconnect during cooldown"))
    for _ in range(3):
        stream.clock[0] += 61
        result = stream.account()
        assert_blocked(result)
        assert result["pnl_diagnostics"]["api_responsive"] is True
        assert broker._PNL[(id(stream.fake), "TEST")]["value"] is original
        assert stream.canceled == []
    # A delayed real callback on that same request can still restore readiness.
    stream.callback(-8.)
    assert stream.account()["account"]["day_pnl"] == -8.


def test_silent_subscription_can_retry_when_cooldown_expires(stream, monkeypatch):
    stream.account()
    original = broker._PNL[(id(stream.fake), "TEST")]["value"]
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", stream.clock[0] + 600)
    stream.clock[0] += 600
    result = stream.account()
    assert_blocked(result)
    assert stream.canceled == ["TEST"]
    assert result["pnl_diagnostics"]["retries"] == 1
    assert broker._PNL[(id(stream.fake), "TEST")]["value"] is not original


def test_silent_pnl_still_checks_api_without_manufacturing_a_value(stream):
    result = stream.account()
    assert_blocked(result)
    assert result["pnl_diagnostics"]["api_responsive"] is True
    assert result["pnl_diagnostics"]["api_response_age_seconds"] == 0
    assert result["pnl_diagnostics"]["callbacks"] == 0


def test_failed_api_probe_is_bounded_and_does_not_reuse_last_success(stream):
    stream.account()
    stream.callback(-10.)
    assert stream.account()["risk_ready"]
    attempts = []
    def timeout():
        attempts.append(True)
        raise TimeoutError("private broker wire text")
    stream.fake.reqCurrentTime = timeout
    stream.clock[0] += 16
    for _ in range(4):
        result = stream.account()
        assert_blocked(result)
        assert result["pnl_diagnostics"]["api_probe_error"] == "TimeoutError"
        assert result["pnl_diagnostics"]["api_response_age_seconds"] is None
        assert "private broker" not in str(result)
    assert len(attempts) == 1
    stream.clock[0] += 16
    stream.fake.reqCurrentTime = lambda: datetime.now(timezone.utc)
    assert stream.account()["account"]["day_pnl"] == -10.


def test_probe_callback_during_missing_pnl_does_not_bypass_outage(stream):
    def loss():
        stream.callback(0.)
        broker._server_error(-1, 1100, "lost")
        return datetime.now(timezone.utc)
    stream.fake.reqCurrentTime = loss
    assert_blocked(stream.account())
    assert not broker._PNL_UPDATED


def test_unchanged_negative_pnl_survives_three_minutes_without_retry(stream):
    assert_blocked(stream.account())
    stream.callback(-12.50)
    first = stream.account()
    assert first["risk_ready"] and first["account"]["day_pnl"] == -12.50
    original_stamp = broker._PNL_UPDATED["TEST"]
    original_request = broker._PNL[(id(stream.fake), "TEST")]["value"]

    stream.clock[0] += 180
    later = stream.account()
    assert later["risk_ready"] and later["account"]["day_pnl"] == -12.50
    assert stream.canceled == []
    assert broker._PNL_UPDATED["TEST"] == original_stamp
    assert broker._PNL[(id(stream.fake), "TEST")]["value"] is original_request
    assert later["pnl_diagnostics"]["callbacks"] == 1


def test_transient_account_reset_requires_post_ready_callback(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.ready("false")
    stream.callback(0.0)  # A queued update during reset must not grant readiness.
    stream.ready("true")  # Both account events occur between dashboard reads.
    assert_blocked(stream.account())
    stream.callback(-12.50)
    assert stream.account()["account"]["day_pnl"] == -12.50


def test_other_account_or_model_readiness_does_not_revoke_selected_account(stream):
    stream.account()
    stream.callback(-12.50)
    stream.ready("false", account="OTHER")
    stream.ready("false", model="MODEL")
    result = stream.account()
    assert result["risk_ready"] and result["account"]["day_pnl"] == -12.50


def test_previous_day_callback_cannot_authorize_current_day(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.day[0] = "2026-09-24"
    assert_blocked(stream.account())
    stream.callback(0.0)
    assert stream.account()["risk_ready"]


def test_day_rollover_replacement_rejects_late_previous_subscription_callback(stream):
    stream.account()
    previous = stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 86400
    stream.day[0] = "2026-09-24"
    assert_blocked(stream.account())
    assert stream.canceled == ["TEST"]
    previous.dailyPnL = 0.0
    broker._pnl_update(previous)
    assert_blocked(stream.account())
    stream.callback(0.0)
    assert stream.account()["risk_ready"]


def test_timed_out_api_pulse_blocks_unchanged_value(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def timeout():
        raise TimeoutError("Gateway heartbeat timed out")

    stream.fake.reqCurrentTime = timeout
    assert_blocked(stream.account())


@pytest.mark.parametrize("loss_code", [1100, 2110, 1101])
def test_outage_during_successful_api_pulse_cannot_grant_readiness(stream, loss_code):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def pulse_with_outage():
        broker._server_error(-1, loss_code, "upstream loss during heartbeat")
        return datetime.now(timezone.utc)

    stream.fake.reqCurrentTime = pulse_with_outage
    assert_blocked(stream.account())


@pytest.mark.parametrize("value", [float("nan"), 1.7976931348623157e308])
def test_unavailable_callback_during_api_pulse_revokes_prior_number(stream, value):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def pulse_with_invalid_pnl():
        stream.callback(value)
        return datetime.now(timezone.utc)

    stream.fake.reqCurrentTime = pulse_with_invalid_pnl
    assert_blocked(stream.account())


def test_new_loss_callback_during_api_pulse_returns_latest_loss(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def pulse_with_larger_loss():
        stream.callback(-55.0)
        return datetime.now(timezone.utc)

    stream.fake.reqCurrentTime = pulse_with_larger_loss
    result = stream.account()
    assert result["risk_ready"] and result["account"]["day_pnl"] == -55.0


def test_subscription_replaced_during_api_pulse_cannot_validate_old_object(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def pulse_with_replacement():
        key = (id(stream.fake), "TEST")
        replacement = dict(broker._PNL[key])
        replacement["value"] = NS(account="TEST", modelCode="", dailyPnL=-55.0)
        broker._PNL[key] = replacement
        broker._pnl_update(replacement["value"])
        return datetime.now(timezone.utc)

    stream.fake.reqCurrentTime = pulse_with_replacement
    assert_blocked(stream.account())


def test_account_reset_during_api_pulse_cannot_restore_old_validity(stream):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.clock[0] += 31

    def pulse_with_reset():
        stream.ready("false")
        stream.callback(0.0)
        stream.ready("true")
        return datetime.now(timezone.utc)

    stream.fake.reqCurrentTime = pulse_with_reset
    assert_blocked(stream.account())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.7976931348623157e308])
def test_unavailable_callback_cannot_reuse_previous_numeric_pnl(stream, value):
    stream.account()
    stream.callback(-12.50)
    assert stream.account()["risk_ready"]
    stream.callback(value)
    assert_blocked(stream.account())


def test_no_initial_or_wrong_callback_cannot_grant_readiness(stream):
    assert_blocked(stream.account())
    for account, model in [("OTHER", ""), ("TEST", ""), ("TEST", "MODEL")]:
        broker._pnl_update(NS(account=account, modelCode=model, dailyPnL=0.0))
    active = broker._PNL[(id(stream.fake), "TEST")]["value"]
    active.modelCode = "MODEL"
    active.dailyPnL = 0.0
    broker._pnl_update(active)
    active.modelCode = ""
    assert_blocked(stream.account())
    assert not broker._PNL_UPDATED
