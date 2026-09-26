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
