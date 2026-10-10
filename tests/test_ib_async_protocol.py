"""Real replacement-library framing/decoder, no socket or real broker."""
import asyncio
import struct
import pytest
from ib_async import IB
import broker_ibkr as broker


@pytest.fixture
def wire(monkeypatch, tmp_path):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    client = IB()
    client.client._serverVersion = 178
    client.client._apiReady = True
    client.client.updateReqId(10)
    sent = []
    monkeypatch.setattr(client.client, "send", lambda *fields: sent.append(fields))
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    for name in ("_PNL", "_PNL_UPDATED", "_ACCOUNT_READY", "_ACCOUNT_EQUITY", "_API_PULSE", "_VERIFIED"):
        monkeypatch.setattr(broker, name, {})
    monkeypatch.setattr(broker, "_PNL_WATCH", {})
    monkeypatch.setattr(broker, "_SERVER_UNAVAILABLE", False)
    monkeypatch.setattr(broker, "_RESYNC_REQUIRED", False)
    client.pnlEvent += broker._pnl_update
    client.disconnectedEvent += broker._disconnected

    def frame(req, value):
        body = ("\0".join(map(str, (94, req, value, 0, 0))) + "\0").encode()
        return struct.pack(">I", len(body)) + body

    def subscribe():
        value = client.reqPnL("DU-TEST")
        broker._PNL[(id(client), "DU-TEST")] = {"value": value, "callbacks": 0}
        return client.wrapper.pnlKey2ReqId[("DU-TEST", "")]

    yield client, sent, frame, subscribe
    client.wrapper.reset()
    loop.close()
    asyncio.set_event_loop(None)


def test_split_wire_frame_does_not_freshen_until_complete(wire):
    client, sent, frame, subscribe = wire
    req = subscribe()
    assert sent == [(92, req, "DU-TEST", "")]
    data = frame(req, -2.75)
    client.client._onSocketHasData(data[:7])
    assert not broker._PNL_UPDATED
    client.client._onSocketHasData(data[7:])
    assert broker._PNL_UPDATED["DU-TEST"] > 0
    sub = broker._PNL[(id(client), "DU-TEST")]
    assert sub["callbacks"] == 1 and sub["value"].dailyPnL == -2.75


def test_cancel_replace_ignores_old_wire_reply_and_disconnect_erases_receipt(wire):
    client, sent, frame, subscribe = wire
    old = subscribe()
    client.cancelPnL("DU-TEST")
    assert sent[-1] == (93, old)
    new = subscribe()
    assert new != old
    client.client._onSocketHasData(frame(old, 99))
    assert not broker._PNL_UPDATED
    client.client._onSocketHasData(frame(new, -5))
    assert broker._PNL_UPDATED
    client.disconnectedEvent.emit()
    assert not broker._PNL and not broker._PNL_UPDATED and not broker._API_PULSE


@pytest.mark.parametrize("value", ["nan", "inf", "1.7976931348623157e308"])
def test_unusable_real_wire_callback_does_not_reset_backoff(wire, value):
    client, _, frame, subscribe = wire
    req = subscribe()
    broker._PNL_WATCH.update(soft_reconnect_count=4, pending_soft_reconnect=True)
    client.client._onSocketHasData(frame(req, value))
    assert broker._PNL_WATCH["soft_reconnect_count"] == 4
    assert broker._PNL_WATCH["pending_soft_reconnect"] is True
