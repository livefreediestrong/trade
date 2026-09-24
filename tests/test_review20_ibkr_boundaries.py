"""Final preflight and corrected executions using fake Gateway I/O only."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from ib_insync import IB, CommissionReport, Execution, Fill, Order, OrderStatus, Stock, Trade
from ib_insync.util import UNSET_DOUBLE

from test_broker_lifecycle import execution, gateway, desk, ibkr, BASE, REAL_DAY_PNL, REAL_POSITION_QTY, REAL_SUBMIT
from broker_fixtures import deadline


@pytest.mark.parametrize("changed", ["loss", "outage", "resync", "equity", "unset", "account"])
def test_final_preflight_change_blocks_before_place_order(execution, gateway, monkeypatch, changed):
    monkeypatch.setattr(desk, "_broker_day_pnl", REAL_DAY_PNL)
    monkeypatch.setattr(desk, "_broker_position_qty", REAL_POSITION_QTY)
    monkeypatch.setattr(desk, "live_broker_place_order", REAL_SUBMIT)
    qualified = []
    def qualify(contract):
        qualified.append(True)
        if changed in ("loss", "unset"):
            gateway.pnl = -2500 if changed == "loss" else float("nan")
            ibkr._pnl_update(ibkr._PNL[(id(gateway), gateway.account)]["value"])
        elif changed == "equity":
            ibkr._account_value_update(NS(account=gateway.account, tag="NetLiquidation", currency="USD", value="50000"))
        elif changed == "account":
            gateway.account = "DU456"
        else:
            ibkr._server_error(-1, 1100 if changed == "outage" else 1101, "fixture")
        return [contract]
    monkeypatch.setattr(gateway, "qualifyContracts", qualify)
    monkeypatch.setattr(gateway, "placeOrder", lambda *a: pytest.fail("Risk was revoked before submission"))
    assert desk.app.test_client().post("/api/config", base_url=BASE,
        json={"mode": "live_manual", "live_confirm": "AUTO_LIVE"}).status_code == 200
    result = desk.execute_gated_broker_or_paper(execution[1], desk.load_config(), source="test", via="test")
    assert qualified, result
    assert not result["ok"] and not gateway.trades, result
    assert result["broker"]["submission_attempted"] is False and not result["pending"], result


def test_reducing_order_keeps_missing_pnl_exception(gateway):
    snapshot = ibkr.get_account()
    identity = snapshot["identity"]
    ibkr._PNL_UPDATED.clear()
    order = {"ticker": "TEST", "side": "sell", "shares": 1, "signal_id": "close",
             "broker_identity": identity, "valid_until": deadline(),
             "risk_authorization": {"equity": 100000., "day_pnl": None, "reducing": True}}
    result = ibkr.place_from_desk_order(order)
    assert result["ok"] and len(gateway.trades) == 1


def test_missing_risk_authorization_fails_closed(gateway):
    identity = ibkr.verify_execution_context()["identity"]
    result = ibkr.place_from_desk_order({"ticker": "TEST", "side": "buy", "shares": 1,
        "signal_id": "missing", "broker_identity": identity, "valid_until": deadline()})
    assert not result["ok"] and result["submission_attempted"] is False and not gateway.trades


def test_account_summary_callback_invalidates_authorized_equity(monkeypatch):
    import ib_insync
    client = IB()
    monkeypatch.setattr(client, "connect", lambda *a, **k: None)
    monkeypatch.setattr(ib_insync, "IB", lambda: client)
    monkeypatch.setattr(ibkr, "_CLIENT", None)
    monkeypatch.setattr(ibkr, "_CLIENT_SETTINGS", None)
    monkeypatch.setattr(ibkr, "_SERVER_UNAVAILABLE", False)
    monkeypatch.setattr(ibkr, "_RESYNC_REQUIRED", False)
    monkeypatch.setattr(ibkr, "_RECONNECT_AFTER", 0.)
    monkeypatch.setattr(ibkr, "_FAILED_SETTINGS", None)
    for name in ("_ACCOUNT_EQUITY", "_PNL", "_PNL_UPDATED", "_ACCOUNT_READY", "_API_PULSE", "_VERIFIED", "_CONNECTION"):
        monkeypatch.setattr(ibkr, name, {})
    assert ibkr._ib() is client
    ibkr._ACCOUNT_EQUITY[(id(client), "DU123")] = 100000.
    client.wrapper.accountSummary(1, "DU123", "NetLiquidation", "50000", "USD")
    assert ibkr._ACCOUNT_EQUITY[(id(client), "DU123")] == 50000.


def execution_fixture(quantity, price, revision):
    stamp = datetime.now(timezone.utc)
    contract = Stock("TEST", "SMART", "USD")
    execution = Execution(execId=f"trade.{revision:02}", acctNumber="DU123", time=stamp,
        shares=quantity, price=price, side="BOT", permId=9, cumQty=quantity, avgPrice=price)
    return Fill(contract, execution, CommissionReport(), stamp)


@pytest.mark.parametrize("quantity,price", [(5, 101), (10, 102), (0, 0)])
def test_latest_execution_revision_supersedes_quantity_and_price(quantity, price):
    original, corrected = execution_fixture(10, 100, 1), execution_fixture(quantity, price, 2)
    trade = Trade(original.contract, Order(account="DU123", permId=9, totalQuantity=10, filledQuantity=10),
                  OrderStatus(status="Cancelled", filled=10, avgFillPrice=100), [], [])
    result = ibkr._execution_state(NS(reqExecutions=lambda *a: [original, corrected]), [trade], "DU123:perm:9")
    assert result["filled_qty"] == quantity and result["filled_avg_price"] == price
    assert result["terminal"] and result["execution_details_verified"] and result["execution_correction"]
    assert result["execution_versions"] == {"trade": 2}


def test_partial_correction_history_does_not_authorize_reduction():
    correction = execution_fixture(5, 101, 2)
    correction.execution.cumQty = 15  # Another execution family is missing.
    trade = Trade(correction.contract, Order(account="DU123", permId=9, totalQuantity=15),
                  OrderStatus(status="Filled", filled=15, avgFillPrice=100), [], [])
    result = ibkr._execution_state(NS(reqExecutions=lambda *a: [correction]), [trade], "DU123:perm:9")
    assert not result["terminal"] and not result["execution_details_verified"]
    assert "filled_qty" not in result
    assert result["execution_correction"] and result["execution_versions"] == {"trade": 2}


def test_correction_of_earlier_partial_fill_recomputes_all_families():
    first = execution_fixture(5, 100, 1)
    correction = execution_fixture(3, 101, 2)
    second = execution_fixture(5, 102, 1)
    second.execution.execId = "other.01"
    second.execution.cumQty = 10  # Retained cumulative includes the old first fill.
    second.execution.avgPrice = 101
    trade = Trade(first.contract, Order(account="DU123", permId=9, totalQuantity=10),
                  OrderStatus(status="Filled", filled=10, avgFillPrice=101), [], [])
    result = ibkr._execution_state(NS(reqExecutions=lambda *a: [first, second, correction]), [trade], "DU123:perm:9")
    assert result["filled_qty"] == 8 and result["filled_avg_price"] == (3*101+5*102)/8
    assert result["execution_details_verified"] and result["execution_correction"] and result["terminal"]
    assert result["execution_versions"] == {"trade": 2, "other": 1}


@pytest.mark.parametrize("reported,expected", [(UNSET_DOUBLE, None), (0., 0.), (12.5, 12.5)])
def test_real_wrapper_preserves_raw_pnl_availability(monkeypatch, reported, expected):
    from contextlib import contextmanager
    client = IB()
    original_class_method = type(client.wrapper).commissionReport
    ibkr._preserve_commission_pnl(client)
    fill = execution_fixture(1, 100, 1)
    client.wrapper.fills[fill.execution.execId] = fill
    client.wrapper.commissionReport(CommissionReport(execId=fill.execution.execId,
        commission=.35, currency="USD", realizedPNL=reported))
    monkeypatch.setattr(client, "reqExecutions", lambda *a: [fill])
    monkeypatch.setattr(client, "sleep", lambda *a: None)
    @contextmanager
    def session():
        yield client
    monkeypatch.setattr(ibkr, "_session", session)
    monkeypatch.setattr(ibkr, "_identity", lambda *a: {"account_id": "DU123", "paper_mode": True})
    result = ibkr.execution_history.__wrapped__()
    assert result["ok"] and result["executions"][0]["broker_realized_pnl"] == expected
    assert result["executions"][0]["broker_realized_pnl_reported"] is (expected is not None)
    assert type(client.wrapper).commissionReport is original_class_method


def test_correction_refresh_batches_reads_and_verifies_account(gateway, monkeypatch):
    identity = ibkr.verify_execution_context()["identity"]
    corrected = execution_fixture(5, 101, 2)
    gateway.trades = [Trade(corrected.contract, Order(account="DU123", permId=9, totalQuantity=10),
        OrderStatus(status="Cancelled", filled=5, avgFillPrice=101), [], [])]
    calls = []
    monkeypatch.setattr(gateway, "reqExecutions", lambda *a: calls.append(True) or [corrected])
    result = ibkr.refresh_execution_corrections(["DU123:perm:9", "DU123:perm:9"], identity)
    assert result["ok"] and len(calls) == 1 and len(result["orders"]) == 1
    assert result["orders"][0]["order_id"] == "DU123:perm:9"
    assert result["orders"][0]["identity"] == identity
    assert not ibkr.refresh_execution_corrections(["DU123:perm:9"], dict(identity, account_id="DU456"))["ok"]


@pytest.mark.parametrize("incomplete", [False, True])
def test_recovered_permanent_id_refresh_preserves_ledger_id_and_incomplete_risk(execution, gateway, monkeypatch, incomplete):
    identity = ibkr.verify_execution_context()["identity"]
    cfg, sig, _ = execution
    desk.save_config(dict(cfg, broker_identity=identity))
    corrected = execution_fixture(5, 101, 2)
    if incomplete:
        corrected.execution.cumQty = 15
    # Completed-order snapshots omit the old client/order ID after reconnect.
    gateway.trades = [Trade(corrected.contract, Order(account="DU123", permId=9, totalQuantity=10),
        OrderStatus(status="Cancelled", filled=5, avgFillPrice=101), [], [])]
    calls = []
    monkeypatch.setattr(gateway, "reqExecutions", lambda *a: calls.append(True) or [corrected])
    response = {"broker": "ibkr", "order_id": "DU123:37:42", "permanent_order_id": "DU123:perm:9",
                "order": {"broker_identity": identity}}
    initial = {"state": "filled", "terminal": True, "filled_qty": 10, "filled_avg_price": 100,
               "execution_details_verified": True, "execution_versions": {"trade": 1}}
    with desk._BROKER_SUBMIT_LOCK:
        desk._apply_broker_update(sig, response, initial, "fixture")
    assert desk.load_signals()[0]["live_response"]["permanent_order_id"] == "DU123:perm:9"
    monkeypatch.setattr(desk, "_LAST_EXECUTION_CORRECTIONS_AT", 0.)
    desk._reconcile_execution_corrections()
    fill = desk.load_ledger()["broker_fills"][0]
    assert calls == [True] and fill["order_id"] == "DU123:37:42"
    assert desk._broker_trades_today(desk.load_ledger()) == 1
    if incomplete:
        assert fill["shares"] == 10 and not fill["broker_reconciled"] and not fill["confirmed"]
        assert desk.load_ledger()["pending_broker_orders"]
    else:
        assert fill["shares"] == 5 and fill["price"] == 101 and fill["broker_reconciled"]
        assert not desk.load_ledger()["pending_broker_orders"]


@pytest.mark.parametrize("late_zero", [False, True])
def test_incomplete_new_correction_cannot_be_resolved_by_older_fill(execution, late_zero):
    sig = execution[1]
    response = {"broker": "ibkr", "order_id": "DU123:perm:9",
                "order": {"broker_identity": execution[0]["broker_identity"]}}
    original = {"state": "filled", "terminal": True, "filled_qty": 10, "filled_avg_price": 100,
                "execution_details_verified": True, "execution_versions": {"trade": 1}}
    incomplete = {"state": "unknown", "terminal": False, "execution_details_verified": False,
                  "execution_correction": True, "execution_versions": {"trade": 2}, "error": "Incomplete correction"}
    with desk._BROKER_SUBMIT_LOCK:
        desk._apply_broker_update(sig, response, original, "fixture")
        desk._apply_broker_update(sig, response, incomplete, "fixture")
        stale = dict(original, state="failed", filled_qty=0, filled_avg_price=0, execution_versions={}) if late_zero else original
        still_pending, terminal = desk._apply_broker_update(sig, response, stale, "late_stale_report")
        assert not terminal and not still_pending["confirmed"]
        assert desk.load_ledger()["pending_broker_orders"]
        corrected, terminal = desk._apply_broker_update(sig, response, dict(original,
            filled_qty=5, filled_avg_price=101, execution_versions={"trade": 2}, execution_correction=True), "complete_correction")
    assert terminal and corrected["confirmed"] and corrected["shares"] == 5
    assert not desk.load_ledger()["pending_broker_orders"]
