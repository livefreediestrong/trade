"""Explicit broker identity and market-time fixtures, without broker I/O."""
from datetime import datetime, timezone, timedelta
import app as desk
import broker_alpaca as alpaca
import data_sources as ds

IDENTITY = {"broker": "alpaca", "account_id": "TEST_ACCOUNT", "paper_mode": True,
            "endpoint": "https://paper-api.alpaca.markets"}


def fresh_quote(ticker, price=100.):
    desk._QUOTE_SNAPSHOTS[ticker] = ds.quote_snapshot(price, datetime.now(timezone.utc), "fixture")
    return price


def configure(monkeypatch, cfg):
    cfg["broker_identity"] = dict(IDENTITY)
    monkeypatch.setattr(alpaca, "verify_execution_context", lambda: {"ok": True, "identity": dict(IDENTITY)})
    monkeypatch.setattr(desk, "_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(desk, "fetch_last_price", fresh_quote)
    monkeypatch.setattr(desk, "_BROKER_REVIEWS", {})


def approval(client, signal_id):
    response = client.post(f"/api/signals/{signal_id}/review", base_url="http://127.0.0.1:5056", json={})
    assert response.status_code == 200, response.get_json()
    return {"review_token": response.get_json()["review_token"], "ack_ticker": "TEST"}


def deadline():
    return (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
