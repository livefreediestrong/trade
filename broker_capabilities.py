"""Broker-neutral execution readiness vocabulary.

Adapters expose this shape without ever returning credentials.  A capability is
descriptive: only the desk's existing risk and approval gates can place orders.
"""
from __future__ import annotations

from typing import Any

NOT_CONFIGURED = "not_configured"
PROFILE_INVALID = "profile_invalid"
DISCONNECTED = "disconnected"
PAPER_READY = "paper_ready"
LIVE_READY = "live_ready"
DEGRADED = "degraded"


def readiness(
    *,
    broker: str,
    configured: bool,
    paper_mode: bool,
    profile_valid: bool = True,
    connected: bool | None = None,
    selected_account: str | None = None,
    diagnostics: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Return one safe, stable readiness shape for every broker adapter."""
    diagnostics = diagnostics or []
    if not configured:
        state = NOT_CONFIGURED
    elif not profile_valid:
        state = PROFILE_INVALID
    elif connected is False:
        state = DISCONNECTED
    elif connected is None:
        state = DEGRADED
    else:
        state = PAPER_READY if paper_mode else LIVE_READY
    return {
        "capability_state": state,
        "can_submit": state in (PAPER_READY, LIVE_READY),
        "can_transition_live": state == LIVE_READY,
        "selected_account": selected_account,
        "diagnostics": diagnostics,
        "broker": broker,
    }
