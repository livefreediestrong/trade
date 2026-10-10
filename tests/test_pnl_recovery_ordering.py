"""Replay retained-subscription recovery without sockets or broker orders."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import struct

import pytest

import broker_ibkr as broker
from test_pnl_stream_safety import stream, assert_blocked
from test_ib_async_protocol import wire


def restore(stream, code=1100):
    stream.account()
    original = stream.callback(-12.5)
    stream.clock[0] += 300  # The initial request is already past its retry age.
    broker._server_error(-1, code, "lost")
    stream.clock[0] += 2.521
    stream.callback(-13.)  # Captured ordering: callback before restored notice.
    stream.clock[0] += 0.171
    broker._server_error(-1, 1102, "restored; data maintained")
    return original


@pytest.mark.parametrize("code", [1100, 2110])
def test_restore_preserves_subscription_and_requires_post_restore_callback(stream, code):
    original = restore(stream, code)
    stream.clock[0] += 4
    result = stream.account()
    assert_blocked(result)
    assert stream.canceled == []
    assert broker._PNL[(id(stream.fake), "TEST")]["value"] is original
    assert result["pnl_diagnostics"]["subscription_age_seconds"] > 300
    assert result["pnl_diagnostics"]["recovery_wait_seconds"] > 0
    assert "Connection restored; waiting" in result["risk_error"]
    assert not broker._PNL_UPDATED
    stream.callback(-15.)
    assert stream.account()["account"]["day_pnl"] == -15.


def test_pending_reconnect_cannot_cancel_just_restored_subscription(stream, monkeypatch):
    original = restore(stream)
    broker._PNL_WATCH.update(pending_soft_reconnect=True, pending_reason="silent")
    monkeypatch.setattr(broker, "_arm_soft_reconnect", lambda *a: pytest.fail("premature reconnect"))
    assert_blocked(stream.account())
    assert broker._PNL[(id(stream.fake), "TEST")]["value"] is original
    assert broker._PNL_WATCH["pending_soft_reconnect"] is True


def test_duplicate_restored_notices_do_not_extend_wait_forever(stream):
    restore(stream)
    stream.clock[0] += broker._PNL_SILENT_RETRY_SEC - 1
    broker._server_error(-1, 1102, "duplicate restored notice")
    assert_blocked(stream.account())
    assert stream.canceled == []
    stream.clock[0] += 2
    assert_blocked(stream.account())
    assert stream.canceled == ["TEST"]


def test_unsolicited_restore_does_not_delay_existing_silent_retry(stream):
    stream.account()
    stream.clock[0] += 300
    broker._server_error(-1, 1102, "no preceding loss")
    assert_blocked(stream.account())
    assert stream.canceled == ["TEST"]


def test_data_lost_resync_takes_priority_over_retained_notice(stream):
    stream.account()
    broker._server_error(-1, 1100, "lost")
    broker._server_error(-1, 1101, "restored; data lost")
    broker._server_error(-1, 1102, "retained")
    assert broker._RESYNC_REQUIRED
    assert "restored_at" not in broker._PNL[(id(stream.fake), "TEST")]
    stream.callback(-1.)
    assert not broker._PNL_UPDATED


@pytest.mark.parametrize("state", ["disconnected", "account_unsubscribed", "other_client"])
def test_retention_window_requires_same_usable_subscription(stream, state, monkeypatch):
    stream.account()
    broker._server_error(-1, 1100, "lost")
    if state == "disconnected":
        stream.connected[0] = False
    elif state == "account_unsubscribed":
        broker._server_error(-1, 2100, "account feed lost")
    else:
        monkeypatch.setattr(broker, "_CLIENT", type(stream.fake)(isConnected=lambda: True))
    broker._server_error(-1, 1102, "retained")
    assert "restored_at" not in broker._PNL[(id(stream.fake), "TEST")]
    assert not broker._PNL_UPDATED


def test_real_decoder_replays_observed_recovery_without_cancel_or_old_readiness(wire, monkeypatch):
    client, sent, frame, subscribe = wire
    clock = [100.]
    monkeypatch.setattr(broker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(broker, "_pnl_day", lambda: "2026-09-26")
    monkeypatch.setattr(broker, "_CLIENT", client)
    monkeypatch.setattr(broker, "_ACCOUNT_UNSUBSCRIBED", False)
    monkeypatch.setattr(broker, "_SOFT_RECONNECT_AFTER", 0.)
    client.errorEvent += broker._server_error
    # Keep the library's 1102 dispatch, but stub its automatic summary request.
    async def summary():
        return []
    monkeypatch.setattr(client, "reqAccountSummaryAsync", summary)
    request = subscribe()
    original = broker._PNL[(id(client), "DU-TEST")]
    original.update(requested_at=100., started_at=100.)
    client.client._onSocketHasData(frame(request, -12.5))

    def error(code):
        body = ("\0".join(map(str, (4, 2, -1, code, "test event", ""))) + "\0").encode()
        client.client._onSocketHasData(struct.pack(">I", len(body)) + body)

    clock[0] = 400.
    error(1100)
    clock[0] = 402.521
    client.client._onSocketHasData(frame(request, -13.))
    assert not broker._PNL_UPDATED
    clock[0] += .171
    error(1102)
    asyncio.get_event_loop().run_until_complete(asyncio.sleep(0))

    from types import SimpleNamespace as NS
    @contextmanager
    def session():
        yield client
    monkeypatch.setattr(broker, "_session", session)
    monkeypatch.setattr(broker, "_identity", lambda ib: {"account_id": "DU-TEST", "paper_mode": True})
    monkeypatch.setattr(client, "accountSummary", lambda account: [
        NS(account=account, currency="USD", tag=k, value="110")
        for k in ("NetLiquidation", "TotalCashValue", "BuyingPower")])
    monkeypatch.setattr(client, "accountValues", lambda account: [])
    monkeypatch.setattr(client, "reqCurrentTime", lambda: datetime.now(timezone.utc))
    original["initial_recovery"] = {"positions_at": 100., "account_download": "completed"}
    clock[0] += 4.
    result = broker.get_account.__wrapped__()
    assert_blocked(result)
    assert sent == [(92, request, "DU-TEST", "")]
    assert broker._PNL[(id(client), "DU-TEST")] is original
    client.client._onSocketHasData(frame(request, -20.))
    result = broker.get_account.__wrapped__()
    assert result["risk_ready"] and result["account"]["day_pnl"] == -20.
