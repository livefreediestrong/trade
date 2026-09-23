"""Select the configured broker while preserving the desk broker interface."""
from __future__ import annotations

import os


def _module():
    if (os.environ.get("BROKER_PROVIDER", "alpaca") or "alpaca").strip().lower() == "ibkr":
        import broker_ibkr
        return broker_ibkr
    import broker_alpaca
    return broker_alpaca


def __getattr__(name):
    return getattr(_module(), name)
