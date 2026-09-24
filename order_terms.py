"""Canonical, server-owned equity order terms. No broker I/O."""
from decimal import Decimal, InvalidOperation


def positive(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{name} must be a positive finite number") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return result


def canonical_order(signal, options=None):
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("Order options must be an object")
    unknown = set(options) - {"type", "limit_price", "max_total"}
    if unknown:
        raise ValueError("Unsupported order fields: " + ", ".join(sorted(unknown)))
    kind = str(options.get("type") or "market").lower()
    if kind not in ("market", "limit"):
        raise ValueError("Only market and DAY limit equity orders are supported")
    qty = positive(signal.get("suggested_shares"), "Shares")
    if qty != qty.to_integral_value():
        raise ValueError("This desk currently supports whole shares only")
    if signal.get("side") not in ("buy", "sell"):
        raise ValueError("Buy or sell required")
    order = {"type": kind, "shares": int(qty), "time_in_force": "day"}
    if kind == "limit":
        price = positive(options.get("limit_price"), "Limit price")
        # Conservative US-equity decimal precision; broker contract rules are
        # still authoritative and can reject a price on a coarser increment.
        quantum = Decimal("0.01") if price >= 1 else Decimal("0.0001")
        if price != price.quantize(quantum):
            raise ValueError("Limit price precision: cents above $1; four decimals below $1")
        order.update(limit=float(price), notional_bound=float(qty * price))
    if options.get("max_total") not in (None, ""):
        positive(options["max_total"], "Maximum total")
        # An estimate is not an upper bound on fees. Do not misrepresent it as
        # enforcing the user's all-in dollar cap.
        raise ValueError("An all-in budget cannot be guaranteed: broker fees have no verified upper bound")
    return order


def capabilities(provider):
    return {"broker": provider, "equities": True, "order_types": ["market", "limit"],
            "time_in_force": ["day"], "whole_shares": True, "fractional_shares": False,
            "protective_brackets": False, "all_in_budget_guaranteed": False,
            "notes": ["Limit price bounds the entry price, not commissions or slippage on a later exit.",
                      "Broker-managed brackets require a separately verified child-order lifecycle.",
                      "Fractional execution is not supported by this desk adapter.",
                      "Working DAY limits remain tracked until the broker reports a terminal state."]}
