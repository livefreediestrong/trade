"""Auto live options intents — allowed/blocked. No live IBKR submits."""
from __future__ import annotations

import pytest
import order_terms
import auto_live_options as alo
import broker_ibkr as ibkr


def test_option_notional_x100():
    assert order_terms.option_notional(1, 1.50) == 150.0
    assert order_terms.option_notional(2, 0.55) == 110.0


def test_bto_call_and_put_allowed():
    for right in ("C", "P"):
        o = order_terms.canonical_option_order(
            {"side": "buy", "suggested_shares": 1},
            {"type": "limit", "limit_price": 1.25, "right": right, "expiry": "2026-10-17",
             "strike": 100, "intent": "BTO"},
        )
        assert o["option_intent"] == "BTO" and o["right"] == right and o["notional_bound"] == 125.0


def test_stc_allowed():
    o = order_terms.canonical_option_order(
        {"side": "sell", "suggested_shares": 2},
        {"type": "market", "right": "C", "expiry": "2026-10-17", "strike": 100, "intent": "STC"},
    )
    assert o["option_intent"] == "STC" and o["contracts"] == 2


def test_sto_blocked_without_flags():
    with pytest.raises(ValueError, match="covered|allow_naked|STO"):
        order_terms.canonical_option_order(
            {"side": "sell", "suggested_shares": 1},
            {"type": "market", "right": "C", "expiry": "2026-10-17", "strike": 100, "intent": "STO"},
        )


def test_sto_covered_and_naked_allowed_in_terms():
    covered = order_terms.canonical_option_order(
        {"side": "sell", "suggested_shares": 1},
        {"type": "limit", "limit_price": 2.0, "right": "C", "expiry": "2026-10-17",
         "strike": 100, "intent": "STO", "covered": True},
    )
    assert covered["covered"] is True and covered["option_intent"] == "STO"
    naked = order_terms.canonical_option_order(
        {"side": "sell", "suggested_shares": 1},
        {"type": "limit", "limit_price": 2.0, "right": "P", "expiry": "2026-10-17",
         "strike": 100, "intent": "STO", "allow_naked": True},
    )
    assert naked["allow_naked"] is True


def test_btc_allowed_in_terms():
    o = order_terms.canonical_option_order(
        {"side": "buy", "suggested_shares": 1},
        {"type": "market", "right": "C", "expiry": "2026-10-17", "strike": 100, "intent": "BTC"},
    )
    assert o["option_intent"] == "BTC"
    assert o["contracts"] == 1 and o["shares"] == 1


def test_intent_gate_matrix():
    assert alo.intent_gate("BTO", right="C", strategy="long_call", allow_naked=False, covered=False) is None
    assert alo.intent_gate("STC", right="P", strategy="long_put", allow_naked=False, covered=False) is None
    assert alo.intent_gate("STO", right="C", strategy="short_call", allow_naked=False, covered=False) is not None
    assert alo.intent_gate("STO", right="C", strategy="short_call", allow_naked=False, covered=True,
                           stock_shares=100, contracts=1) is None
    assert alo.intent_gate("STO", right="C", strategy="short_call", allow_naked=True, covered=False) is None
    assert alo.intent_gate("BTC", right="C", strategy="short_call", allow_naked=False, covered=False) is None
    assert alo.intent_gate("OPEN", right="C", strategy="call_debit", allow_naked=False, covered=False) is None


def test_bag_vertical_terms():
    bag = order_terms.canonical_bag_order(
        {"side": "buy", "suggested_shares": 1},
        {"type": "limit", "limit_price": 1.10, "strategy": "call_debit",
         "expiry": "2026-10-17", "long_strike": 100, "short_strike": 105},
    )
    assert bag["asset_type"] == "BAG" and len(bag["legs"]) == 2
    assert bag["notional_bound"] == 110.0


def test_risk_ready_honesty():
    assert alo.risk_ready_error(None) is not None
    assert alo.risk_ready_error({"ok": True, "risk_ready": False}) is not None
    assert alo.risk_ready_error({"ok": True, "risk_ready": True, "account": {"day_pnl": 1.0}}) is None


def test_place_option_refuses_naked_sto_without_flag_no_broker_io():
    class Boom(Exception):
        pass

    class FakeIB:
        def qualifyContracts(self, *a, **k):
            raise Boom("should not qualify unflagged STO")
        def positions(self, *a, **k):
            return []
        def openTrades(self):
            return []
        def placeOrder(self, *a, **k):
            raise Boom("should not place")
        def sleep(self, *a, **k):
            return None
        def accountSummary(self, *a, **k):
            return []

    with pytest.raises(ValueError, match="STO|naked|covered|allow_naked"):
        ibkr._place_option_from_desk(
            FakeIB(),
            {"option_intent": "STO", "side": "sell", "contracts": 1, "right": "C",
             "expiry": "2026-10-17", "strike": 100, "ticker": "SPY", "type": "limit",
             "limit": 1.5, "signal_id": "x"},
            {"account_id": "DU123", "paper_mode": True, "endpoint": "x"},
            "ref",
        )


def test_capabilities_advertise_v2():
    caps = order_terms.capabilities("ibkr")
    assert "STO" in caps["options_live_v2"]["intents"]
    assert caps["options_live_v2"]["multi_leg"] is True
    assert caps["options_live_v2"]["multiplier"] == 100


def test_map_stock_signal_strategies():
    cfg = alo.validate({"enabled": True, "strategies": ["long_call", "long_put", "short_call"],
                        "allow_naked_short": False, "max_contracts": 2, "dte_min": 5, "dte_max": 45,
                        "max_quote_age_sec": 15, "prefer_otm_pct": 0.5, "spread_width_pct": 1.0,
                        "every_n_stock_cycles": 1})
    assert alo.map_stock_signal_to_strategy({"side": "buy"}, cfg, 0) == "long_call"
    assert alo.map_stock_signal_to_strategy({"side": "sell"}, cfg, 0) == "long_put"
    assert alo.map_stock_signal_to_strategy({"side": "sell"}, cfg, 200) == "long_put"
    # short_call only when sell side and shares (or later in priority after long_put)
    cfg2 = alo.validate({**cfg, "strategies": ["short_call"]})
    assert alo.map_stock_signal_to_strategy({"side": "sell"}, cfg2, 100) == "short_call"
    assert alo.map_stock_signal_to_strategy({"side": "sell"}, cfg2, 0) is None
