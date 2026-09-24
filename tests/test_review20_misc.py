"""Actual stage-route concurrency and independent break-even payoff regressions."""
from contextlib import contextmanager
from datetime import datetime, timezone
import socket
import threading
from types import SimpleNamespace

from flask import Flask
import pytest

import desk_operations as operations
import options_desk as options
from trade_planner import estimate


@pytest.fixture(autouse=True)
def no_external_connections(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *args, **kwargs: pytest.fail("Network forbidden"))


def test_stage_compare_and_set_rejects_stale_request_after_review(tmp_path, monkeypatch):
    store = operations.EvidenceStore(tmp_path)
    with store.db() as connection:
        connection.execute("INSERT INTO suites VALUES(?,?,?,?)", ("suite", operations.utc(), "offline_evaluated", "{}"))
    captured, release = threading.Event(), threading.Event()

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchone(self):
            row = self.cursor.fetchone()
            if threading.current_thread().name == "review20-stale-stage":
                captured.set()
                assert release.wait(5), "test did not release the captured stage read"
            return row

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, values=()):
            cursor = self.connection.execute(statement, values)
            return Cursor(cursor) if statement.startswith("SELECT stage FROM suites") else cursor

    class StoreProxy:
        @contextmanager
        def db(self):
            with store.db() as connection:
                yield Connection(connection)

    monkeypatch.setattr(operations, "EvidenceStore", lambda folder: StoreProxy())
    app = Flask(__name__)
    app.config["TESTING"] = True
    operations.register(app, SimpleNamespace(DATA_DIR=tmp_path), None)
    replies, failures = {}, []

    def transition(key, stage):
        try:
            with app.test_client() as client:
                response = client.post("/api/operations/suites/suite/stage", json={
                    "stage": stage, "note": "Offline regression review note",
                })
                replies[key] = response.status_code, response.get_json()
        except BaseException as error:
            failures.append(error)

    stale = threading.Thread(target=transition, args=("stale", "paper_observation"), name="review20-stale-stage")
    stale.start()
    try:
        assert captured.wait(5), "stale request never captured the old stage"
        transition("observation", "paper_observation")
        transition("review", "reviewed")
    finally:
        release.set()
        stale.join(6)
    assert not stale.is_alive()
    assert failures == []
    assert replies["observation"][0] == replies["review"][0] == 200
    assert replies["stale"][0] == 400
    assert replies["stale"][1]["ok"] is False
    assert "changed during" in replies["stale"][1]["error"]
    with store.db() as connection:
        assert connection.execute("SELECT stage FROM suites WHERE id='suite'").fetchone()[0] == "reviewed"
        stages = [row[0] for row in connection.execute("SELECT stage FROM transitions ORDER BY id")]
    assert stages == ["paper_observation", "reviewed"], "rejected write must not append a transition"


def option_plan(strategy="call_debit", long=100, short=102, fee=.65):
    return options.plan(dict(symbol="SPY", expiry="2026-09-25", strategy=strategy,
                             long_strike=long, short_strike=short, contracts=1,
                             budget=250, fee_per_contract=fee))


def option_quote(plan, prices):
    now = datetime(2026, 9, 23, 18, tzinfo=timezone.utc).isoformat()
    rows = [dict(leg, bid=bid, ask=ask, bid_size=10, ask_size=10,
                 multiplier=100, con_id=index + 1, market_data_type=1, halted=0,
                 bid_observed_at=now, ask_observed_at=now)
            for index, (leg, (bid, ask)) in enumerate(zip(options.legs(plan), prices))]
    return dict(ok=True, source="IBKR streaming bid/ask", legs=rows)


def expiration_profit(plan, economics, stock):
    def intrinsic(strike):
        return max(stock - strike, 0) if plan["right"] == "C" else max(strike - stock, 0)
    payoff = intrinsic(plan["long_strike"])
    if plan["short_strike"] is not None:
        payoff -= intrinsic(plan["short_strike"])
    return payoff * plan["contracts"] * 100 - economics["entry_net_usd"] - 2 * economics["fee_per_side_usd"]


@pytest.mark.parametrize("strategy,long,short,prices", [
    ("call_debit", 100, 102, [(2.40, 2.49), (.50, .51)]),
    ("put_debit", 102, 100, [(2.40, 2.49), (.50, .51)]),
    ("call_credit", 102, 100, [(.49, .50), (.51, .52)]),
    ("put_credit", 100, 102, [(.49, .50), (.51, .52)]),
    ("long_put", 1, None, [(.98, .99)]),
])
def test_options_with_all_loss_payoffs_have_no_break_even(strategy, long, short, prices):
    plan = option_plan(strategy, long, short)
    result = options.economics(plan, option_quote(plan, prices))
    assert result["maximum_gain_usd"] < 0
    assert result["break_even_possible"] is False
    assert result["break_even_at_expiry"] is None
    assert "No attainable" in result["break_even_note"]
    for stock in (0, long, short or long, max(long, short or 0) + 10, 1000):
        assert expiration_profit(plan, result, stock) < 0


def test_fillable_options_preview_discloses_impossible_break_even(tmp_path, monkeypatch):
    now = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)
    monkeypatch.setattr(options, "now_utc", lambda: now)
    desk = SimpleNamespace(DATA_DIR=tmp_path, _CORRUPT_PATHS=set(), _lock=threading.RLock(),
                           _load_json=lambda path, default: default,
                           load_config=lambda: {"paper_research_enabled": True})
    service = options.OptionsDesk(desk)
    monkeypatch.setattr(service, "quote", lambda plan: option_quote(plan, [(2.4, 2.49), (.5, .51)]))
    result = service.preview(dict(symbol="SPY", expiry="2026-09-25", strategy="call_debit",
                                  long_strike=100, short_strike=102, contracts=1,
                                  budget=250, fee_per_contract=.65))
    assert result["review_id"] and not result["blockers"]
    assert result["estimate"]["maximum_gain_usd"] == pytest.approx(-1.6)
    assert result["estimate"]["break_even_at_expiry"] is None
    assert result["estimate"]["break_even_possible"] is False


@pytest.mark.parametrize("strategy,long,short,fee,prices,expected", [
    ("call_debit", 100, 102, 12.5, [(1.9, 2), (.5, .6)], 102),
    ("put_debit", 102, 100, 12.5, [(1.9, 2), (.5, .6)], 100),
    ("call_credit", 102, 100, 25, [(.4, .5), (1.5, 1.6)], 100),
    ("put_credit", 100, 102, 25, [(.4, .5), (1.5, 1.6)], 102),
    ("long_put", 1, None, 25, [(.4, .5)], 0),
])
def test_zero_maximum_profit_still_has_an_attainable_break_even(strategy, long, short, fee, prices, expected):
    plan = option_plan(strategy, long, short, fee)
    result = options.economics(plan, option_quote(plan, prices))
    assert result["maximum_gain_usd"] == pytest.approx(0)
    assert result["break_even_possible"] is True
    assert result["break_even_at_expiry"] == expected
    assert expiration_profit(plan, result, expected) == pytest.approx(0)


@pytest.mark.parametrize("strategy,long,short", [
    ("long_call", 100, None), ("long_put", 100, None),
    ("call_debit", 100, 102), ("put_debit", 102, 100),
    ("call_credit", 102, 100), ("put_credit", 100, 102),
])
def test_normal_options_break_even_solves_actual_expiration_payoff(strategy, long, short):
    plan = option_plan(strategy, long, short)
    prices = [(1.1, 1.2)] + ([] if short is None else [(2.4, 2.5) if plan["kind"] == "credit" else (.5, .6)])
    result = options.economics(plan, option_quote(plan, prices))
    assert result["break_even_possible"] is True
    assert result["break_even_note"] is None
    assert expiration_profit(plan, result, result["break_even_at_expiry"]) == pytest.approx(0, abs=.01)


@pytest.mark.parametrize("borrow,expected,possible", [(9, None, False), (8, 0, True), (0, 80, True)])
def test_short_stock_break_even_unreachable_zero_boundary_and_normal(borrow, expected, possible):
    result = estimate(dict(budget=10, price=100, exit_price=.0001, direction="short",
                           order_type="limit", fractional=True, fee_mode="custom",
                           entry_fee=1, exit_fee=1, borrow_fee=borrow, slippage_bps=0))
    assert result["break_even_possible"] is possible
    assert result["break_even_price"] == expected
    if possible:
        assert result["shares"] * (result["entry_price"] - expected) - result["fees_assumed"] == pytest.approx(0)
    else:
        assert result["net_pnl_estimate"] == pytest.approx(-1.00001)
        assert result["entry_value"] - result["fees_assumed"] < 0
        assert "No attainable" in result["note"]


def test_stock_unknown_fees_and_zero_quantity_remain_unknown():
    unknown = estimate(dict(budget=10, price=100, fee_mode="unknown"))
    assert unknown["break_even_possible"] is None and unknown["break_even_price"] is None
    empty = estimate(dict(budget=10, price=100, fractional=False, fee_mode="custom"))
    assert empty["shares"] == 0
    assert empty["break_even_possible"] is None and empty["break_even_price"] is None
