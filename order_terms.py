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
            "options_live_v1": {"long_single_leg": True, "intents": ["BTO", "STC"], "naked_short": False, "multi_leg": False, "multiplier": 100},
            "notes": ["Limit price bounds the entry price, not commissions or slippage on a later exit.",
                      "Broker-managed brackets require a separately verified child-order lifecycle.",
                      "Fractional execution is not supported by this desk adapter.",
                      "Working DAY limits remain tracked until the broker reports a terminal state."]}


def option_notional(contracts, premium, multiplier=100):
    """USD exposure for equity options: contracts × premium × multiplier.

    Never treat premium as a stock price for share-cap helpers.
    """
    qty = positive(contracts, "Contracts")
    if qty != qty.to_integral_value():
        raise ValueError("Whole contracts only")
    px = positive(premium, "Premium")
    mult = positive(multiplier, "Multiplier")
    if mult != 100:
        raise ValueError("Only standard 100-share equity options are supported in live v1")
    return float(qty * px * mult)


def canonical_option_order(signal, options=None):
    """Long single-leg equity option terms for live manual tickets (BTO/STC only)."""
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("Order options must be an object")
    unknown = set(options) - {"type", "limit_price", "max_total", "right", "expiry", "strike", "intent"}
    if unknown:
        raise ValueError("Unsupported option order fields: " + ", ".join(sorted(unknown)))
    intent = str(options.get("intent") or signal.get("option_intent") or "").upper()
    if intent in ("STO", "BTC"):
        raise ValueError("Live v1 refuses naked short sells (STO) and buy-to-close of shorts (BTC); use Paper Options or a later release")
    if intent not in ("BTO", "STC"):
        raise ValueError("Live options v1 supports BTO and STC only (long single-leg)")
    right = str(options.get("right") or signal.get("right") or "").upper()
    if right not in ("C", "P", "CALL", "PUT"):
        raise ValueError("Choose Call (C) or Put (P)")
    right = "C" if right in ("C", "CALL") else "P"
    expiry = str(options.get("expiry") or signal.get("expiry") or "").strip()
    if len(expiry) == 8 and expiry.isdigit():
        expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
    from datetime import datetime
    try:
        datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Expiry must be YYYY-MM-DD") from exc
    strike = positive(options.get("strike", signal.get("strike")), "Strike")
    kind = str(options.get("type") or "market").lower()
    if kind not in ("market", "limit"):
        raise ValueError("Only market and DAY limit option orders are supported")
    qty = positive(signal.get("suggested_shares", signal.get("contracts")), "Contracts")
    if qty != qty.to_integral_value():
        raise ValueError("Whole contracts only")
    if int(qty) > 20:
        raise ValueError("Live v1 caps option tickets at 20 contracts per order")
    side = "buy" if intent == "BTO" else "sell"
    if signal.get("side") not in (None, side):
        raise ValueError("Option intent does not match order side")
    order = {"type": kind, "shares": int(qty), "contracts": int(qty), "time_in_force": "day",
             "asset_type": "OPT", "right": right, "expiry": expiry, "strike": float(strike),
             "option_intent": intent, "multiplier": 100}
    if kind == "limit":
        price = positive(options.get("limit_price"), "Limit price")
        quantum = Decimal("0.01") if price >= 1 else Decimal("0.0001")
        if price != price.quantize(quantum):
            raise ValueError("Limit premium precision: cents above $1; four decimals below $1")
        order.update(limit=float(price), notional_bound=option_notional(int(qty), float(price), 100))
    if options.get("max_total") not in (None, ""):
        positive(options["max_total"], "Maximum total")
        raise ValueError("An all-in budget cannot be guaranteed: broker fees have no verified upper bound")
    return order

