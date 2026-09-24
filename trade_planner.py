"""Pure, hypothetical dollar sizing and cost arithmetic. Never submits orders."""
from decimal import Decimal, InvalidOperation, ROUND_DOWN


def number(value, label, minimum=0, maximum=1_000_000):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a number") from None
    if not n.is_finite() or not Decimal(str(minimum)) <= n <= Decimal(str(maximum)):
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return n


def paper_quantity(value, fractional=True):
    n = number(value, "Shares", 0, 1_000_000_000)
    return float(n.quantize(Decimal("0.000001") if fractional else Decimal("1"), rounding=ROUND_DOWN))


def estimate(body):
    if not isinstance(body, dict):
        raise ValueError("Enter an estimate as a JSON object")
    budget = number(body.get("budget"), "Budget", .01)
    price = number(body.get("price"), "Share price", .0001)
    exit_price = number(body.get("exit_price", price), "Possible exit price", .0001)
    slip = number(body.get("slippage_bps", 5), "Price movement allowance", 0, 1000) / 10000
    direction = body.get("direction", "long")
    kind = body.get("order_type", "market")
    if direction not in ("long", "short") or kind not in ("market", "limit"):
        raise ValueError("Choose long/short and market/limit")
    fractional = body.get("fractional", True)
    if not isinstance(fractional, bool):
        raise ValueError("Fractional shares must be true or false")
    known_fees = body.get("fee_mode", "unknown") == "custom"
    if body.get("fee_mode", "unknown") not in ("unknown", "custom"):
        raise ValueError("Fee mode must be unknown or custom")
    fee_in = number(body.get("entry_fee", 0), "Entry fee", 0) if known_fees else Decimal(0)
    fee_out = number(body.get("exit_fee", 0), "Exit fee", 0) if known_fees else Decimal(0)
    borrow = number(body.get("borrow_fee", 0), "Borrow cost", 0) if known_fees and direction == "short" else Decimal(0)
    # Long budget includes the assumed entry fee. Short budget describes gross
    # exposure, not required collateral; margin/locate requirements are unknown.
    entry = price if kind == "limit" else price * (1 + slip if direction == "long" else 1 - slip)
    available = max(Decimal(0), budget - fee_in) if direction == "long" else budget
    shares = (available / entry).quantize(Decimal(".000001") if fractional else Decimal("1"), rounding=ROUND_DOWN)
    exit_fill = exit_price * (1 - slip if direction == "long" else 1 + slip)
    gross = shares * (exit_fill - entry) * (1 if direction == "long" else -1)
    fees = fee_in + fee_out + borrow if shares > 0 else Decimal(0)
    breakeven = None
    feasible_break_even = None
    if known_fees and shares > 0:
        target = entry + fees / shares if direction == "long" else entry - fees / shares
        feasible_break_even = target >= 0
        if feasible_break_even:
            breakeven = target / (1 - slip if direction == "long" else 1 + slip)
    def convert(n):
        if n is None:
            return None
        rounded = round(float(n), 6)
        return 0.0 if rounded == 0 else rounded
    return {"ok": True, "hypothetical": True, "direction": direction, "order_type": kind,
            "shares": convert(shares), "entry_price": convert(entry), "entry_value": convert(shares * entry),
            "cash_needed_estimate": convert(shares * entry + (fee_in if shares > 0 else 0)) if known_fees and direction == "long" else None,
            "gross_pnl": convert(gross), "fees_assumed": convert(fees) if known_fees else None,
            "net_pnl_estimate": convert(gross - fees) if known_fees else None,
            "break_even_price": convert(breakeven), "break_even_possible": feasible_break_even, "fees_known": known_fees,
            "all_in_guaranteed": False, "live_fractional_available": False, "trade_possible": shares > 0,
            "note": "The budget cannot cover the minimum share size and assumed entry fee. No trade is modeled."
                    if shares == 0 else "No attainable break-even: assumed fees exceed the maximum possible proceeds."
                    if feasible_break_even is False else "Your assumptions, not a broker quote. Market prices and fees can change."
                    if known_fees else "Fees are unknown. Total cost, net profit and break-even stay unknown.",
            "short_note": "Borrow availability, margin and borrow rates are unverified; research only. Short-sale losses can exceed the initial amount." if direction == "short" else None}
