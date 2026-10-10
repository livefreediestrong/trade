"""Paper stop qualification: real state machine and adapter, intercepted I/O only."""
import copy
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
from ib_async import Stock, StopOrder, OrderStatus, Trade, OrderState, IB

import app as desk
import broker_ibkr as ibkr
import broker_router as router
import live_agent as agent
import stock_protection as stops
from test_execution_repairs import execution, BASE  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_trade_intelligence import entered, _hold  # noqa: F401

REF = "tstop-" + "a" * 32
IDENTITY = dict(broker="ibkr", account_id="DU123", paper_mode=True, client_id=37, endpoint="127.0.0.1:4002")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(ibkr, "_WORKER", threading.current_thread())
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Broker network forbidden"))


def view(status=None, qty=2, filled=0, held=2, available=2, price=99.2):
    return {"held": held, "available": available, "min_tick": .01,
            "order": None if status is None else dict(status=status, quantity=qty, filled=filled,
                                                     stop_price=price, order_id=7, client_id=37,
                                                     permanent_id=700, con_id=42, why_held="")}


def record(action=None, expected=None, **over):
    r = {"ref": REF, "position_key": "b" * 64, "state": "protected"}
    if action:
        r["intent"] = dict(action=action, expected=expected or view("Submitted"), total_quantity=2, stop_price=99.2)
    return dict(r, **over)


@pytest.mark.parametrize("value,tick,expected", [(99.201, .01, 99.21), (99.2, .05, 99.2), (99.21, .05, 99.25)])
def test_rounding_only_tightens_stop(value, tick, expected):
    assert stops.stop_price(value, tick) == expected


@pytest.mark.parametrize("value", [True, 0, -1, "NaN", "Infinity"])
def test_bad_price_fails(value):
    with pytest.raises(ValueError):
        stops.stop_price(value, .01)


@pytest.mark.parametrize("status", [None, "PendingSubmit", "PendingCancel", "Inactive", "Filled"])
def test_uncertain_or_terminal_order_never_resubmits(status):
    p = stops.decision(record("create"), view(status), 2, 99.2)
    assert p["state"] in {"review", "waiting"} and not p.get("allow_exit")


def test_partial_fill_resizes_total_not_remaining_and_never_loosens():
    p = stops.decision(record(accounting_settled=True, accounted_qty=2), view("Submitted", qty=5, filled=2, held=3, available=3), 2, 98)
    assert p["state"] == "modify" and p["stop_price"] == 99.2


def test_pending_modification_needs_exact_ack():
    r = record("modify")
    assert stops.decision(r, view("Submitted", price=98), 2, 99.2)["state"] == "review"
    assert stops.decision(r, view("Submitted"), 2, 99.2)["state"] == "protected"


@pytest.mark.parametrize("reason", ["take_profit", "max_hold", "end_of_day", "disable"])
def test_separate_exit_waits_for_cancel_ack(reason):
    assert stops.decision(record(), view("Submitted"), 2, 99.2, reason)["state"] == "cancel"
    assert stops.decision(record("cancel"), view("Submitted"), 2, 99.2, reason)["state"] == "waiting"
    assert stops.decision(record("cancel"), view("Cancelled"), 2, 99.2, reason)["allow_exit"] is True


def test_fill_during_cancellation_waits_for_positions_then_requires_review():
    r = record("cancel", expected=view("Submitted"))
    assert stops.decision(r, view("Cancelled", filled=1), 2, 99.2, "take_profit")["state"] == "review"
    p = stops.decision(r, view("Cancelled", filled=1, held=1, available=1), 1, 99.2, "take_profit")
    assert p["state"] == "review" and not p.get("allow_exit")


def test_unknown_external_cancel_not_automatically_replaced():
    assert stops.decision(record(), view("Cancelled"), 2, 99.2)["state"] == "review"


def test_cancelled_exit_no_longer_due_restores_with_new_intent():
    assert stops.decision(record("cancel"), view("Cancelled"), 2, 99.2)["state"] == "create"


def test_trigger_does_not_send_an_extra_sell():
    assert stops.decision(record(), view("Submitted"), 2, 99.2, "stop_loss")["state"] == "waiting"


def test_competing_sells_or_call_cover_block_stop_creation():
    assert stops.decision(None, view(available=1), 2, 99.2)["state"] == "review"


class PaperBroker:
    """Faithful raw openOrder callbacks, with a deliberately less complete cache."""
    def __init__(self):
        self.identity = copy.deepcopy(IDENTITY)
        self.contract = Stock("TEST", "SMART", "USD", conId=42, primaryExchange="NASDAQ")
        self.native = StopOrder("SELL", 2, 99.2, orderId=7, clientId=37, permId=700,
                                account="DU123", orderRef=REF, tif="GTC", outsideRth=False)
        self.status, self.filled, self.held = "Submitted", 0, 2
        self.present = True
        self.cached = None
        self.extra = []
        self.sent, self.cancelled = [], []
        self.wrapper = NS(openOrder=lambda *a: None)
        self.after_positions = lambda: None
        self.connected = True
        self.raise_send = False
    def qualifyContracts(self, contract):
        contract.conId, contract.primaryExchange = 42, "NASDAQ"
        return [contract]
    def reqContractDetails(self, contract):
        return [NS(contract=contract, minTick=.01)]
    def reqAllOpenOrders(self):
        for t in self.extra:
            self.wrapper.openOrder(t.order.orderId, t.contract, t.order, OrderState(status=t.orderStatus.status))
        if not self.present or self.status in stops.CANCELLED | {"Filled"}:
            self.cached = None
            return list(self.extra)
        raw = copy.deepcopy(self.native)
        self.wrapper.openOrder(raw.orderId, self.contract, raw, OrderState(status=self.status))
        # Simulate a cached GTC TIF even when the broker changed it to DAY.
        cached_order = copy.deepcopy(raw)
        cached_order.tif = "GTC"
        self.cached = Trade(self.contract, cached_order, OrderStatus(status=self.status, filled=self.filled))
        return [self.cached, *self.extra]
    def reqCompletedOrders(self, apiOnly=False):
        if self.present and self.status in stops.CANCELLED | {"Filled"}:
            o = copy.deepcopy(self.native)
            o.orderId, o.clientId, o.filledQuantity = 0, 0, self.filled
            return [Trade(self.contract, o, OrderStatus(status=self.status))]
        return []
    def positions(self, account):
        return [NS(account=self.identity["account_id"], contract=self.contract, position=self.held)]
    def reqPositions(self):
        result = copy.deepcopy(self.positions(self.identity["account_id"]))
        self.after_positions()
        return result
    def openTrades(self):
        return ([self.cached] if self.cached else []) + self.extra
    def isConnected(self):
        return self.connected
    def placeOrder(self, contract, order):
        self.sent.append(copy.deepcopy(order))
        if self.raise_send:
            raise TimeoutError("simulated uncertain dispatch")
    def cancelOrder(self, order):
        self.cancelled.append(copy.deepcopy(order))


@pytest.fixture
def paper_broker(monkeypatch):
    b = PaperBroker()
    @contextmanager
    def session():
        yield b
    monkeypatch.setattr(ibkr, "_session", session)
    monkeypatch.setattr(ibkr, "_identity", lambda ib: copy.deepcopy(ib.identity))
    monkeypatch.setattr(ibkr, "_ACCOUNT_READY", {"DU123": True})
    monkeypatch.setattr(ibkr, "_SERVER_UNAVAILABLE", False)
    monkeypatch.setattr(ibkr, "_RESYNC_REQUIRED", False)
    return b


def command(b, action="modify", **over):
    observed = ibkr.observe_stock_stop("TEST", REF, b.identity)
    assert observed["ok"], observed
    return dict(action=action, ref=REF, previous_ref=REF, ticker="TEST", broker_identity=copy.deepcopy(b.identity),
                expected=observed["view"], quantity=2, total_quantity=2, stop_price=99.5,
                valid_until=(datetime.now(timezone.utc)+timedelta(seconds=20)).isoformat(), **over)


def test_adapter_modifies_same_order_id_and_rechecks_account(paper_broker):
    b = paper_broker
    result = ibkr.change_stock_stop(command(b), lambda: True)
    assert result["ok"]
    sent = b.sent[0]
    assert (sent.action, sent.orderType, sent.orderId, sent.orderRef, sent.totalQuantity, sent.auxPrice) == ("SELL", "STP", 7, REF, 2, 99.5)
    assert sent.tif == "GTC" and sent.outsideRth is False


def test_adapter_create_and_cancel_are_individually_scoped(paper_broker):
    b = paper_broker
    assert ibkr.change_stock_stop(command(b, "cancel"), lambda: True)["ok"]
    assert len(b.cancelled) == 1 and b.cancelled[0].orderId == 7
    b.present = False
    assert ibkr.change_stock_stop(command(b, "create"), lambda: True)["ok"]
    assert len(b.sent) == 1 and b.sent[0].orderRef == REF and b.sent[0].orderId == 0


@pytest.mark.parametrize("mutation", ["live", "other_account", "other_client", "client_zero", "disconnect", "account_not_ready"])
def test_adapter_context_change_sends_nothing(paper_broker, monkeypatch, mutation):
    b = paper_broker
    cmd = command(b)
    if mutation == "live": b.identity.update(paper_mode=False, account_id="U123")
    if mutation == "other_account": b.identity["account_id"] = "DU999"
    if mutation == "other_client": b.identity["client_id"] = 38
    if mutation == "client_zero": b.identity["client_id"] = 0
    if mutation == "disconnect": b.connected = False
    if mutation == "account_not_ready": monkeypatch.setitem(ibkr._ACCOUNT_READY, "DU123", False)
    result = ibkr.change_stock_stop(cmd, lambda: True)
    assert result["ok"] is False and result["submission_attempted"] is False
    assert not b.sent and not b.cancelled


@pytest.mark.parametrize("field,value", [("tif", "DAY"), ("outsideRth", True), ("clientId", 9),
                                        ("parentId", 10), ("goodAfterTime", "tomorrow"), ("orderType", "STP LMT")])
def test_actual_broker_terms_override_stale_cache(paper_broker, field, value):
    setattr(paper_broker.native, field, value)
    result = ibkr.observe_stock_stop("TEST", REF, paper_broker.identity)
    assert result["ok"] is False
    assert not paper_broker.sent


def test_actual_library_wrapper_retains_tif_but_raw_reader_detects_it():
    # No connection: exercise the actual maintained library's callback parser.
    ib = IB()
    c = Stock("TEST", "SMART", "USD", conId=42)
    o = StopOrder("SELL", 2, 99, account="DU123", orderRef=REF, orderId=7, clientId=37, permId=700, tif="GTC")
    ib.wrapper.openOrder(7, c, o, OrderState(status="Submitted"))
    original = ib.wrapper.openOrder
    def request():
        ib.wrapper._results["openOrders"] = []
        changed = copy.deepcopy(o)
        changed.tif = "DAY"
        ib.wrapper.openOrder(7, c, changed, OrderState(status="PreSubmitted"))
        return ib.wrapper._results.pop("openOrders")
    ib.reqAllOpenOrders = request
    observed = ibkr._reported_open_orders(ib)
    assert ib.wrapper.openOrder == original
    assert observed[0].order.tif == "DAY" and observed[0].orderStatus.status == "PreSubmitted"
    assert ib.trades()[0].order.tif == "GTC"  # Demonstrates why cache alone is insufficient.


def test_partial_modification_includes_executed_quantity(paper_broker):
    b = paper_broker
    b.native.totalQuantity, b.filled, b.held = 5, 2, 3
    cmd = command(b)
    cmd.update(quantity=2, total_quantity=4)
    assert ibkr.change_stock_stop(cmd, lambda: True)["ok"]
    assert b.sent[0].totalQuantity == 4


def test_stop_never_reserves_shares_already_promised_to_another_sell(paper_broker):
    b = paper_broker
    b.present = False
    other = StopOrder("SELL", 1, 98, account="DU123", orderRef="manual", orderId=8, clientId=37)
    b.extra = [Trade(b.contract, other, OrderStatus(status="Submitted", filled=0))]
    cmd = command(b, "create")
    assert cmd["expected"]["available"] == 1
    assert not ibkr.change_stock_stop(cmd, lambda: True)["ok"] and not b.sent


def test_duplicate_stop_references_are_never_adopted(paper_broker):
    b = paper_broker
    other = copy.deepcopy(b.native)
    other.orderId, other.permId = 8, 701
    b.extra = [Trade(b.contract, other, OrderStatus(status="Submitted", filled=0))]
    assert not ibkr.observe_stock_stop("TEST", REF, b.identity)["ok"]


def test_confirmed_cancel_replacement_needs_new_unused_reference(paper_broker):
    b = paper_broker
    b.status = "Cancelled"
    cmd = command(b, "create")
    assert not ibkr.change_stock_stop(cmd, lambda: True)["ok"]
    cmd["ref"] = "tstop-" + "c" * 32
    assert ibkr.change_stock_stop(cmd, lambda: True)["ok"]
    assert b.sent[0].orderRef == cmd["ref"]


@pytest.mark.parametrize("mutation", ["partial_fill", "shares", "price", "expired", "pause", "loosen", "too_many", "wrong_total"])
def test_final_changes_and_invalid_terms_block_send(paper_broker, mutation):
    b = paper_broker
    cmd = command(b)
    authorized = lambda: True
    if mutation == "partial_fill": b.filled = 1
    if mutation == "shares": b.held = 1
    if mutation == "price": b.native.auxPrice = 99.3
    if mutation == "expired": cmd["valid_until"] = "2000-01-01T00:00:00+00:00"
    if mutation == "pause": authorized = lambda: False
    if mutation == "loosen": cmd["stop_price"] = 90
    if mutation == "too_many": cmd.update(quantity=3, total_quantity=3)
    if mutation == "wrong_total": cmd["total_quantity"] = 3
    assert not ibkr.change_stock_stop(cmd, authorized)["ok"]
    assert not b.sent


def test_callback_during_last_read_cannot_oversell(paper_broker):
    b = paper_broker
    cmd = command(b)
    b.after_positions = lambda: setattr(b, "held", 1)
    assert not ibkr.change_stock_stop(cmd, lambda: True)["ok"]
    assert not b.sent


def test_adapter_uncertain_send_reports_attempt_and_never_retries(paper_broker):
    b = paper_broker
    b.raise_send = True
    result = ibkr.change_stock_stop(command(b), lambda: True)
    assert not result["ok"] and result["submission_attempted"] is True and len(b.sent) == 1


def enable_stop_test(entered):
    cfg = desk.load_config()
    cfg["live_agent"]["policy"]["broker_stops"] = True
    desk.save_config(cfg)
    return cfg, agent.validate(cfg["live_agent"]["policy"])


@pytest.fixture
def lifecycle(entered, monkeypatch):
    cfg, policy = enable_stop_test(entered)
    _hold(monkeypatch, 2, 100.1)
    state = NS(view=view(), commands=[], cfg=cfg, policy=policy, service=entered.service, entered=entered)
    # These lifecycle unit cases start after the worker's execution-history read.
    # Startup/read failures are exercised separately in test_stop_fills.
    state.service.stop_accounting_identity = copy.deepcopy(cfg["broker_identity"])
    monkeypatch.setitem(router.__dict__, "observe_stock_stop", lambda *a: {"ok": True, "view": copy.deepcopy(state.view)})
    def dispatch(cmd, authorized):
        saved = state.service.load()["native_stops"]["TEST"]
        assert saved["intent"] == cmd and saved["state"] == "pending"
        assert authorized()
        state.commands.append(copy.deepcopy(cmd))
        return {"ok": True, "submission_attempted": True}
    monkeypatch.setitem(router.__dict__, "change_stock_stop", dispatch)
    return state


def manage(s):
    return s.service._manage_exits(s.cfg, s.policy)


def test_intent_precedes_io_and_restart_does_not_repeat_missing_order(lifecycle):
    s = lifecycle
    assert manage(s) is True
    assert len(s.commands) == 1 and s.commands[0]["action"] == "create"
    s.service = agent.LiveAgent(desk)
    assert manage(s) is True
    assert len(s.commands) == 1
    assert s.service.load()["native_stops"]["TEST"]["state"] == "review"


def test_exact_ack_enables_protected_state_then_target_waits_for_cancel(lifecycle, monkeypatch):
    s = lifecycle
    manage(s)
    price = s.commands[0]["stop_price"]
    s.view = view("Submitted", price=price)
    assert manage(s) is False
    assert s.service.load()["native_stops"]["TEST"]["state"] == "protected"
    _hold(monkeypatch, 2, 102)
    assert manage(s) is True
    assert [c["action"] for c in s.commands] == ["create", "cancel"]
    assert len(s.entered.sent) == 1  # Only the fixture's original entry.
    manage(s)
    assert len(s.commands) == 2 and len(s.entered.sent) == 1
    s.view["order"]["status"] = "Cancelled"
    manage(s)
    assert len(s.entered.sent) == 2 and s.entered.sent[-1]["side"] == "sell"
    assert not s.service.load()["native_stops"]


def test_native_partial_entry_is_protected_despite_unresolved_entry(lifecycle):
    ledger = desk.load_ledger()
    ledger["pending_broker_orders"] = [{"signal": {"id": "pending-entry", "ticker": "TEST"}}]
    desk.save_ledger(ledger)
    manage(lifecycle)
    assert len(lifecycle.commands) == 1 and lifecycle.commands[0]["quantity"] == 2


def test_save_failure_prevents_stop_submission(lifecycle, monkeypatch):
    def fail(*a): raise OSError("disk full")
    monkeypatch.setattr(lifecycle.service, "save", fail)
    assert manage(lifecycle) is True
    assert not lifecycle.commands


def test_stop_fill_preserves_evidence_and_blocks_new_entries(lifecycle, monkeypatch):
    s = lifecycle
    manage(s)
    s.view = view("Filled", filled=2, held=0, available=0)
    _hold(monkeypatch, 0, 99)
    assert manage(s) is True
    raw = s.service.load()
    assert "TEST" in raw["managed"] and raw["native_stops"]["TEST"]["last_observed"]["filled"] == 2
    assert stops.entry_error(raw, s.cfg)
    assert len(s.commands) == 1 and len(s.entered.sent) == 1


def test_quote_outage_still_protects_confirmed_holdings(lifecycle, monkeypatch):
    monkeypatch.setitem(router.__dict__, "stock_quote", lambda _: {"ok": False})
    manage(lifecycle)
    assert len(lifecycle.commands) == 1


def test_protection_toggle_off_does_not_forget_outstanding_order(lifecycle):
    s = lifecycle
    manage(s)
    s.view = view("Submitted", price=s.commands[0]["stop_price"])
    s.cfg["live_agent"]["policy"]["broker_stops"] = False
    s.policy = agent.validate(s.cfg["live_agent"]["policy"])
    desk.save_config(s.cfg)
    manage(s)
    assert [c["action"] for c in s.commands] == ["create", "cancel"]
    assert stops.entry_error(s.service.load(), s.cfg)


def test_stale_protection_blocks_new_entries(lifecycle):
    s = lifecycle
    manage(s)
    s.view = view("Submitted", price=s.commands[0]["stop_price"])
    manage(s)
    raw = s.service.load()
    assert stops.entry_error(raw, s.cfg) is None
    assert stops.entry_error(raw, s.cfg, datetime.now(timezone.utc)+timedelta(seconds=31))


def test_corrupt_native_record_is_not_silently_reset(lifecycle):
    raw = lifecycle.service.load()
    raw["native_stops"] = {"TEST": {"ref": REF}}
    lifecycle.service.save(raw)
    with pytest.raises(ValueError, match="recovery"):
        lifecycle.service.load()


def test_pause_during_observation_prevents_durable_intent_and_send(lifecycle, monkeypatch):
    s = lifecycle
    def pause(*a):
        cfg = desk.load_config()
        cfg["live_agent"]["enabled"] = False
        desk.save_config(cfg)
        return {"ok": True, "view": view()}
    monkeypatch.setitem(router.__dict__, "observe_stock_stop", pause)
    manage(s)
    assert not s.commands and not s.service.load().get("native_stops")


def test_failed_current_observation_cannot_reuse_last_good_status(lifecycle, monkeypatch):
    s = lifecycle
    manage(s)
    s.view = view("Submitted", price=s.commands[0]["stop_price"])
    manage(s)
    monkeypatch.setitem(router.__dict__, "observe_stock_stop", lambda *a: {"ok": False, "error": "offline"})
    assert manage(s) is True


def test_stop_submission_exception_retains_intent_and_prevents_retry(lifecycle, monkeypatch):
    count = []
    def fail(*a):
        count.append(1)
        raise TimeoutError("uncertain")
    monkeypatch.setitem(router.__dict__, "change_stock_stop", fail)
    manage(lifecycle)
    assert lifecycle.service.load()["native_stops"]["TEST"]["last_request"]["ok"] is False
    manage(lifecycle)
    assert count == [1]


def test_first_fill_schedules_protection_before_next_research(live, monkeypatch):
    cfg = desk.load_config()
    cfg["live_agent"]["policy"]["broker_stops"] = True
    desk.save_config(cfg)
    captured = []
    def check(cfg, policy):
        if live.service.load().get("managed"):
            captured.append(copy.deepcopy(live.service.load()["managed"]))
        return False
    monkeypatch.setattr(live.service, "_manage_exits", check)
    live.service.tick()
    assert len(live.sent) == 1 and captured[0]["TEST"]["shares"] == 2


def test_manual_route_cannot_bypass_unknown_stop(lifecycle, monkeypatch):
    s = lifecycle
    manage(s)
    cfg = desk.load_config()
    cfg.update(mode="live_manual", session_active=True)
    cfg["live_agent"]["enabled"] = False
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "fetch_last_price", lambda *a: pytest.fail("Must block before quote or submission"))
    result = desk.execute_gated_broker_or_paper({"id": "manual-next", "ticker": "TEST", "side": "sell"}, cfg, source="test", via="test")
    assert result["ok"] is False and "stop" in result["error"].lower()
    assert len(s.entered.sent) == 1


def test_another_accounts_stop_does_not_block_current_account(lifecycle):
    manage(lifecycle)
    assert stops.pending_error(lifecycle.service.load(), dict(lifecycle.cfg["broker_identity"], account_id="DIFFERENT")) is None


def test_live_activation_is_rejected_even_with_real_confirmation(live, monkeypatch):
    cfg = desk.load_config()
    cfg["live_agent"]["policy"]["broker_stops"] = True
    cfg["live_agent"]["enabled"] = False
    cfg["broker_identity"].update(paper_mode=False, account_id="U123")
    desk.save_config(cfg)
    monkeypatch.setitem(router.__dict__, "verify_execution_context", lambda: {"ok": True, "identity": cfg["broker_identity"]})
    reply = desk.app.test_client().post("/api/live-agent/start", base_url=BASE,
        json={"revision": cfg["live_agent"]["revision"], "identity": cfg["broker_identity"], "confirm": "REAL"})
    assert reply.status_code == 400 and "paper" in reply.json["error"].lower()
    assert desk.load_config()["live_agent"]["enabled"] is False
    assert not live.sent


@pytest.mark.parametrize("changes", [{"broker_stops": "yes"}, {"broker_stops": True, "protective_exits": False}])
def test_invalid_policy_combinations(changes):
    with pytest.raises(ValueError): agent.validate(dict(agent.DEFAULTS, **changes))
