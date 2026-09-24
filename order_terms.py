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
            "options_live_v2": {
                "long_single_leg": True,
                "short_single_leg": True,
                "intents": ["BTO", "STC", "STO", "BTC"],
                "rights": ["C", "P"],
                "naked_short": "requires allow_naked_short + buying_power check",
                "covered_short_call": True,
                "multi_leg": True,
                "multi_leg_strategies": ["call_debit", "put_debit", "call_credit", "put_credit"],
                "multiplier": 100,
            },
            # Back-compat alias for older UI/tests
            "options_live_v1": {
                "long_single_leg": True, "intents": ["BTO", "STC", "STO", "BTC"],
                "naked_short": "gated", "multi_leg": True, "multiplier": 100,
            },
            "notes": ["Limit price bounds the entry price, not commissions or slippage on a later exit.",
                      "Broker-managed brackets require a separately verified child-order lifecycle.",
                      "Fractional execution is not supported by this desk adapter.",
                      "Working DAY limits remain tracked until the broker reports a terminal state.",
                      "Option notional = contracts x premium x 100. Naked shorts carry assignment risk.",
                      "BAG verticals submit as one IBKR combo; early assignment on short legs is not simulated."]}


def option_notional(contracts, premium, multiplier=100):
    """USD exposure for equity options: contracts x premium x multiplier.

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


OPTION_MIN_TICK = Decimal("0.01")


def option_tick(price, *, combo=False):
    """Conservative IBKR option price increment for an order at ``price``.

    Single-leg US equity options trade in $0.05 steps at $3 and above for most
    classes (penny-pilot names allow $0.01 below $3). Combos trade in cents.
    The broker's contract rules stay authoritative.
    """
    if combo:
        return OPTION_MIN_TICK
    return Decimal("0.05") if Decimal(str(price)) >= 3 else OPTION_MIN_TICK


def round_option_price(price, *, buying, combo=False):
    """Round a premium onto its tick, never past the intended bound.

    Buys round down (never pay above the computed limit); sells round up.
    """
    from decimal import ROUND_CEILING, ROUND_FLOOR
    value = Decimal(str(price))
    tick = option_tick(value, combo=combo)
    steps = (value / tick).to_integral_value(rounding=ROUND_FLOOR if buying else ROUND_CEILING)
    return steps * tick


def option_max_loss(order, premium=None):
    """Worst-case USD loss for an option/BAG order at entry, for risk caps.

    Premium-based notional understates short and credit risk, so:
      long single leg / debit spread -> premium x 100 x contracts
      credit spread                  -> (width - credit) x 100 x contracts
      short put                      -> (strike - premium) x 100 x contracts
      short call (naked)             -> strike x 100 x contracts (unbounded; strike is a floor)
      covered short call             -> premium x 100 x contracts (stock already held)
    Closing intents (STC/BTC/CLOSE) return the premium notional.
    """
    qty = positive(order.get("contracts", order.get("shares")), "Contracts")
    px = Decimal(str(abs(float(premium if premium is not None else order.get("limit") or 0))))
    if px <= 0:
        raise ValueError("Premium must be a positive finite number")
    intent = str(order.get("option_intent") or "").upper()
    strategy = str(order.get("option_strategy") or "")
    per = px
    if order.get("asset_type") == "BAG" or strategy in ("call_credit", "put_credit", "call_debit", "put_debit"):
        if intent != "CLOSE" and "credit" in strategy:
            width = abs(positive(order.get("long_strike"), "Buy-leg strike")
                        - positive(order.get("short_strike"), "Sell-leg strike"))
            per = max(width - px, Decimal("0"))
    elif intent == "STO":
        right = str(order.get("right") or "").upper()
        # Only a short CALL is covered by held shares; a "covered" put is still a
        # short put whose loss runs to the strike.
        if not (order.get("covered") and right in ("C", "CALL")):
            strike = positive(order.get("strike"), "Strike")
            per = max(strike - px, Decimal("0")) if right in ("P", "PUT") else strike
    return float(per * qty * 100)


def _normalize_right(right):
    right = str(right or "").upper()
    if right not in ("C", "P", "CALL", "PUT"):
        raise ValueError("Choose Call (C) or Put (P)")
    return "C" if right in ("C", "CALL") else "P"


def _normalize_expiry(expiry):
    expiry = str(expiry or "").strip()
    if len(expiry) == 8 and expiry.isdigit():
        expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
    from datetime import datetime
    try:
        datetime.strptime(expiry, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("Expiry must be YYYY-MM-DD") from exc
    return expiry


def canonical_option_order(signal, options=None):
    """Single-leg equity option terms (BTO/STC/STO/BTC). Calls and puts.

    Naked STO requires options['allow_naked']=True (broker also checks buying power).
    Covered STO sets options['covered']=True; broker verifies stock shares for calls.
    """
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("Order options must be an object")
    unknown = set(options) - {
        "type", "limit_price", "max_total", "right", "expiry", "strike", "intent",
        "allow_naked", "covered", "strategy",
    }
    if unknown:
        raise ValueError("Unsupported option order fields: " + ", ".join(sorted(unknown)))
    intent = str(options.get("intent") or signal.get("option_intent") or "").upper()
    if intent not in ("BTO", "STC", "STO", "BTC"):
        raise ValueError("Live options support BTO, STC, STO and BTC")
    allow_naked = bool(options.get("allow_naked") or signal.get("allow_naked_short"))
    covered = bool(options.get("covered") or signal.get("covered"))
    if intent == "STO" and not (allow_naked or covered):
        raise ValueError(
            "STO requires covered=True (verified long stock for short calls) "
            "or allow_naked=True with an explicit naked-short allow flag"
        )
    right = _normalize_right(options.get("right") or signal.get("right"))
    expiry = _normalize_expiry(options.get("expiry") or signal.get("expiry"))
    strike = positive(options.get("strike", signal.get("strike")), "Strike")
    kind = str(options.get("type") or "market").lower()
    if kind not in ("market", "limit"):
        raise ValueError("Only market and DAY limit option orders are supported")
    qty = positive(signal.get("suggested_shares", signal.get("contracts")), "Contracts")
    if qty != qty.to_integral_value():
        raise ValueError("Whole contracts only")
    if int(qty) > 20:
        raise ValueError("Live options cap tickets at 20 contracts per order")
    side = "buy" if intent in ("BTO", "BTC") else "sell"
    if signal.get("side") not in (None, side):
        raise ValueError("Option intent does not match order side")
    strategy = str(options.get("strategy") or signal.get("option_strategy") or "")
    if not strategy:
        if intent in ("BTO", "STC"):
            strategy = "long_call" if right == "C" else "long_put"
        else:
            strategy = "short_call" if right == "C" else "short_put"
    order = {
        "type": kind, "shares": int(qty), "contracts": int(qty), "time_in_force": "day",
        "asset_type": "OPT", "right": right, "expiry": expiry, "strike": float(strike),
        "option_intent": intent, "multiplier": 100, "option_strategy": strategy,
        "allow_naked": allow_naked, "covered": covered,
    }
    if kind == "limit":
        price = positive(options.get("limit_price"), "Limit price")
        if price != price.quantize(OPTION_MIN_TICK):
            raise ValueError("Option limit premiums trade in whole cents (IBKR rejects sub-penny prices)")
        order.update(limit=float(price), notional_bound=option_notional(int(qty), float(price), 100))
    if options.get("max_total") not in (None, ""):
        positive(options["max_total"], "Maximum total")
        raise ValueError("An all-in budget cannot be guaranteed: broker fees have no verified upper bound")
    return order


def canonical_bag_order(signal, options=None):
    """Two-leg vertical BAG terms (same underlying/expiry/right; different strikes).

    Legs follow options_desk conventions: long_strike bought, short_strike sold.
    """
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise ValueError("Order options must be an object")
    unknown = set(options) - {
        "type", "limit_price", "max_total", "right", "expiry", "long_strike", "short_strike",
        "strategy", "intent",
    }
    if unknown:
        raise ValueError("Unsupported BAG order fields: " + ", ".join(sorted(unknown)))
    strategy = str(options.get("strategy") or signal.get("option_strategy") or "")
    allowed = {"call_debit", "put_debit", "call_credit", "put_credit"}
    if strategy not in allowed:
        raise ValueError("BAG live path supports call/put debit and credit verticals only")
    right = _normalize_right(options.get("right") or ("C" if strategy.startswith("call") else "P"))
    if (strategy.startswith("call") and right != "C") or (strategy.startswith("put") and right != "P"):
        raise ValueError("BAG strategy does not match call/put right")
    expiry = _normalize_expiry(options.get("expiry") or signal.get("expiry"))
    long_strike = positive(options.get("long_strike", signal.get("long_strike")), "Buy-leg strike")
    short_strike = positive(options.get("short_strike", signal.get("short_strike")), "Sell-leg strike")
    if long_strike == short_strike:
        raise ValueError("Vertical legs need distinct strikes")
    kind_spread = "debit" if "debit" in strategy else "credit"
    lower_long = (right == "C" and kind_spread == "debit") or (right == "P" and kind_spread == "credit")
    if (float(long_strike) < float(short_strike)) != lower_long:
        raise ValueError("The buy/sell strikes do not match this spread strategy")
    kind = str(options.get("type") or "market").lower()
    if kind not in ("market", "limit"):
        raise ValueError("Only market and DAY limit BAG orders are supported")
    qty = positive(signal.get("suggested_shares", signal.get("contracts")), "Contracts")
    if qty != qty.to_integral_value():
        raise ValueError("Whole contracts only")
    if int(qty) > 10:
        raise ValueError("Live BAG tickets cap at 10 contracts per order")
    intent = str(options.get("intent") or signal.get("option_intent") or "OPEN").upper()
    if intent not in ("OPEN", "CLOSE"):
        raise ValueError("BAG intent must be OPEN or CLOSE")
    side = "buy" if (intent == "OPEN" and kind_spread == "debit") or (intent == "CLOSE" and kind_spread == "credit") else "sell"
    if signal.get("side") not in (None, side):
        raise ValueError("BAG intent does not match order side")
    # The combo definition is fixed per spread kind so its natural price is a
    # positive net premium: debit = [BUY long, SELL short], credit = [BUY short,
    # SELL long] (written below as SELL long / BUY short). OPEN trades the combo
    # in its opening direction and CLOSE trades the same combo the other way.
    # IBKR reverses every leg of a SOLD combo, so only combo_action may flip;
    # flipping the legs too would make CLOSE identical to OPEN.
    open_action = "BUY" if kind_spread == "debit" else "SELL"
    legs = [
        {"action": open_action, "right": right, "expiry": expiry,
         "strike": float(long_strike), "ratio": 1},
        {"action": "SELL" if open_action == "BUY" else "BUY", "right": right, "expiry": expiry,
         "strike": float(short_strike), "ratio": 1},
    ]
    combo_action = open_action if intent == "OPEN" else ("SELL" if open_action == "BUY" else "BUY")
    order = {
        "type": kind, "shares": int(qty), "contracts": int(qty), "time_in_force": "day",
        "asset_type": "BAG", "right": right, "expiry": expiry,
        "long_strike": float(long_strike), "short_strike": float(short_strike),
        "option_intent": intent, "option_strategy": strategy, "multiplier": 100,
        "legs": legs, "combo_action": combo_action,
    }
    if kind == "limit":
        price = positive(options.get("limit_price"), "Limit price")
        # Net premium per share; notional uses |net| x contracts x 100.
        if abs(price) != abs(price).quantize(OPTION_MIN_TICK):
            raise ValueError("Combo limit premiums trade in whole cents (IBKR rejects sub-penny prices)")
        order.update(limit=float(price), notional_bound=option_notional(int(qty), abs(float(price)), 100))
    if options.get("max_total") not in (None, ""):
        positive(options["max_total"], "Maximum total")
        raise ValueError("An all-in budget cannot be guaranteed: broker fees have no verified upper bound")
    return order
