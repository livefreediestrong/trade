"""Single source of truth for the desk's user-selectable risk presets.

This module contains policy data only. It deliberately has no Flask, broker, or
filesystem dependencies so the execution and API layers can consume the same
limits without importing each other.
"""
from __future__ import annotations

from typing import Any
import math

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


def effective_limits(cfg, equity=None, *, agent=True):
    """Account-based ceilings, shared by order checks and read-only displays.

    Saved agent dollar limits are extra ceilings, never a replacement for the
    account preset. Unknown account value must not become an unlimited budget.
    """
    def positive(value):
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) and value > 0 else None
        except (ValueError, TypeError):
            return None

    preset = get_preset(cfg.get("risk_preset"))
    value = positive(equity)
    policy = ((cfg.get("live_agent") or {}).get("policy") or {}) if agent else {}
    switch = cfg.get("kill_switch") or {}
    switch = switch if switch.get("armed") else {}
    order = value * preset["max_position_pct"] / 100 if value else None
    loss = value * preset["max_daily_loss_pct"] / 100 if value else None
    if value:
        order = min([order] + [x for x in (positive(policy.get("max_order_usd")),
                    positive(switch.get("max_position_size_usd"))) if x is not None])
        loss = min([loss] + [x for x in (positive(policy.get("max_daily_loss_usd")),
                   positive(switch.get("max_daily_loss_usd")), positive(cfg.get("max_session_loss_usd"))) if x is not None])
    trades = min([preset["max_trades_per_day"]] + [int(x) for x in
                 (positive(switch.get("max_trades_per_day")),) if x is not None])
    attempts = positive(policy.get("max_orders_per_day"))
    return {"equity": value, "verified": value is not None,
            "max_order_usd": round(order, 8) if order is not None else None,
            "max_daily_loss_usd": round(loss, 8) if loss is not None else None,
            "max_trades_per_day": trades,
            "max_orders_per_day": min(trades, int(attempts)) if attempts else trades,
            "position_pct": preset["max_position_pct"], "loss_pct": preset["max_daily_loss_pct"],
            "note": "Whole shares for stocks; options use estimated maximum loss per order. Holdings, working orders, fees and final broker checks may reduce size further. Loss limits stop new entries; they do not guarantee an exit price."}


def option_entry_error(cfg, equity, maximum_loss, *, agent=False):
    limits = effective_limits(cfg, equity, agent=agent)
    try:
        value = float(maximum_loss)
    except (ValueError, TypeError):
        value = float("nan")
    if not limits["verified"] or not math.isfinite(value) or value <= 0:
        return "Verified equity and option maximum loss are required before opening risk"
    if value > limits["max_order_usd"]:
        return f"Option maximum loss ${value:,.2f} exceeds the effective account limit ${limits['max_order_usd']:,.2f}"
    return None
