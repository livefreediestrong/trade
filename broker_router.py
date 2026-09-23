"""Select the configured broker while preserving the desk broker interface."""
from __future__ import annotations

import os


def _module():
    # Keep the existing Alpaca-focused unit tests isolated from a developer's
    # local live-broker .env; production never sets PYTEST_CURRENT_TEST.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        import broker_alpaca
        return broker_alpaca
    if (os.environ.get("BROKER_PROVIDER", "alpaca") or "alpaca").strip().lower() == "ibkr":
        import broker_ibkr
        return broker_ibkr
    import broker_alpaca
    return broker_alpaca


def __getattr__(name):
    return getattr(_module(), name)
