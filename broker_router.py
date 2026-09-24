"""Select the configured broker while preserving the desk broker interface."""
from __future__ import annotations

import os
from datetime import datetime, timezone


def submission_window_error(order):
    """Adapters must check this after their last blocking preflight call."""
    try:
        now = datetime.now(timezone.utc)
        deadline = datetime.fromisoformat(str(order.get("valid_until") or "").replace("Z", "+00:00"))
        if deadline.tzinfo is None or now >= deadline:
            return "Order review, signal or quote expired; request a fresh review"
    except (ValueError, TypeError):
        return "Verified order expiry required before broker submission"
    return None


def _module():
    if (os.environ.get("BROKER_PROVIDER", "alpaca") or "alpaca").strip().lower() == "ibkr":
        import broker_ibkr
        return broker_ibkr
    import broker_alpaca
    return broker_alpaca


def __getattr__(name):
    return getattr(_module(), name)
