"""Real execution/control flow with isolated files and no broker/network writes."""
import copy
import socket
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

import app as desk
import broker_alpaca as alpaca
import broker_ibkr as ibkr
import broker_router as router
from broker_fixtures import configure, approval, deadline, IDENTITY
from test_broker_lifecycle import gateway

BASE = "http://127.0.0.1:5056"


@pytest.fixture
def execution(tmp_path, monkeypatch):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    desk._CORRUPT_PATHS.clear()
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    cfg = dict(desk.load_config(), mode="live_manual", session_active=True, rth_only=False,
               risk_preset="mid", paper_research_enabled=True)
    configure(monkeypatch, cfg)
    desk.save_config(cfg)
    sig = {"id": "review", "ticker": "TEST", "side": "buy", "suggested_shares": 10,
           "signal_price": 100., "status": "pending", "workspace": "live"}
    desk.save_signals([sig])
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (0., 100000., None))
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (0., None))
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": True, "orders": []})
    sent = []
    def submit(order):
        sent.append(copy.deepcopy(order))
        return {"ok": True, "broker": "alpaca", "order_id": "order-1", "status": "paper_submitted"}
    monkeypatch.setattr(desk, "live_broker_place_order", submit)
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {
        "state": "filled", "terminal": True, "filled_qty": sent[-1]["shares"], "filled_avg_price": 100.})
    return cfg, sig, sent


def run(execution):
    cfg, sig, _ = execution
    return desk.execute_gated_broker_or_paper(sig, cfg, source="test", via="test")


@pytest.mark.parametrize("held,side,working,filled,expected", [
    (10, "sell", 10, 0, 0), (10, "sell", 8, 3, 5),
    (-10, "buy", 10, 0, 0), (-10, "buy", 8, 3, 5),
])
def test_closes_reserve_working_remainders(execution, monkeypatch, held, side, working, filled, expected):
    execution[1]["side"] = side
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (held, None))
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": True, "orders": [
        {"symbol": "TEST", "side": side, "qty": working, "filled_qty": filled}]})
    result = run(execution)
    assert result["ok"] is bool(expected)
    assert ([o["shares"] for o in execution[2]]) == ([expected] if expected else [])


def test_bad_closing_order_quantity_fails_closed(execution, monkeypatch):
    execution[1]["side"] = "sell"
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (10, None))
    monkeypatch.setattr(alpaca, "get_open_orders", lambda: {"ok": True, "orders": [
        {"symbol": "TEST", "side": "sell", "qty": 2, "filled_qty": 3}]})
    assert not run(execution)["ok"] and not execution[2]


@pytest.mark.parametrize("side,held,allowed", [("sell", 10, True), ("buy", -10, True), ("buy", 0, False)])
def test_missing_pnl_allows_only_verified_close(execution, monkeypatch, side, held, allowed):
    execution[1]["side"] = side
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (held, None))
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (None, 100000., "Daily P&L unavailable"))
    assert run(execution)["ok"] is allowed
    assert bool(execution[2]) is allowed


def test_account_failure_still_blocks_close(execution, monkeypatch):
    execution[1]["side"] = "sell"
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (10, None))
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (None, None, "Broker account unavailable"))
    assert not run(execution)["ok"] and not execution[2]


def test_arbitrarily_bad_paper_book_does_not_gate_broker(execution, monkeypatch):
    ledger = desk.load_ledger()
    ledger.update(cash=-1e9, equity=1., positions=[{"ticker": "UNMARKED", "shares": 1e9, "avg_price": 1000}],
                  daily={desk._today_str(): {"trades": 100000, "pnl": -1e9}},
                  fills=[{"side": "sell", "realized_pnl": -10000, "fee_usd": 100} for _ in range(30)])
    desk.save_ledger(ledger)
    # These paper functions must never run at either broker precheck or final check.
    for name in ("can_take_trade", "daily_stats", "_session_pnl_with_mtm", "bleed_status", "_missing_marks"):
        monkeypatch.setattr(desk, name, lambda *a, **k: pytest.fail("Broker consulted paper risk state"))
    result = run(execution)
    assert result["ok"] and execution[2][0]["shares"] == 10
    saved = desk.load_ledger()
    assert saved["cash"] == -1e9 and saved["positions"] == ledger["positions"]


def test_real_broker_loss_still_blocks_with_good_paper_book(execution, monkeypatch):
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (-99999., 100000., None))
    result = run(execution)
    assert not result["ok"] and "broker account" in result["error"] and not execution[2]


def test_broker_count_alone_controls_broker_trade_cap(execution):
    ledger = desk.load_ledger()
    max_trades = desk.get_preset(execution[0]["risk_preset"])["max_trades_per_day"]
    ledger["broker_daily"] = {desk._today_str(): {"trades": max_trades}}
    desk.save_ledger(ledger)
    result = run(execution)
    assert not result["ok"] and "broker trades" in result["error"] and not execution[2]


def test_missing_broker_never_fills_local_paper(execution, monkeypatch):
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: False)
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: pytest.fail("No broker-to-paper fallback"))
    result = run(execution)
    assert not result["ok"] and result["book"] is None and not result["paper_fallback"]
    assert not execution[2]


@pytest.mark.parametrize("session_active,rth_only,is_open,reducing,allowed", [
    (False, False, True, False, False), (False, False, True, True, True),
    (True, True, False, False, False), (True, True, False, True, False),
    (True, True, True, False, True),
])
def test_broker_session_and_hours_remain_enforced(execution, monkeypatch, session_active, rth_only, is_open, reducing, allowed):
    cfg = dict(execution[0], session_active=session_active, rth_only=rth_only)
    desk.save_config(cfg)
    execution[0].update(cfg)
    monkeypatch.setattr(desk.paper_loop_mod, "is_rth", lambda: is_open)
    if reducing:
        execution[1]["side"] = "sell"
        monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (10., None))
    assert run(execution)["ok"] is allowed


def test_account_parser_distinguishes_pnl_unavailable_from_blocked(monkeypatch):
    monkeypatch.setattr(alpaca, "get_account", lambda: {"ok": True, "account": {"equity": 100000}})
    pnl, equity, error = desk._broker_day_pnl()
    assert pnl is None and equity == 100000 and error
    monkeypatch.setattr(alpaca, "get_account", lambda: {"ok": True, "account": {"equity": 100000, "trading_blocked": True}})
    assert desk._broker_day_pnl()[1] is None


def test_mode_change_during_checks_revokes_order(execution, monkeypatch):
    def account_read():
        desk.save_config(dict(execution[0], mode="manual"))
        return 0., 100000., None
    monkeypatch.setattr(desk, "_broker_day_pnl", account_read)
    result = run(execution)
    assert not result["ok"] and "mode changed" in result["error"]
    assert not execution[2] and not desk.load_ledger().get("pending_broker_orders")


def test_mode_post_waits_for_atomic_submission_window(execution, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    changing = threading.Event()
    changed = threading.Event()
    failures = []
    def submit(order):
        entered.set()
        assert release.wait(3)
        assert desk.load_config()["mode"] == "live_manual"
        return {"ok": False, "submission_attempted": False, "error": "test-only refusal"}
    monkeypatch.setattr(desk, "live_broker_place_order", submit)
    def trade():
        try:
            run(execution)
        except Exception as exc:
            failures.append(exc)
    def mode_change():
        changing.set()
        try:
            result = desk.app.test_client().post("/api/config", base_url=BASE, json={"mode": "manual"})
            assert result.status_code == 200
        except Exception as exc:
            failures.append(exc)
        finally:
            changed.set()
    execution_thread = threading.Thread(target=trade)
    changer = threading.Thread(target=mode_change)
    execution_thread.start()
    try:
        assert entered.wait(3)
        changer.start()
        assert changing.wait(3)
        assert not changed.wait(0.1)
    finally:
        release.set()
        execution_thread.join(4)
        if changer.ident is not None:
            changer.join(4)
    assert not failures and not execution_thread.is_alive() and not changer.is_alive()
    assert desk.load_config()["mode"] == "manual"


def test_account_authorization_change_during_checks_revokes_order(execution, monkeypatch):
    identity = {"broker": "ibkr", "account_id": "TEST_ACCOUNT"}
    execution[0]["broker_identity"] = identity
    desk.save_config(execution[0])
    fake = NS(public_status=lambda: {"broker": "ibkr"},
              verify_execution_context=lambda: {"ok": True, "identity": identity},
              get_open_orders=lambda: {"ok": True, "orders": []})
    monkeypatch.setattr(router, "_module", lambda: fake)
    def account_read():
        desk.save_config(dict(execution[0], broker_identity={"account_id": "OTHER_TEST_ACCOUNT"}))
        return 0., 100000., None
    monkeypatch.setattr(desk, "_broker_day_pnl", account_read)
    result = run(execution)
    assert not result["ok"] and "authorization changed" in result["error"] and not execution[2]


def test_intent_is_on_disk_before_broker_io(execution, monkeypatch):
    def submit(order):
        pending = desk.load_ledger()["pending_broker_orders"]
        assert len(pending) == 1 and pending[0]["order_id"].startswith("intent:")
        assert pending[0]["broker"]["order"] == order
        assert desk.load_signals()[0]["status"] == "broker_pending"
        raise ConnectionError("Connection lost after unknown acceptance")
    monkeypatch.setattr(desk, "live_broker_place_order", submit)
    result = run(execution)
    assert result["pending"] and not result["ok"]
    assert len(desk.load_ledger()["pending_broker_orders"]) == 1
    assert not run(execution)["ok"]  # intent prevents any new submit


def test_intent_write_failure_prevents_any_broker_submit(execution, monkeypatch):
    monkeypatch.setattr(desk, "save_ledger", lambda ledger: (_ for _ in ()).throw(OSError("disk unavailable")))
    with pytest.raises(OSError, match="disk unavailable"):
        run(execution)
    assert not execution[2]


def test_ambiguous_approval_stays_pending_and_recovers_read_only(execution, monkeypatch):
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: {"ok": False, "error": "timeout"})
    client = desk.app.test_client()
    assert client.post("/api/signals/review/approve", base_url=BASE, json=approval(client, "review")).status_code == 400
    assert desk.load_signals()[0]["status"] == "broker_pending"
    assert client.post("/api/signals/review/approve", base_url=BASE, json={}).status_code == 400
    monkeypatch.setattr(alpaca, "find_order_by_signal", lambda *a, **k: {"ok": True, "found": False})
    desk._reconcile_pending_broker_orders()
    assert len(desk.load_ledger()["pending_broker_orders"]) == 1
    monkeypatch.setattr(alpaca, "find_order_by_signal", lambda *a, **k: {"ok": True, "order_id": "recovered"})
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {
        "state": "filled", "terminal": True, "filled_qty": 10, "filled_avg_price": 101.})
    desk._reconcile_pending_broker_orders()
    desk._reconcile_pending_broker_orders()
    ledger = desk.load_ledger()
    assert not ledger["pending_broker_orders"] and len(ledger["broker_fills"]) == 1
    assert ledger["broker_fills"][0]["order_id"] == "recovered"
    assert desk._broker_trades_today(ledger) == 1


def test_definite_pre_submit_failure_releases_intent(execution, monkeypatch):
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: {
        "ok": False, "submission_attempted": False, "error": "contract validation failed"})
    result = run(execution)
    assert not result["ok"] and not result["pending"]
    assert not desk.load_ledger()["pending_broker_orders"]


def test_known_order_identity_survives_error_response(execution, monkeypatch):
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: {
        "ok": False, "order_id": "known", "error": "post-submit socket lost"})
    monkeypatch.setattr(alpaca, "wait_for_fill", lambda *a, **k: {"state": "unknown", "terminal": False})
    monkeypatch.setattr(alpaca, "reconcile_after_timeout", lambda *a, **k: {"state": "unknown", "terminal": False})
    result = run(execution)
    assert result["pending"]
    pending = desk.load_ledger()["pending_broker_orders"]
    assert len(pending) == 1 and pending[0]["order_id"] == "known"


def fresh_stock_quote(identity, con_id=42):
    """A live two-sided quote that passes the adapter's final pre-send check."""
    now = datetime.now(timezone.utc)
    ticker = NS(marketDataType=1, bidSize=100, askSize=100,
                ticks=[NS(tickType=1, price=100.0, time=now), NS(tickType=2, price=100.02, time=now)])
    seen = {}
    ibkr._capture_stock_ticks(ticker, seen)
    return dict(ibkr._stock_tick_quote(ticker, seen), ok=True, identity=identity, con_id=con_id)


def test_ibkr_preserves_identity_after_submit_failure(monkeypatch, gateway):
    identity = ibkr.get_account()["identity"]
    placed = []
    def place(contract, order):
        placed.append(order)
        order.orderId, order.clientId = 42, 37
        return NS(order=order)
    monkeypatch.setattr(gateway, "placeOrder", place)
    monkeypatch.setattr(ibkr, "stock_quote", lambda symbol: fresh_stock_quote(identity))
    monkeypatch.setattr(gateway, "sleep", lambda t: (_ for _ in ()).throw(ConnectionError("post-submit disconnect")))
    response = ibkr.place_from_desk_order({
        "ticker": "TEST", "side": "buy", "shares": 1, "signal_id": "s", "broker_identity": identity, "valid_until": deadline(),
        "risk_authorization": {"equity": 100000., "day_pnl": -250., "reducing": False}})
    assert placed and not response["ok"] and response["submission_attempted"]
    assert response["order_id"] == "DU123:37:42"


def test_ibkr_recovery_is_bound_to_account_and_never_resubmits(monkeypatch):
    identity = {"account_id": "TEST_ACCOUNT", "endpoint": "isolated", "paper_mode": True, "client_id": 37}
    trade = NS(order=NS(account="TEST_ACCOUNT", clientId=37, orderId=42, orderRef="s"))
    fake = NS(reqAllOpenOrders=lambda: [trade], reqCompletedOrders=lambda **k: [trade])
    @contextmanager
    def session():
        yield fake
    monkeypatch.setattr(ibkr, "_session", session)
    monkeypatch.setattr(ibkr, "_identity", lambda ib: identity)
    lookup = ibkr.find_order_by_signal.__wrapped__
    assert not lookup("s", {"account_id": "OTHER"})["ok"]
    result = lookup("s", identity)
    assert result["order_id"] == "TEST_ACCOUNT:37:42"
    assert lookup("missing", identity) == {"ok": True, "found": False}


def test_alpaca_recovery_only_gets_client_order_id(monkeypatch):
    calls = []
    monkeypatch.setattr(alpaca, "is_configured", lambda: True)
    monkeypatch.setattr(alpaca, "verify_execution_context", lambda: {"ok": True, "identity": IDENTITY})
    monkeypatch.setattr(alpaca, "_request", lambda method, path: calls.append((method, path)) or (200, {"id": "existing"}))
    assert alpaca.find_order_by_signal("ref/with space", IDENTITY)["order_id"] == "existing"
    assert calls == [("GET", "/orders:by_client_order_id?client_order_id=ref%2Fwith%20space")]


@pytest.mark.parametrize("status,message,definitive", [
    (403, "insufficient buying power", True),
    (422, "qty must be greater than 0", True),
    (422, "client_order_id must be unique", False),
    (0, "request timeout", False), (500, "internal error", False),
])
def test_alpaca_distinguishes_rejection_from_ambiguous_submit(monkeypatch, status, message, definitive):
    monkeypatch.setattr(alpaca, "verify_execution_context", lambda: {"ok": True, "identity": IDENTITY})
    monkeypatch.setattr(alpaca, "is_configured", lambda: True)
    monkeypatch.setattr(alpaca, "_request", lambda *a, **k: (status, {"message": message}))
    result = alpaca.place_from_desk_order({"ticker": "TEST", "side": "buy", "shares": 1, "signal_id": "s", "broker_identity": IDENTITY, "valid_until": deadline()})
    assert result["definitive_rejection"] is definitive


def test_paper_workspace_cannot_enter_broker_path(execution, monkeypatch):
    execution[1]["workspace"] = "paper"
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: pytest.fail("Broker must not be consulted"))
    assert not run(execution)["ok"]


def test_paper_approval_routes_to_paper_even_in_live_mode(execution, monkeypatch):
    sig = dict(execution[1], workspace="paper")
    desk.save_signals([sig])
    calls = []
    monkeypatch.setattr(desk, "paper_fill", lambda s, c, **k: calls.append((s, c)) or {"ok": True})
    monkeypatch.setattr(desk, "execute_gated_broker_or_paper", lambda *a, **k: pytest.fail("No broker routing"))
    reply = desk.app.test_client().post("/api/signals/review/approve", base_url=BASE, json={})
    assert reply.status_code == 200 and calls[0][1]["_paper_research"]
    assert desk.load_config()["mode"] == "live_manual"


def test_live_workspace_is_never_reinterpreted_as_paper_after_mode_change(execution, monkeypatch):
    desk.save_config(dict(execution[0], mode="manual"))
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: pytest.fail("Live idea cannot fill paper"))
    reply = desk.app.test_client().post("/api/signals/review/approve", base_url=BASE, json={})
    assert reply.status_code == 400 and "live-account idea" in reply.json["error"]
    assert desk.load_signals()[0]["status"] == "pending" and not execution[2]


def test_legacy_paper_approval_preserves_its_session(execution, monkeypatch):
    desk.save_config(dict(execution[0], mode="manual", paper_research_enabled=False))
    desk.save_signals([dict(execution[1], workspace="paper", mode_at_create="manual")])
    calls = []
    monkeypatch.setattr(desk, "paper_fill", lambda s, c, **k: calls.append(c) or {"ok": True})
    reply = desk.app.test_client().post("/api/signals/review/approve", base_url=BASE, json={})
    assert reply.status_code == 200 and calls[0]["session_active"]
    assert not calls[0].get("_paper_research")


@pytest.mark.parametrize("field", ["stop_loss", "take_profit", "trail_pct"])
def test_broker_approval_rejects_unsupported_exit_fields(execution, field):
    reply = desk.app.test_client().post("/api/signals/review/approve", base_url=BASE, json={field: "1%"})
    assert reply.status_code == 400 and "do not support" in reply.json["error"]
    assert desk.load_signals()[0]["status"] == "pending" and not execution[2]
