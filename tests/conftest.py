"""Tests select their broker explicitly; production routing has no pytest branch."""
import os
import tempfile

import pytest

os.environ["TOMAHAWK_NO_BG"] = "1"
os.environ.setdefault("TOMAHAWK_DATA_DIR", tempfile.mkdtemp(prefix="tomahawk_tests_"))
# Prevent local credentials/provider selection from turning mocks into real calls.
for key in ("ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_SECRET_KEY"):
    os.environ[key] = ""
os.environ["BROKER_PROVIDER"] = "alpaca"


@pytest.fixture(autouse=True)
def paper_broker_environment(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "alpaca")
    monkeypatch.setenv("ALPACA_PAPER", "true")


@pytest.fixture(autouse=True)
def fresh_screener_caches():
    import screener_logic
    screener_logic._SECTOR_CACHE.clear()
    screener_logic._EARNINGS_CACHE.clear()


@pytest.fixture(autouse=True)
def no_flat_pnl_derivation(request, monkeypatch):
    """Existing P&L tests model a silent IBKR feed; the flat-account $0 rule is opt-in."""
    if "flat_pnl" in request.keywords:
        return
    import broker_ibkr
    monkeypatch.setattr(broker_ibkr, "_flat_day_evidence", lambda ib, account: {"flat": False, "reason": "test_default"})
    monkeypatch.setattr(broker_ibkr, "_flat_evidence_cached", lambda ib, account: False)


@pytest.fixture(autouse=True)
def promotion_gate_passes_by_default(request, monkeypatch):
    """Order-path tests exercise the other gates; the evidence lock is tested on its own (opt-in)."""
    if "promotion_gate" in request.keywords:
        return
    import live_agent
    monkeypatch.setattr(live_agent.LiveAgent, "promotion",
                        lambda self, cfg: {"passed": True, "plain": "test default", "missing": []})
