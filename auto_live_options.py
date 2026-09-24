"""Moss auto_live options: policy, intent gates, contract pick, execution terms.

Reuses options_desk strategy names and broker OPT/BAG adapters.
Never places orders itself — callers feed desk ingest / place_from_desk_order.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Any

from order_terms import (
    canonical_bag_order,
    canonical_option_order,
    option_notional,
    positive,
)
from trade_planner import number

STRATEGIES = {
    "long_call": {"right": "C", "kind": "long", "open_intent": "BTO", "close_intent": "STC"},
    "long_put": {"right": "P", "kind": "long", "open_intent": "BTO", "close_intent": "STC"},
    "short_call": {"right": "C", "kind": "short", "open_intent": "STO", "close_intent": "BTC"},
    "short_put": {"right": "P", "kind": "short", "open_intent": "STO", "close_intent": "BTC"},
    "call_debit": {"right": "C", "kind": "debit", "open_intent": "OPEN", "close_intent": "CLOSE"},
    "put_debit": {"right": "P", "kind": "debit", "open_intent": "OPEN", "close_intent": "CLOSE"},
    "call_credit": {"right": "C", "kind": "credit", "open_intent": "OPEN", "close_intent": "CLOSE"},
    "put_credit": {"right": "P", "kind": "credit", "open_intent": "OPEN", "close_intent": "CLOSE"},
}

DEFAULT_AUTO_OPTIONS = {
    "enabled": False,
    "strategies": ["long_call", "long_put"],
    "allow_naked_short": False,
    "max_contracts": 2,
    "dte_min": 5,
    "dte_max": 45,
    "max_quote_age_sec": 15,
    "prefer_otm_pct": 0.5,  # % of underlying for mild OTM preference
    "spread_width_pct": 1.0,  # vertical width ~ this % of underlying
    "every_n_stock_cycles": 1,  # attempt option conversion each stock PASS
}

UI_LABEL = "Moss LIVE options | calls/puts | shorts gated | BAG verticals"


def defaults() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_AUTO_OPTIONS)


def validate(body) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise ValueError("auto_options must be an object")
    unknown = set(body) - set(DEFAULT_AUTO_OPTIONS)
    if unknown:
        raise ValueError("Unsupported auto_options fields: " + ", ".join(sorted(unknown)))
    out = defaults()
    out.update(body)
    out["enabled"] = bool(out["enabled"])
    out["allow_naked_short"] = bool(out["allow_naked_short"])
    strategies = out["strategies"]
    if not isinstance(strategies, list) or not strategies:
        raise ValueError("Choose at least one options strategy")
    cleaned = []
    for s in strategies:
        if s not in STRATEGIES:
            raise ValueError(f"Unknown options strategy: {s}")
        if s not in cleaned:
            cleaned.append(s)
    out["strategies"] = cleaned
    out["max_contracts"] = int(number(out["max_contracts"], "max_contracts", 1, 20))
    out["dte_min"] = int(number(out["dte_min"], "dte_min", 0, 370))
    out["dte_max"] = int(number(out["dte_max"], "dte_max", 1, 370))
    if out["dte_min"] > out["dte_max"]:
        raise ValueError("dte_min cannot exceed dte_max")
    out["max_quote_age_sec"] = int(number(out["max_quote_age_sec"], "max_quote_age_sec", 1, 60))
    out["prefer_otm_pct"] = float(number(out["prefer_otm_pct"], "prefer_otm_pct", 0, 20))
    out["spread_width_pct"] = float(number(out["spread_width_pct"], "spread_width_pct", 0.1, 20))
    out["every_n_stock_cycles"] = int(number(out["every_n_stock_cycles"], "every_n_stock_cycles", 1, 50))
    return out


def get_config(cfg) -> dict[str, Any]:
    saved = (cfg or {}).get("live_agent") or {}
    raw = saved.get("auto_options")
    if not isinstance(raw, dict):
        return defaults()
    try:
        return validate(raw)
    except (ValueError, TypeError, KeyError):
        return defaults()


def intent_gate(
    intent: str,
    *,
    right: str,
    strategy: str,
    allow_naked: bool,
    covered: bool,
    stock_shares: float | None = None,
    contracts: int = 1,
) -> str | None:
    """Return blocker text, or None if allowed."""
    intent = str(intent or "").upper()
    right = str(right or "").upper()
    right = "C" if right in ("C", "CALL") else ("P" if right in ("P", "PUT") else right)
    if strategy not in STRATEGIES:
        return f"Strategy {strategy} is not supported for live auto options"
    meta = STRATEGIES[strategy]
    if intent not in (meta["open_intent"], meta["close_intent"]):
        return f"Intent {intent} does not match strategy {strategy}"
    if intent == "STO":
        if meta["kind"] != "short":
            return "STO is only for short single-leg strategies"
        if covered:
            if right == "C":
                need = contracts * 100
                if stock_shares is None or stock_shares < need:
                    return f"Covered short call needs {need} long shares of the underlying; held={stock_shares}"
            elif right == "P":
                # Cash-secured put coverage is not verified here (needs cash reserve).
                if not allow_naked:
                    return "Short puts are treated as naked unless allow_naked_short is on (cash-secured not auto-verified)"
        elif not allow_naked:
            return "Naked STO blocked: enable allow_naked_short or provide covered long shares for short calls"
        return None
    if intent in ("BTO", "STC", "BTC", "OPEN", "CLOSE"):
        return None
    return f"Intent {intent} is not recognized"


def risk_ready_error(book: dict | None) -> str | None:
    """Honest risk_ready: require verified day PnL before new auto options risk."""
    if not book or not book.get("ok"):
        return "Broker book unavailable; auto options blocked"
    if book.get("risk_ready") is not True:
        return book.get("risk_error") or "Daily P&L not risk_ready; auto options blocked"
    if book.get("day_pnl") is None and book.get("day_pnl_usd") is None:
        day = (book.get("account") or {}).get("day_pnl")
        if day is None:
            return "Verified day P&L missing; auto options blocked"
    return None


def pick_expiry(expirations: list[str], dte_min: int, dte_max: int, today=None) -> str | None:
    today = today or datetime.now(timezone.utc).date()
    best = None
    best_score = 10**9
    for raw in expirations or []:
        text = str(raw)
        if len(text) == 8 and text.isdigit():
            text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            day = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError:
            continue
        dte = (day - today).days
        if dte < dte_min or dte > dte_max:
            continue
        # Prefer nearer to mid of window.
        mid = (dte_min + dte_max) / 2
        score = abs(dte - mid)
        if score < best_score:
            best, best_score = text, score
    return best


def pick_strike(strikes: list[float], underlying: float, right: str, otm_pct: float) -> float | None:
    if not strikes or underlying <= 0:
        return None
    target = underlying * (1 + otm_pct / 100.0) if right == "C" else underlying * (1 - otm_pct / 100.0)
    return min(strikes, key=lambda s: abs(float(s) - target))


def pick_vertical_strikes(strikes: list[float], underlying: float, right: str, strategy: str, width_pct: float):
    if not strikes or underlying <= 0:
        return None, None
    sorted_strikes = sorted(float(s) for s in strikes)
    width = max(underlying * width_pct / 100.0, 0.5)
    if "debit" in strategy:
        # Buy nearer ATM, sell further OTM.
        if right == "C":
            long_s = min(sorted_strikes, key=lambda s: abs(s - underlying))
            short_s = min((s for s in sorted_strikes if s >= long_s + width * 0.5), default=None, key=lambda s: abs(s - (long_s + width)))
        else:
            long_s = min(sorted_strikes, key=lambda s: abs(s - underlying))
            short_s = min((s for s in sorted_strikes if s <= long_s - width * 0.5), default=None, key=lambda s: abs(s - (long_s - width)))
    else:
        # Credit: sell nearer ATM, buy further OTM.
        if right == "C":
            short_s = min(sorted_strikes, key=lambda s: abs(s - underlying))
            long_s = min((s for s in sorted_strikes if s >= short_s + width * 0.5), default=None, key=lambda s: abs(s - (short_s + width)))
        else:
            short_s = min(sorted_strikes, key=lambda s: abs(s - underlying))
            long_s = min((s for s in sorted_strikes if s <= short_s - width * 0.5), default=None, key=lambda s: abs(s - (short_s - width)))
    if long_s is None or short_s is None or long_s == short_s:
        return None, None
    return float(long_s), float(short_s)


def map_stock_signal_to_strategy(signal: dict, auto_cfg: dict, stock_shares: float = 0.0) -> str | None:
    """Choose one armed strategy that fits the stock PASS side."""
    side = str(signal.get("side") or "").lower()
    armed = auto_cfg.get("strategies") or []
    if side == "buy":
        for name in ("long_call", "call_debit", "short_put", "put_credit"):
            if name in armed:
                if name == "short_put" and not auto_cfg.get("allow_naked_short"):
                    continue
                return name
        if "short_call" in armed and stock_shares >= 100:
            return "short_call"
    if side == "sell":
        for name in ("long_put", "put_debit", "short_call", "call_credit"):
            if name in armed:
                if name == "short_call":
                    if stock_shares >= 100 or auto_cfg.get("allow_naked_short"):
                        return name
                    continue
                return name
    return None


def build_option_candidate(
    signal: dict,
    auto_cfg: dict,
    *,
    chain: dict,
    underlying_price: float,
    stock_shares: float = 0.0,
) -> dict[str, Any]:
    """Build a reviewed-ready option/BAG candidate from a stock PASS signal + chain."""
    auto_cfg = validate(auto_cfg)
    strategy = map_stock_signal_to_strategy(signal, auto_cfg, stock_shares)
    if not strategy:
        raise ValueError("No armed options strategy matches this stock signal side/holdings")
    meta = STRATEGIES[strategy]
    expirations = chain.get("expirations") or chain.get("expiries") or []
    # Some chain payloads nest under classes
    if not expirations and isinstance(chain.get("classes"), list):
        for cls in chain["classes"]:
            expirations.extend(cls.get("expirations") or [])
    expiry = pick_expiry(list(dict.fromkeys(str(x) for x in expirations)), auto_cfg["dte_min"], auto_cfg["dte_max"])
    if not expiry:
        raise ValueError("No option expiry inside the configured DTE window")
    strikes = chain.get("strikes") or []
    if not strikes and isinstance(chain.get("classes"), list):
        for cls in chain["classes"]:
            strikes.extend(cls.get("strikes") or [])
    strikes = [float(s) for s in strikes]
    contracts = min(int(auto_cfg["max_contracts"]), max(1, int(signal.get("suggested_shares") or 1)))
    # Cap short covered calls by shares.
    if strategy == "short_call" and not auto_cfg.get("allow_naked_short"):
        contracts = min(contracts, max(1, int(stock_shares // 100)))
    right = meta["right"]
    intent = meta["open_intent"]
    covered = strategy == "short_call" and stock_shares >= contracts * 100
    allow_naked = bool(auto_cfg.get("allow_naked_short")) and not covered
    err = intent_gate(
        intent, right=right, strategy=strategy, allow_naked=allow_naked,
        covered=covered, stock_shares=stock_shares, contracts=contracts,
    )
    if err:
        raise ValueError(err)
    if meta["kind"] in ("long", "short"):
        strike = pick_strike(strikes, underlying_price, right, auto_cfg["prefer_otm_pct"])
        if strike is None:
            raise ValueError("No strikes available to pick a single-leg contract")
        return {
            "asset_type": "OPT",
            "option_strategy": strategy,
            "option_intent": intent,
            "right": right,
            "expiry": expiry,
            "strike": float(strike),
            "contracts": contracts,
            "side": "buy" if intent in ("BTO", "BTC") else "sell",
            "allow_naked_short": allow_naked,
            "covered": covered,
            "ticker": str(signal.get("ticker") or "").upper(),
        }
    long_s, short_s = pick_vertical_strikes(strikes, underlying_price, right, strategy, auto_cfg["spread_width_pct"])
    if long_s is None:
        raise ValueError("No strike pair available for vertical BAG")
    err = intent_gate(intent, right=right, strategy=strategy, allow_naked=allow_naked, covered=False, contracts=contracts)
    if err:
        raise ValueError(err)
    return {
        "asset_type": "BAG",
        "option_strategy": strategy,
        "option_intent": intent,
        "right": right,
        "expiry": expiry,
        "long_strike": float(long_s),
        "short_strike": float(short_s),
        "contracts": contracts,
        "side": "buy" if "debit" in strategy else "sell",
        "ticker": str(signal.get("ticker") or "").upper(),
    }


def execution_terms_option(signal, cfg, premium, execution_quote, max_order_usd: float):
    """Deterministic option/BAG terms for auto_live — never trusts model prices for limits."""
    auto_cfg = get_config(cfg)
    if not auto_cfg.get("enabled"):
        raise ValueError("auto_options is not enabled on the live agent")
    err = risk_ready_error(signal.get("broker_book") or cfg.get("_broker_book_snapshot"))
    # Caller may skip book on signal; live_agent start already required risk_ready.
    asset = str(signal.get("asset_type") or "").upper()
    policy = ((cfg.get("live_agent") or {}).get("policy") or {})
    order_type = str(policy.get("order_type") or "limit")
    contracts = int(signal.get("contracts") or signal.get("suggested_shares") or 0)
    if contracts < 1:
        raise ValueError("Option contracts missing")
    mark = positive(premium, "Option premium")
    bound = mark
    options = {"type": order_type}
    if order_type == "limit":
        offset = Decimal(str(policy.get("limit_offset_bps") or 5)) / 10000
        buying = signal.get("side") == "buy"
        limit = mark * (1 + offset if buying else 1 - offset)
        quantum = Decimal(".01") if limit >= 1 else Decimal(".0001")
        limit = limit.quantize(quantum, rounding=ROUND_DOWN if buying else ROUND_UP)
        options["limit_price"] = float(limit)
        bound = max(mark, limit)
    budget = Decimal(str(max_order_usd if max_order_usd is not None else policy.get("max_order_usd") or 0))
    affordable = int(budget / (bound * 100)) if bound > 0 else 0
    contracts = min(contracts, max(0, affordable), int(auto_cfg["max_contracts"]))
    if contracts < 1:
        raise ValueError("Option budget below one contract notional (premium x 100)")
    base = {
        "side": signal.get("side"),
        "suggested_shares": contracts,
        "contracts": contracts,
        "right": signal.get("right"),
        "expiry": signal.get("expiry"),
        "strike": signal.get("strike"),
        "option_intent": signal.get("option_intent"),
        "option_strategy": signal.get("option_strategy"),
        "allow_naked_short": signal.get("allow_naked_short"),
        "covered": signal.get("covered"),
        "long_strike": signal.get("long_strike"),
        "short_strike": signal.get("short_strike"),
    }
    if asset == "BAG":
        options.update({
            "strategy": signal.get("option_strategy"),
            "right": signal.get("right"),
            "expiry": signal.get("expiry"),
            "long_strike": signal.get("long_strike"),
            "short_strike": signal.get("short_strike"),
            "intent": signal.get("option_intent") or "OPEN",
        })
        return canonical_bag_order(base, options)
    options.update({
        "right": signal.get("right"),
        "expiry": signal.get("expiry"),
        "strike": signal.get("strike"),
        "intent": signal.get("option_intent"),
        "allow_naked": bool(signal.get("allow_naked_short")),
        "covered": bool(signal.get("covered")),
        "strategy": signal.get("option_strategy"),
    })
    return canonical_option_order(base, options)


def status_payload(cfg) -> dict[str, Any]:
    auto_cfg = get_config(cfg)
    return {
        "auto_options": auto_cfg,
        "label": UI_LABEL,
        "strategies": list(STRATEGIES),
        "armed": bool(auto_cfg.get("enabled")) and bool(((cfg or {}).get("live_agent") or {}).get("enabled")),
        "notes": [
            "×100 notional via option_notional",
            "risk_ready (verified day P&L) required for new auto options risk",
            "Naked STO needs allow_naked_short; covered short calls need long shares",
            "Assignment / early exercise not simulated",
        ],
    }
