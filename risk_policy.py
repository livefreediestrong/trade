"""Single source of truth for the desk's user-selectable risk presets.

This module contains policy data only. It deliberately has no Flask, broker, or
filesystem dependencies so the execution and API layers can consume the same
limits without importing each other.
"""
from __future__ import annotations

from typing import Any

RISK_PRESETS: dict[str, dict[str, Any]] = {
    "low": {
        "max_position_pct": 1.0,
        "max_trades_per_day": 3,
        "max_daily_loss_pct": 1.0,
        "min_confidence": 0.0,
        "stop_r": 1.0,
        "target_r": 2.0,
    },
    "mid": {
        "max_position_pct": 2.0,
        "max_trades_per_day": 6,
        "max_daily_loss_pct": 2.0,
        "min_confidence": 0.0,
        "stop_r": 1.0,
        "target_r": 2.5,
    },
    "high": {
        "max_position_pct": 4.0,
        "max_trades_per_day": 12,
        "max_daily_loss_pct": 4.0,
        "min_confidence": 0.0,
        "stop_r": 1.0,
        "target_r": 3.0,
    },
}


def get_preset(name: str | None) -> dict[str, Any]:
    """Return a defensive copy of a named policy, defaulting to ``mid``."""
    return dict(RISK_PRESETS.get(name or "mid", RISK_PRESETS["mid"]))
