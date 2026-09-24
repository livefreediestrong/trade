"""Regression tests for option order direction, risk sizing and adapter gates."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import auto_live_options as alo
import broker_ibkr as ibkr
import live_agent
import order_terms


def _net_legs(order):
    """Effective leg actions: IBKR reverses every leg of a SOLD combo."""
    flip = order["combo_action"] == "SELL"
    return {leg["strike"]: ("SELL" if leg["action"] == "BUY" else "BUY") if flip else leg["action"]
            for leg in order["legs"]}


@pytest.mark.parametrize("strategy,long_s,short_s", [
    ("call_debit", 100, 105), ("put_debit", 100, 95),
    ("call_credit", 105, 100), ("put_credit", 95, 100),
])
def test_bag_close_unwinds_the_open(strategy, long_s, short_s):
    common = {"strategy": strategy, "expiry": "2026-10-16", "long_strike": long_s, "short_strike": short_s}
    opened = order_terms.canonical_bag_order({"suggested_shares": 1}, {**common, "intent": "OPEN"})
    closed = order_terms.canonical_bag_order({"suggested_shares": 1}, {**common, "intent": "CLOSE"})
    assert _net_legs(opened) == {float(long_s): "BUY", float(short_s): "SELL"}
    assert _net_legs(closed) == {float(long_s): "SELL", float(short_s): "BUY"}
    # The combo definition is the same; only the combo direction flips.
    assert opened["legs"] == closed["legs"]
    assert opened["combo_action"] != closed["combo_action"]


def test_option_limits_reject_sub_penny_prices():
    with pytest.raises(ValueError, match="whole cents"):
        order_terms.canonical_option_order(
            {"suggested_shares": 1, "side": "buy"},
            {"type": "limit", "limit_price": 0.4302, "right": "C", "expiry": "2026-10-16",
             "strike": 100, "intent": "BTO"},
        )
    with pytest.raises(ValueError, match="whole cents"):
        order_terms.canonical_bag_order(
            {"suggested_shares": 1},
            {"type": "limit", "limit_price": 0.4302, "strategy": "call_debit", "expiry": "2026-10-16",
             "long_strike": 100, "short_strike": 105},
        )


def test_round_option_price_uses_ticks_and_direction():
    assert float(order_terms.round_option_price(0.4302, buying=True)) == pytest.approx(0.43)
    assert float(order_terms.round_option_price(0.4302, buying=False)) == pytest.approx(0.44)
    assert float(order_terms.round_option_price(3.47, buying=True)) == pytest.approx(3.45)
    assert float(order_terms.round_option_price(3.47, buying=False)) == pytest.approx(3.50)
    assert float(order_terms.round_option_price(3.47, buying=True, combo=True)) == pytest.approx(3.47)


def test_option_max_loss_by_structure():
    base = {"contracts": 2}
    assert order_terms.option_max_loss({**base, "option_intent": "BTO", "right": "C", "strike": 100}, 1.5) == 300
    credit = {**base, "asset_type": "BAG", "option_intent": "OPEN", "option_strategy": "put_credit",
              "long_strike": 95, "short_strike": 100}
    assert order_terms.option_max_loss(credit, 0.5) == pytest.approx((5 - 0.5) * 100 * 2)
    debit = {**base, "asset_type": "BAG", "option_intent": "OPEN", "option_strategy": "call_debit",
             "long_strike": 100, "short_strike": 105}
    assert order_terms.option_max_loss(debit, 1.2) == pytest.approx(240)
    short_put = {**base, "option_intent": "STO", "right": "P", "strike": 50}
    assert order_terms.option_max_loss(short_put, 1.0) == pytest.approx(49 * 100 * 2)
    short_call = {**base, "option_intent": "STO", "right": "C", "strike": 50}
    assert order_terms.option_max_loss(short_call, 1.0) == pytest.approx(50 * 100 * 2)
    covered = {**short_call, "covered": True}
    assert order_terms.option_max_loss(covered, 1.0) == pytest.approx(200)


def _cfg(**policy):
    auto = alo.validate({"enabled": True, "strategies": ["put_credit"], "max_contracts": 20})
    return {"live_agent": {"auto_options": auto, "policy": {"order_type": "limit", **policy}}}


def test_credit_spread_sized_by_max_loss_not_credit():
    signal = {"asset_type": "BAG", "side": "sell", "contracts": 20, "option_strategy": "put_credit",
              "option_intent": "OPEN", "right": "P", "expiry": "2026-10-16",
              "long_strike": 95.0, "short_strike": 100.0}
    order = alo.execution_terms_option(signal, _cfg(), 0.50, {}, 500.0)
    # Credit sizing allowed 10 contracts; the real max loss is ~$450 each.
    assert order["contracts"] == 1
    assert order["limit"] == pytest.approx(round(order["limit"], 2))
    with pytest.raises(ValueError, match="maximum loss"):
        alo.execution_terms_option(dict(signal), _cfg(), 0.50, {}, 400.0)


def test_auto_limit_premium_lands_on_a_tick():
    signal = {"asset_type": "OPT", "side": "buy", "contracts": 1, "option_strategy": "long_call",
              "option_intent": "BTO", "right": "C", "expiry": "2026-10-16", "strike": 100.0}
    cfg = _cfg()
    cfg["live_agent"]["auto_options"]["strategies"] = ["long_call"]
    order = alo.execution_terms_option(signal, cfg, 0.43, {}, 1000.0)
    assert order["limit"] == pytest.approx(0.43)
    order = alo.execution_terms_option(dict(signal), cfg, 3.47, {}, 1000.0)
    assert order["limit"] == pytest.approx(3.45)


def test_candidate_contracts_do_not_come_from_stock_shares():
    auto = alo.validate({"enabled": True, "strategies": ["long_call"], "max_contracts": 3})
    exp = (date.today() + timedelta(days=20)).isoformat()
    cand = alo.build_option_candidate(
        {"side": "buy", "ticker": "SPY", "suggested_shares": 1}, auto,
        chain={"expirations": [exp], "strikes": [99, 100, 101]}, underlying_price=100.0,
    )
    assert cand["contracts"] == 3


def test_pick_expiry_counts_from_the_market_date():
    assert alo.pick_expiry(["2026-10-16"], 5, 5, today=date(2026, 10, 11)) == "2026-10-16"
    ny_today = datetime.now(alo._MARKET_TZ).date()
    target = (ny_today + timedelta(days=7)).isoformat()
    assert alo.pick_expiry([target], 7, 7) == target


def test_auto_options_refuse_delayed_quotes():
    live_agent._require_live_option_data([{"market_data_type": 1}])
    for kind in (2, 3, 4, None):
        with pytest.raises(ValueError, match="not live"):
            live_agent._require_live_option_data([{"market_data_type": 1}, {"market_data_type": kind}])


# --- adapter gates -----------------------------------------------------------

class _Refused(Exception):
    pass


def _pos(sec, symbol, qty, right="", con_id=0):
    return SimpleNamespace(account="DU1", position=qty,
                           contract=SimpleNamespace(secType=sec, symbol=symbol, currency="USD",
                                                    right=right, conId=con_id))


class _FakeIB:
    def __init__(self, positions=(), trades=(), buying_power="1000000"):
        self._positions, self._trades, self._bp = list(positions), list(trades), buying_power
        self.placed = []
        self._next_con = 1000

    def qualifyContracts(self, c):
        self._next_con += 1
        c.conId, c.secType, c.multiplier, c.localSymbol = int(c.strike * 10) or self._next_con, "OPT", "100", "X"
        return [c]

    def positions(self, *a):
        return self._positions

    def openTrades(self):
        return self._trades

    def accountSummary(self, *a):
        return [SimpleNamespace(tag="BuyingPower", value=self._bp)]

    def placeOrder(self, *a):
        raise _Refused("reached placeOrder")


def _sto(**kw):
    order = {"option_intent": "STO", "side": "sell", "contracts": 1, "right": "C", "expiry": "2026-10-16",
             "strike": 100, "ticker": "SPY", "type": "limit", "limit": 1.5, "covered": True,
             "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    order.update(kw)
    return order


IDENTITY = {"account_id": "DU1", "paper_mode": True, "endpoint": "x"}


def test_covered_call_counts_existing_short_calls():
    ib = _FakeIB([_pos("STK", "SPY", 100), _pos("OPT", "SPY", -1, "C", 555)])
    with pytest.raises(ValueError, match="unpledged"):
        ibkr._place_option_from_desk(ib, _sto(), IDENTITY, "ref")


def test_covered_call_counts_working_call_sales():
    trade = SimpleNamespace(order=SimpleNamespace(account="DU1", action="SELL", totalQuantity=1),
                            orderStatus=SimpleNamespace(filled=0),
                            contract=SimpleNamespace(secType="OPT", symbol="SPY", right="C", conId=555))
    ib = _FakeIB([_pos("STK", "SPY", 100)], [trade])
    with pytest.raises(ValueError, match="unpledged"):
        ibkr._place_option_from_desk(ib, _sto(), IDENTITY, "ref")


def test_covered_call_with_free_shares_reaches_the_final_risk_check():
    ib = _FakeIB([_pos("STK", "SPY", 200), _pos("OPT", "SPY", -1, "C", 555)])
    # Passes the coverage check and stops at the unrelated risk authorization.
    with pytest.raises(ValueError, match="risk authorization"):
        ibkr._place_option_from_desk(ib, _sto(), IDENTITY, "ref")


def test_naked_short_put_buying_power_uses_strike_risk():
    # $1.50 premium: premium floor is $150, cash-secured risk is $9,850.
    ib = _FakeIB(buying_power="5000")
    with pytest.raises(ValueError, match="worst-case loss"):
        ibkr._place_option_from_desk(ib, _sto(right="P", covered=False, allow_naked=True), IDENTITY, "ref")


def _bag_close(**kw):
    order = {"asset_type": "BAG", "option_intent": "CLOSE", "option_strategy": "call_debit", "side": "sell",
             "contracts": 1, "right": "C", "expiry": "2026-10-16", "long_strike": 100, "short_strike": 105,
             "ticker": "SPY", "type": "limit", "limit": 1.2,
             "valid_until": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}
    order.update(kw)
    return order


def test_bag_close_requires_the_spread_to_be_held():
    with pytest.raises(ValueError, match="does not hold this spread"):
        ibkr._place_bag_from_desk(_FakeIB(), _bag_close(), IDENTITY, "ref")
    held = _FakeIB([_pos("OPT", "SPY", 1, "C", 1000), _pos("OPT", "SPY", -1, "C", 1050)])
    with pytest.raises(ValueError, match="risk authorization"):
        ibkr._place_bag_from_desk(held, _bag_close(), IDENTITY, "ref")
