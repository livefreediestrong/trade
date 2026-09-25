"""Exercise the real risk/ledger paths with only external broker I/O replaced."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
import app as desk
import broker_alpaca as alpaca
import broker_ibkr as ibkr
import broker_router as router
import screener_logic as screener
from broker_fixtures import configure, approval, deadline

BASE = "http://127.0.0.1:5056"
REAL_DAY_PNL = desk._broker_day_pnl
REAL_POSITION_QTY = desk._broker_position_qty
REAL_SUBMIT = desk.live_broker_place_order


@pytest.fixture
def execution(tmp_path, monkeypatch):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    desk._CORRUPT_PATHS.clear()
    cfg = dict(desk.load_config(), session_active=True, rth_only=False, mode="live_manual", risk_preset="mid")
    configure(monkeypatch, cfg)
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (0., 100000., None))
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (0., None))
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": True, "orders": []})
    sent = []
    def submit(order):
        sent.append(dict(order))
        return {"ok": True, "status": "paper_submitted", "broker": "alpaca", "order_id": "o1"}
    monkeypatch.setattr(desk, "live_broker_place_order", submit)
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {"state": "filled", "terminal": True, "filled_qty": 20, "filled_avg_price": 100})
    sig = {"id": "s1", "ticker": "AAPL", "side": "buy", "signal_price": 90, "suggested_shares": 20, "status": "pending"}
    desk.save_signals([sig])
    return cfg, sig, sent


def run(execution):
    cfg, sig, _ = execution
    return desk.execute_gated_broker_or_paper(sig, cfg, source="test", via="test")


def test_position_at_cap_cannot_be_added_to(execution, monkeypatch):
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (20., None))
    assert not run(execution)["ok"]
    assert execution[2] == []


def test_holdings_and_working_orders_reduce_new_quantity(execution, monkeypatch):
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (10., None))
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": True, "orders": [
        {"symbol": "AAPL", "side": "buy", "qty": "5", "filled_qty": "0", "limit_price": "120"}]})
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {"state": "filled", "filled_qty": 4, "filled_avg_price": 100})
    assert run(execution)["ok"]
    assert execution[2][0]["shares"] == 4  # $2,000 - $1,000 held - $600 reserved


def test_one_share_cannot_exceed_remaining_cap(execution, monkeypatch):
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (19.5, None))
    assert not run(execution)["ok"]
    assert not execution[2]


def test_missing_open_order_snapshot_blocks_entry(execution, monkeypatch):
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": False})
    assert not run(execution)["ok"]
    assert not execution[2]


def test_open_partial_keeps_tracking_and_updates_without_double_count(execution, monkeypatch):
    partial = {"state": "partially_filled", "terminal": False, "alpaca_status": "partially_filled", "filled_qty": 5, "filled_avg_price": 100}
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: dict(partial))
    monkeypatch.setattr(alpaca, "reconcile_after_timeout", lambda *a, **k: dict(partial))
    result = run(execution)
    assert result["ok"] and result["pending"] and not result["fill"]["broker_reconciled"]
    assert len(desk.load_ledger()["pending_broker_orders"]) == 1
    # Another approval cannot duplicate an order with an unresolved outcome.
    assert not run(execution)["ok"]
    assert len(execution[2]) == 1
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: dict(partial, state="filled", terminal=True, filled_qty=20, filled_avg_price=101))
    desk._reconcile_pending_broker_orders()
    desk._reconcile_pending_broker_orders()
    ledger = desk.load_ledger()
    assert ledger["pending_broker_orders"] == []
    assert len(ledger["broker_fills"]) == 1
    assert ledger["broker_fills"][0]["shares"] == 20
    assert ledger["broker_fills"][0]["price"] == 101
    assert desk._broker_trades_today(ledger) == 1
    assert desk.load_signals()[0]["fill"]["shares"] == 20


def test_partial_evidence_survives_failed_cancel_poll(execution, monkeypatch):
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {"state": "partially_filled", "terminal": False, "filled_qty": 5, "filled_avg_price": 100})
    monkeypatch.setattr(alpaca, "reconcile_after_timeout", lambda *a, **k: {"state": "unknown", "terminal": False})
    result = run(execution)
    assert result["ok"] and result["fill"]["shares"] == 5 and result["pending"]


def test_pending_approval_is_not_reoffered(execution, monkeypatch):
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {"state": "pending", "terminal": False})
    monkeypatch.setattr(alpaca, "reconcile_after_timeout", lambda *a, **k: {"state": "unknown", "terminal": False})
    client = desk.app.test_client()
    assert client.post("/api/signals/s1/approve", base_url=BASE, json=approval(client, "s1")).status_code == 400
    assert desk.load_signals()[0]["status"] == "broker_pending"
    assert client.post("/api/signals/s1/approve", base_url=BASE, json={}).status_code == 400
    assert len(execution[2]) == 1


@pytest.mark.parametrize("endpoint", ["/api/ledger/reset", "/api/session/start"])
def test_paper_resets_preserve_all_broker_state(execution, monkeypatch, endpoint):
    ledger = desk.load_ledger()
    state = {"pending_broker_orders": [{"order_id": "o1", "broker": {"order_id": "o1"}, "signal": execution[1]}],
             "broker_fills": [{"id": "f1", "order_id": "old"}], "broker_daily": {"day": {"trades": 2}},
             "broker_counted_orders": {"old": "day"}, "broker_fills_archive": [{"id": "f0"}]}
    ledger.update(copy.deepcopy(state))
    desk.save_ledger(ledger)
    loop = NS(start=lambda: None, reset_session_totals=lambda: None, status=lambda cfg: {})
    monkeypatch.setattr(desk, "get_paper_loop", lambda: loop)
    monkeypatch.setattr(desk, "ensure_paper_loop_started", lambda: None)
    monkeypatch.setattr(desk, "generate_scan_signal", lambda *a, **k: None)
    response = desk.app.test_client().post(endpoint, base_url=BASE, json={"make_today_usd": 50, "beginning_bank_usd": 100000})
    assert response.status_code == 200
    assert {key: desk.load_ledger()[key] for key in state} == state


class FakeIB:
    def __init__(self, account="DU123", currency="USD"):
        self.account, self.currency = account, currency
        self.disconnected = False
        self.canceled = []
        self.trades = []
        self.pnl = -250.
    def managedAccounts(self): return [self.account]
    def accountSummary(self, account):
        return [NS(account=account, currency=self.currency, tag=k, value=v) for k, v in
                {"NetLiquidation": "100000", "TotalCashValue": "90000", "BuyingPower": "180000"}.items()]
    def reqPnL(self, account):
        owner = self
        class LivePnL:
            @property
            def dailyPnL(self):
                return owner.pnl
        self._pnl_pending = LivePnL()
        return self._pnl_pending
    def cancelPnL(self, account): pass
    def disconnect(self): self.disconnected = True
    def isConnected(self): return not self.disconnected
    def reqCurrentTime(self): return object()
    def sleep(self, interval):
        if getattr(self, '_pnl_pending', None) is not None:
            pnl, self._pnl_pending = self._pnl_pending, None
            ibkr._pnl_update(pnl)
    def reqAllOpenOrders(self): return self.trades
    def reqCompletedOrders(self, apiOnly=False): return []
    def reqExecutions(self, filters): return []
    def cancelOrder(self, order): self.canceled.append(order)
    def qualifyContracts(self, contract):
        contract.conId = 42
        return [contract]
    def reqMarketDataType(self, kind): pass
    def reqMktData(self, contract, *args):
        stamp = datetime.now(timezone.utc)
        return NS(marketDataType=1, bidSize=10000, askSize=10000,
                  ticks=[NS(tickType=1, price=99.99, time=stamp), NS(tickType=2, price=100.01, time=stamp)])
    def cancelMktData(self, contract): pass
    def placeOrder(self, contract, order):
        order.orderId, order.clientId = 42, 37
        trade = NS(order=order, contract=contract, orderStatus=NS(status="Submitted", filled=0, avgFillPrice=0))
        self.trades.append(trade)
        return trade
    def positions(self, account): return []


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setenv("BROKER_PROVIDER", "ibkr")
    monkeypatch.setenv("IBKR_LIVE", "false")
    monkeypatch.setenv("IB_GATEWAY_PORT", "4002")
    monkeypatch.setenv("IB_CLIENT_ID", "37")
    monkeypatch.setenv("IBKR_ACCOUNT", "")
    ibkr._VERIFIED.clear()
    monkeypatch.setattr(ibkr, "_PNL", {})
    monkeypatch.setattr(ibkr, "_PNL_UPDATED", {})
    monkeypatch.setattr(ibkr, "_ACCOUNT_READY", {})
    monkeypatch.setattr(ibkr, "_ACCOUNT_EQUITY", {})
    monkeypatch.setattr(ibkr, "_API_PULSE", {})
    monkeypatch.setattr(ibkr, "_SERVER_UNAVAILABLE", False)
    monkeypatch.setattr(ibkr, "_RESYNC_REQUIRED", False)
    fake = FakeIB()
    monkeypatch.setattr(ibkr, "_ib", lambda: fake)
    return fake


def test_ibkr_routing_and_normalized_daily_pnl(gateway):
    assert router._module() is ibkr
    assert desk._broker_day_pnl() == (-250., 100000., None)
    assert not gateway.disconnected


@pytest.mark.parametrize("currency", ["EUR", "BASE"])
def test_non_usd_base_account_fails_closed(gateway, currency):
    gateway.currency = currency
    assert not ibkr.get_account()["ok"]
    assert not gateway.disconnected


def test_unverified_mode_is_never_labeled_paper(gateway, monkeypatch):
    monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
    assert ibkr.public_status()["paper_mode"] is None
    assert ibkr.public_status()["masthead"] == "BROKER UNVERIFIED"
    assert not ibkr.verify_execution_context()["ok"]


def test_live_account_rejects_paper_setting(gateway):
    gateway.account = "U123"
    assert not ibkr.verify_execution_context()["ok"]
    assert not gateway.disconnected


def test_unavailable_daily_pnl_never_defaults_to_zero(gateway, monkeypatch):
    gateway.pnl = float("nan")
    # Clock must keep advancing: get_account now reads monotonic() more than
    # twice, and a frozen clock never reaches its callback-wait deadline.
    ticks = iter(float(4 * i) for i in range(10**6))
    monkeypatch.setattr(ibkr.time, "monotonic", lambda: next(ticks))
    result = ibkr.get_account()
    assert result["ok"] and not result["risk_ready"]
    assert result["account"]["day_pnl"] is None and result["account"]["equity"] == 100000
    assert "daily P&L unavailable" in result["risk_error"]
    monkeypatch.setattr(ibkr, "get_account", lambda: result)
    assert desk._broker_day_pnl()[2]  # account display must not bypass execution checks
    assert not gateway.disconnected


def test_multiple_accounts_need_explicit_selection(gateway, monkeypatch):
    monkeypatch.setattr(gateway, "managedAccounts", lambda: ["DU123", "DU456"])
    assert not ibkr.verify_execution_context()["ok"]
    monkeypatch.setenv("IBKR_ACCOUNT", "DU456")
    result = ibkr.verify_execution_context()
    assert result["ok"] and result["identity"]["account_id"] == "DU456"


def test_ibkr_confirmation_bound_to_account(execution, gateway, monkeypatch):
    client = desk.app.test_client()
    assert client.post("/api/config", base_url=BASE, json={"mode": "live_manual", "live_confirm": "AUTO_LIVE"}).status_code == 200
    cfg = desk.load_config()
    assert cfg["broker_identity"]["account_id"] == "DU123"
    gateway.account = "DU456"
    result = desk.execute_gated_broker_or_paper(execution[1], cfg, source="t", via="t")
    assert not result["ok"] and "account changed" in result["error"].lower()
    assert not execution[2]


def test_live_mode_requires_real_confirmation(execution, gateway, monkeypatch):
    gateway.account = "U123"
    monkeypatch.setenv("IBKR_LIVE", "true")
    monkeypatch.setenv("IB_GATEWAY_PORT", "4001")
    client = desk.app.test_client()
    assert client.post("/api/config", base_url=BASE, json={"mode": "live_manual", "live_confirm": "AUTO_LIVE"}).status_code == 400
    assert client.post("/api/config", base_url=BASE, json={"mode": "live_manual", "live_confirm": "REAL"}).status_code == 200


def test_ibkr_order_identity_and_retry_deduplication(gateway):
    identity = ibkr.get_account()["identity"]
    order = {"ticker": "AAPL", "side": "buy", "shares": 5, "signal_id": "s1", "broker_identity": identity, "valid_until": deadline(),
             "risk_authorization": {"equity": 100000., "day_pnl": -250., "reducing": False}}
    result = ibkr.place_from_desk_order(order)
    assert result["ok"] and result["order_id"] == "DU123:37:42", result
    assert ibkr.place_from_desk_order(order)["order_id"] == result["order_id"]
    assert len(gateway.trades) == 1 and not gateway.disconnected


def test_ibkr_cancel_request_is_not_terminal_proof(gateway):
    gateway.trades = [NS(order=NS(account="DU123", clientId=37, orderId=42),
                         orderStatus=NS(status="Submitted", filled=2, avgFillPrice=100))]
    result = ibkr.reconcile_after_timeout("DU123:37:42", timeout=0)
    assert result["state"] == "partially_filled" and not result["terminal"]
    assert len(gateway.canceled) == 1 and not gateway.disconnected
    gateway.trades[0].orderStatus.status = "Cancelled"
    result = ibkr.wait_for_fill("DU123:37:42", timeout=0)
    assert result["terminal"] and result["filled_qty"] == 2


def test_ibkr_adapter_through_real_execution_gate(execution, gateway, monkeypatch):
    monkeypatch.setattr(desk, "_broker_day_pnl", REAL_DAY_PNL)
    monkeypatch.setattr(desk, "_broker_position_qty", REAL_POSITION_QTY)
    monkeypatch.setattr(desk, "live_broker_place_order", REAL_SUBMIT)
    original_place = gateway.placeOrder
    def fill(contract, order):
        trade = original_place(contract, order)
        trade.orderStatus = NS(status="Filled", filled=order.totalQuantity, avgFillPrice=100.)
        return trade
    monkeypatch.setattr(gateway, "placeOrder", fill)
    client = desk.app.test_client()
    result = client.post("/api/config", base_url=BASE, json={"mode": "live_manual", "live_confirm": "AUTO_LIVE"})
    assert result.status_code == 200
    cfg = desk.load_config()
    result = desk.execute_gated_broker_or_paper(execution[1], cfg, source="t", via="t")
    assert result["ok"] and result["fill"]["broker_reconciled"], result
    assert result["fill"]["shares"] == 20 and len(gateway.trades) == 1
    gateway.pnl = -2500.
    result = desk.execute_gated_broker_or_paper(dict(execution[1], id="s2"), cfg, source="t", via="t")
    assert not result["ok"] and "loss limit" in result["error"]
    assert len(gateway.trades) == 1


def test_process_probe_and_second_start_do_not_kill_running_process(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        lock = tmp_path / "test.pid"
        lock.write_text(str(process.pid), encoding="ascii")
        monkeypatch.setattr(desk, "_INSTANCE_LOCK_PATH", lock)
        monkeypatch.setattr(desk, "_INSTANCE_LOCK_FD", None)
        assert desk._instance_pid_is_running(process.pid)
        with pytest.raises(RuntimeError, match="Another Tomahawk instance"):
            desk.acquire_instance_lock()
        assert process.poll() is None
        assert lock.read_text() == str(process.pid)
    finally:
        process.terminate()
        process.wait(timeout=5)
    assert not desk._instance_pid_is_running(process.pid)


@pytest.mark.parametrize("ticker, info", [("SPY", {}), ("QQQ", {}), ("XYZ", {"quoteType": "ETF"})])
def test_fund_scan_never_requests_company_earnings(monkeypatch, ticker, info):
    def unexpected(*a, **k): raise AssertionError("earnings provider must not be called for funds")
    monkeypatch.setattr(screener.ds, "finnhub_earnings", unexpected)
    monkeypatch.setattr(screener.ds, "yahoo_next_earnings", unexpected)
    assert screener._fetch_earnings(ticker, object(), info) == (None, None)


def test_stock_earnings_still_requested(monkeypatch):
    monkeypatch.setattr(screener.ds, "finnhub_earnings", lambda ticker: {"date": "2026-10-01"})
    assert screener._fetch_earnings("AAPL", object(), {"quoteType": "EQUITY"})[0]["date"] == "2026-10-01"
