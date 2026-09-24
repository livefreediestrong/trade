"""Live options v1 unit tests — no live IBKR submits."""
from __future__ import annotations

import pytest
import order_terms
import broker_ibkr as ibkr


def test_option_notional_uses_multiplier():
    assert order_terms.option_notional(1, 1.50) == 150.0
    assert order_terms.option_notional(2, 0.55) == 110.0
    with pytest.raises(ValueError):
        order_terms.option_notional(1, 1.5, multiplier=10)


def test_canonical_option_order_refuses_sto():
    with pytest.raises(ValueError, match="STO|naked|short|covered|allow_naked"):
        order_terms.canonical_option_order(
            {"side": "sell", "suggested_shares": 1},
            {"type": "market", "right": "C", "expiry": "2026-10-17", "strike": 500, "intent": "STO"},
        )
    o = order_terms.canonical_option_order(
        {"side": "buy", "suggested_shares": 1},
        {"type": "limit", "limit_price": 1.5, "right": "P", "expiry": "2026-10-17", "strike": 100, "intent": "BTO"},
    )
    assert o["asset_type"] == "OPT" and o["contracts"] == 1 and o["notional_bound"] == 150.0
    assert o["option_intent"] == "BTO"


def test_stock_canonical_order_unchanged():
    o = order_terms.canonical_order({"side": "buy", "suggested_shares": 2}, {"type": "limit", "limit_price": 100})
    assert o["shares"] == 2 and o["limit"] == 100.0
    assert o.get("asset_type") is None
    assert abs(o["notional_bound"] - 200.0) < 1e-9


def test_place_option_helper_refuses_sto_without_broker_io():
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

    with pytest.raises(ValueError, match="STO|naked|short|covered|allow_naked|refuses"):
        ibkr._place_option_from_desk(
            FakeIB(),
            {"option_intent": "STO", "side": "sell", "contracts": 1, "right": "C",
             "expiry": "2026-10-17", "strike": 100, "ticker": "SPY", "type": "market", "signal_id": "x"},
            {"account_id": "DU123", "paper_mode": True, "endpoint": "x"},
            "ref",
        )


def test_option_positions_helper_exists_and_positions_stay_separate():
    assert callable(ibkr.get_option_positions)
    assert callable(ibkr.get_positions)
    # Source-level: get_positions still filters STK only.
    src = open(ibkr.__file__).read()
    assert 'secType != "STK"' in src or "secType != 'STK'" in src or 'secType!="STK"' in src.replace(" ", "")
    assert "def get_option_positions" in src
