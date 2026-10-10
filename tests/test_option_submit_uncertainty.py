"""Exercise the actual IBKR option adapters at the socket-send boundary."""
from datetime import datetime, timedelta, timezone

import pytest
import broker_ibkr as broker
import app as desk
from test_broker_lifecycle import gateway, execution


def ticket(asset, identity):
    row = {"ticker": "SPY", "side": "buy", "shares": 1, "contracts": 1,
           "asset_type": asset, "right": "C", "expiry": "2030-10-18", "strike": 100.,
           "long_strike": 100., "short_strike": 105., "option_strategy": "call_debit" if asset == "BAG" else "long_call",
           "option_intent": "OPEN" if asset == "BAG" else "BTO", "type": "limit", "limit": 1.,
           "signal_id": "ambiguous-options", "broker_identity": identity,
           "valid_until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
           "risk_authorization": {"reducing": False, "day_pnl": 0., "equity": 100000.}}
    return row


@pytest.mark.parametrize("asset", ["OPT", "BAG"])
def test_lost_acknowledgement_remains_pending_and_prevents_second_submit(gateway, execution, monkeypatch, asset):
    identity = broker.verify_execution_context()["identity"]
    monkeypatch.setattr(broker, "_submission_risk_error", lambda *a: None)
    monkeypatch.setattr(gateway, "openTrades", lambda: [], raising=False)
    def qualify(c):
        c.conId = int(c.strike)
        return [c]
    monkeypatch.setattr(gateway, "qualifyContracts", qualify)
    attempts = []
    def lost_ack(contract, native):
        attempts.append((contract, native))
        raise ConnectionError("Socket closed after send")
    monkeypatch.setattr(gateway, "placeOrder", lost_ack)
    order = ticket(asset, identity)
    response = broker.place_from_desk_order(order)
    assert not response["ok"] and response["submission_attempted"] is True, response.get("error")
    assert len(attempts) == 1
    # Exercise the ledger's definitive/uncertain handling with that real adapter
    # outcome, then a second attempt: it must be stopped before broker dispatch.
    cfg, sig, sent = execution
    cfg["broker_identity"] = identity
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: response)
    first = desk.execute_gated_broker_or_paper(sig, cfg, source="test", via="test")
    assert first.get("pending") is True, first
    pending = desk.load_ledger()["pending_broker_orders"]
    assert len(pending) == 1
    second = desk.execute_gated_broker_or_paper(sig, cfg, source="test", via="test")
    assert "unresolved" in second["error"]
    assert len(attempts) == 1


@pytest.mark.parametrize("asset", ["OPT", "BAG"])
def test_failure_before_send_can_be_definitive(gateway, monkeypatch, asset):
    identity = broker.verify_execution_context()["identity"]
    monkeypatch.setattr(gateway, "qualifyContracts", lambda c: [])
    response = broker.place_from_desk_order(ticket(asset, identity))
    assert not response["ok"] and response["submission_attempted"] is False
