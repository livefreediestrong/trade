"""Focused IBKR profile/readiness tests; all connection calls are mocked."""
from __future__ import annotations

import importlib


def _ibkr(monkeypatch, **env):
    for name in ("BROKER_PROVIDER", "IB_GATEWAY_HOST", "IB_GATEWAY_PORT", "IBKR_LIVE", "IB_MANAGED_ACCOUNT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    import broker_ibkr

    return importlib.reload(broker_ibkr)


def test_profile_rejects_live_port_mismatch(monkeypatch):
    ibkr = _ibkr(monkeypatch, BROKER_PROVIDER="ibkr", IBKR_LIVE="true", IB_GATEWAY_PORT="4002")
    status = ibkr.public_status()
    assert status["capability_state"] == "profile_invalid"
    assert status["can_submit"] is False
    assert status["diagnostics"][0]["code"] == "gateway_environment_mismatch"


def test_profile_rejects_remote_gateway(monkeypatch):
    ibkr = _ibkr(monkeypatch, BROKER_PROVIDER="ibkr", IB_GATEWAY_HOST="10.0.0.5", IB_GATEWAY_PORT="4002")
    assert ibkr.connection_diagnostics()["diagnostics"][0]["code"] == "gateway_host_not_local"


def test_multiple_accounts_require_explicit_selection(monkeypatch):
    ibkr = _ibkr(monkeypatch, BROKER_PROVIDER="ibkr")

    class FakeIb:
        def managedAccounts(self):
            return ["DU2", "DU1"]

    try:
        ibkr._selected_account(FakeIb())
    except RuntimeError as exc:
        assert "IB_MANAGED_ACCOUNT" in str(exc)
    else:
        raise AssertionError("ambiguous managed accounts must fail closed")


def test_idempotent_submission_reuses_inflight_result(monkeypatch):
    ibkr = _ibkr(monkeypatch, BROKER_PROVIDER="ibkr")
    monkeypatch.setattr(ibkr, "_INFLIGHT", {"signal-1": {"ok": True, "order_id": "42", "status": "paper_submitted"}})
    result = ibkr.place_from_desk_order({"signal_id": "signal-1", "ticker": "AAPL", "shares": 1, "side": "buy"})
    assert result["order_id"] == "42"
    assert result["idempotent_replay"] is True
