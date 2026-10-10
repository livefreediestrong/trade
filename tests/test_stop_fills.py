"""Actual stop accounting, atomic disk boundaries and intercepted broker reports."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import app as desk
import broker_router as router
import broker_ibkr as ibkr
import live_agent as agent
import real_trade_journal
import stock_protection as stops
import stop_fills
from test_execution_repairs import execution  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_trade_intelligence import entered, _hold  # noqa: F401
from test_stock_protection import lifecycle, no_network, manage, view, paper_broker  # noqa: F401


@pytest.fixture
def book(lifecycle, monkeypatch):
    s = lifecycle
    manage(s)
    s.ref = s.service.load()["native_stops"]["TEST"]["ref"]
    s.view = view("Submitted", price=s.commands[0]["stop_price"])
    manage(s)
    s.rows = []
    s.reads = []
    s.journal = real_trade_journal.TradeJournal(desk)
    monkeypatch.setattr(desk._research_companion, "trades", s.journal)
    def history(expected_identity=None):
        s.reads.append(copy.deepcopy(expected_identity))
        return {"ok": True, "identity": copy.deepcopy(s.cfg["broker_identity"]), "executions": copy.deepcopy(s.rows), "excluded": 0}
    monkeypatch.setitem(router.__dict__, "execution_history", history)
    return s


def execution_row(s, exid="first.01", qty=1, cumulative=1, fee=.2, **over):
    return dict(dict(execution_id=exid, account_id=s.cfg["broker_identity"]["account_id"], paper_mode=True,
        verified=True, ticker="TEST", asset_type="STK", currency="USD", side="sell", con_id=42,
        permanent_order_id=700, order_id=7, client_id=37, order_ref=s.ref,
        ts=(datetime.now(timezone.utc)-timedelta(seconds=3)).isoformat(), shares=qty, price=99.2,
        cumulative_shares=cumulative, commission=fee, commission_currency="USD" if fee is not None else None,
        pending_price_revision=False, broker_realized_pnl=None, voided=False), **over)


def reconcile(s, *rows, status="Submitted", filled=1, held=1):
    s.rows = list(rows)
    s.view = view(status, filled=filled, held=held, available=held, price=s.commands[0]["stop_price"])
    stop_fills.reconcile(s.service, force=True)


def stop_rows():
    return [r for r in desk.load_ledger().get("broker_fills", []) if r.get("native_stop_ref")]


def active(s):
    return s.service.load()["native_stops"]["TEST"]


def test_partial_fill_is_written_once_and_managed_shares_shrink_once(book):
    s = book
    row = execution_row(s)
    paper_before = {k: copy.deepcopy(desk.load_ledger().get(k)) for k in ("fills", "cash", "equity", "positions")}
    reconcile(s, row)
    first_id = stop_rows()[0]["id"]
    reconcile(s, row, row)
    assert len(stop_rows()) == 1 and stop_rows()[0]["id"] == first_id
    assert stop_rows()[0]["shares"] == 1 and stop_rows()[0]["fee_usd"] == .2
    assert s.service.load()["managed"]["TEST"]["shares"] == 1
    assert active(s)["accounted_qty"] == 1 and active(s)["accounting_settled"] is True
    assert not desk.load_ledger().get("pending_broker_orders")
    assert {k: desk.load_ledger().get(k) for k in paper_before} == paper_before
    assert s.journal.status()["broker_paper"]["executions"] == 1


def test_holdings_only_check_cannot_double_apply_native_fill(book, monkeypatch):
    s = book
    row = execution_row(s)
    s.view = view("Submitted", filled=1, held=1, available=1, price=s.commands[0]["stop_price"])
    _hold(monkeypatch, 1, 100.1)
    manage(s)
    assert s.service.load()["managed"]["TEST"]["shares"] == 2
    reconcile(s, row)
    manage(s)
    assert s.service.load()["managed"]["TEST"]["shares"] == 1
    assert len(s.commands) == 1  # The broker already has the correct one-share remainder.


def test_late_fee_updates_same_fill_and_releases_hold(book):
    s = book
    row = execution_row(s, fee=None)
    reconcile(s, row)
    fid = stop_rows()[0]["id"]
    assert stop_rows()[0]["fee_usd"] is None and not stop_rows()[0]["fees_reported"]
    assert stops.pending_error(s.service.load(), s.cfg["broker_identity"])
    reconcile(s, dict(row, commission=.3, commission_currency="USD"))
    assert stop_rows()[0]["id"] == fid and stop_rows()[0]["fee_usd"] == .3
    assert active(s)["accounting_settled"]
    assert stops.pending_error(s.service.load(), s.cfg["broker_identity"]) is None
    reconcile(s, row)  # A reconnect omits a previously verified fee.
    assert stop_rows()[0]["fee_usd"] == .3


def test_complete_fill_closes_only_owned_position_and_unblocks_without_activation(book):
    s = book
    a, b = execution_row(s), execution_row(s, "second.01", cumulative=2, fee=.1, price=99)
    cfg = desk.load_config()
    cfg.update(mode="manual", session_active=False)
    cfg["live_agent"]["enabled"] = False
    desk.save_config(cfg)
    reconcile(s, a, b, status="Filled", filled=2, held=0)
    raw = s.service.load()
    assert not raw["managed"] and not raw["native_stops"]
    assert raw["native_stop_history"][0]["accounting_settled"]
    assert stop_rows()[0]["shares"] == 2 and stop_rows()[0]["price"] == 99.1
    assert stop_rows()[0]["fee_usd"] == .3 and stop_rows()[0]["broker_reconciled"]
    assert stops.pending_error(raw, s.cfg["broker_identity"]) is None
    assert desk.load_config() == cfg and len(s.commands) == 1 and len(s.entered.sent) == 1
    count = sum(day["trades"] for day in desk.load_ledger()["broker_daily"].values())
    reconcile(s, a, b, status="Filled", filled=2, held=0)
    assert sum(day["trades"] for day in desk.load_ledger()["broker_daily"].values()) == count


def test_positions_must_catch_up_before_resume(book):
    row = execution_row(book)
    reconcile(book, row, held=2)
    assert stop_rows()[0]["shares"] == 1 and book.service.load()["managed"]["TEST"]["shares"] == 1
    assert active(book)["state"] == "waiting" and not active(book)["accounting_settled"]
    reconcile(book, row, held=1)
    assert active(book)["accounting_settled"] and book.service.load()["managed"]["TEST"]["shares"] == 1


@pytest.mark.parametrize("boundary", ["before_ledger", "after_ledger", "after_signal_save"])
def test_restart_recovers_each_disk_boundary_without_double_count(book, monkeypatch, boundary):
    s = book
    row = execution_row(s)
    original_save, original_signals = s.service.save, desk.save_signals
    def save(raw):
        record = raw.get("native_stops", {}).get("TEST", {})
        if boundary == "before_ledger" or (boundary == "after_signal_save" and record.get("accounting")):
            raise OSError("simulated disk failure")
        original_save(raw)
    def signals(rows):
        raise OSError("simulated failure after ledger write")
    monkeypatch.setattr(s.service, "save", save)
    if boundary == "after_ledger": monkeypatch.setattr(desk, "save_signals", signals)
    with pytest.raises(OSError): reconcile(s, row)
    assert s.service.stop_accounting_error
    monkeypatch.setattr(s.service, "save", original_save)
    monkeypatch.setattr(desk, "save_signals", original_signals)
    s.service = agent.LiveAgent(desk)  # Actual reload of durable state.
    monkeypatch.setattr(desk, "_live_agent", s.service)
    reconcile(s, row)
    assert len(stop_rows()) == 1 and s.service.load()["managed"]["TEST"]["shares"] == 1
    assert active(s)["accounted_qty"] == 1
    reconcile(s, row)
    assert len(stop_rows()) == 1 and s.service.load()["managed"]["TEST"]["shares"] == 1


def test_qty_correction_updates_ledger_restores_shares_and_requires_review(book):
    s = book
    original = execution_row(s)
    reconcile(s, original)
    correction = dict(original, execution_id="first.02", shares=0, price=0, voided=True, commission=.05)
    reconcile(s, correction, filled=0, held=2)
    fill = stop_rows()[0]
    assert fill["shares"] == 0 and fill["broker_fill_state"] == "voided" and fill["fee_usd"] == .05
    assert s.service.load()["managed"]["TEST"]["shares"] == 2
    assert active(s)["review_required"] and stops.pending_error(s.service.load(), s.cfg["broker_identity"])
    reconcile(s, original, filled=0, held=2)
    assert stop_rows()[0]["shares"] == 0  # A delayed .01 cannot restore the old fill.


def test_correction_after_close_restores_old_tracking_without_reactivation(book):
    s = book
    original = execution_row(s, qty=2, cumulative=2)
    reconcile(s, original, status="Filled", filled=2, held=0)
    assert not s.service.load()["managed"]
    changed = dict(original, execution_id="first.02", shares=1, commission=.1)
    reconcile(s, changed, status="Filled", filled=2, held=1)
    raw = s.service.load()
    assert stop_rows()[0]["shares"] == 1
    assert raw["managed"]["TEST"]["shares"] == 1 and raw["managed"]["TEST"]["tracking_error"]
    assert raw["native_stop_history"][0]["review_required"]
    assert stops.pending_error(raw, s.cfg["broker_identity"])


def test_old_correction_cannot_mutate_a_new_position_same_ticker(book):
    s = book
    original = execution_row(s, qty=2, cumulative=2)
    reconcile(s, original, status="Filled", filled=2, held=0)
    raw = s.service.load()
    new = copy.deepcopy(raw["native_stop_history"][0]["position_snapshot"])
    new.update(opened_at=datetime.now(timezone.utc).isoformat(), shares=7)
    raw["managed"]["TEST"] = new
    s.service.save(raw)
    reconcile(s, dict(original, execution_id="first.02", shares=1), status="Filled", filled=2, held=8)
    assert s.service.load()["managed"]["TEST"] == new
    assert stops.pending_error(s.service.load(), s.cfg["broker_identity"])


@pytest.mark.parametrize("field,value", [("commission_currency", "CAD"), ("commission", None)])
def test_missing_or_non_usd_fees_do_not_become_zero_dollars(book, field, value):
    row = execution_row(book, **{field: value})
    reconcile(book, row)
    assert stop_rows()[0]["fee_usd"] is None and not active(book)["accounting_settled"]
    if field == "commission_currency": assert stop_rows()[0]["fees_by_currency"] == {"CAD": .2}


@pytest.mark.parametrize("mutation", ["wrong_contract", "wrong_order", "wrong_side", "provisional", "incomplete", "no_cumulative"])
def test_bad_execution_evidence_never_changes_ledger_or_managed_shares(book, mutation):
    row = execution_row(book)
    if mutation == "wrong_contract": row["con_id"] = 1234
    if mutation == "wrong_order": row["permanent_order_id"] = 99
    if mutation == "wrong_side": row["side"] = "buy"
    if mutation == "provisional": row["pending_price_revision"] = True
    if mutation == "no_cumulative": row["cumulative_shares"] = None
    if mutation == "incomplete": row["cumulative_shares"] = 2
    reconcile(book, row)
    assert not stop_rows() and book.service.load()["managed"]["TEST"]["shares"] == 2
    assert active(book)["state"] == "review"


def test_same_execution_id_cannot_change_quantity_or_price(book):
    row = execution_row(book)
    reconcile(book, row)
    reconcile(book, dict(row, price=97))
    assert stop_rows()[0]["price"] == 99.2 and active(book)["accounting_error"]


def test_current_account_change_during_broker_read_cannot_write_other_book(book, monkeypatch):
    before = copy.deepcopy(book.service.load())
    row = execution_row(book)
    def changed(*args):
        cfg = desk.load_config()
        cfg["broker_identity"]["account_id"] = "OTHER"
        desk.save_config(cfg)
        return {"ok": True, "view": view("Submitted", filled=1, held=1, available=1)}
    monkeypatch.setitem(router.__dict__, "observe_stock_stop", changed)
    reconcile(book, row)
    assert not stop_rows() and book.service.load() == before


def test_cancel_race_only_releases_remaining_shares_after_fees_and_holdings(book, monkeypatch):
    s = book
    _hold(monkeypatch, 2, 102)
    manage(s)
    assert s.commands[-1]["action"] == "cancel"
    row = execution_row(s, fee=None)
    reconcile(s, row, status="Cancelled", filled=1, held=1)
    _hold(monkeypatch, 1, 102)
    manage(s)
    assert len(s.entered.sent) == 1
    reconcile(s, dict(row, commission=.2, commission_currency="USD"), status="Cancelled", filled=1, held=1)
    manage(s)
    assert len(s.entered.sent) == 2 and s.entered.sent[-1]["shares"] == 1


def test_raw_history_includes_cumulative_and_checks_expected_account(paper_broker, monkeypatch):
    b = paper_broker
    ex = NS(execId="native.01", acctNumber="DU123", time=datetime.now(timezone.utc)-timedelta(seconds=1),
            shares=1, price=99, side="SLD", exchange="NYSE", orderId=7, clientId=37, permId=700,
            orderRef="ref", cumQty=1, pendingPriceRevision=True)
    native = NS(execution=ex, contract=b.contract, commissionReport=NS(execId="", commission=0, currency="", realizedPNL=0))
    b.reqExecutions = lambda f: [native]
    b.sleep = lambda sec: None
    result = ibkr.execution_history(expected_identity=b.identity)
    assert result["ok"] and result["executions"][0]["cumulative_shares"] == 1
    assert result["executions"][0]["pending_price_revision"] is True
    assert not ibkr.execution_history(expected_identity=dict(b.identity, account_id="OTHER"))["ok"]
    def disconnect(sec): b.connected = False
    b.sleep = disconnect
    assert not ibkr.execution_history(expected_identity=b.identity)["ok"]


@pytest.mark.parametrize("status", ["Cancelled", "ApiCancelled", "Inactive", "PendingCancel", "PendingSubmit"])
def test_accounting_does_not_make_unaccepted_or_unrequested_cancelled_stop_safe(book, status):
    reconcile(book, execution_row(book), status=status)
    assert stop_rows()[0]["shares"] == 1
    assert active(book)["state"] in {"review", "waiting"}
    assert stops.pending_error(book.service.load(), book.cfg["broker_identity"])


def test_weaker_stop_after_fill_needs_resize_before_resuming(book):
    book.rows = [execution_row(book)]
    book.view = view("Submitted", filled=1, held=1, available=1, price=90)
    stop_fills.reconcile(book.service, force=True)
    assert active(book)["state"] == "waiting" and active(book)["verified_quantity"] == 0
    assert stops.entry_error(book.service.load(), book.cfg)


def test_price_correction_updates_same_ledger_row_and_keeps_review_hold(book):
    row = execution_row(book)
    reconcile(book, row)
    fid = stop_rows()[0]["id"]
    reconcile(book, dict(row, execution_id="first.02", price=98.2, commission=.4))
    assert stop_rows()[0]["id"] == fid and stop_rows()[0]["price"] == 98.2
    assert stop_rows()[0]["fee_usd"] == .4 and active(book)["review_required"]
    assert book.service.load()["managed"]["TEST"]["shares"] == 1


def test_cancelled_stop_replacement_archives_accounting_and_starts_new_receipt(book, monkeypatch):
    s = book
    _hold(monkeypatch, 2, 102)
    manage(s)
    reconcile(s, execution_row(s), status="Cancelled")
    old = copy.deepcopy(active(s))
    _hold(monkeypatch, 1, 100.1)  # Target is no longer reached; restore a working stop.
    manage(s)
    current = active(s)
    assert s.commands[-1]["action"] == "create" and s.commands[-1]["quantity"] == 1
    assert current["ref"] != s.ref and "accounted_qty" not in current and "accounting" not in current
    assert s.service.load()["native_stop_history"][0] == old
    assert current["held_anchor"] == 1 and current["tracked_anchor"] == 1


@pytest.mark.parametrize("mutation", ["history_shape", "history_record", "repeat_ref", "quantity", "flag", "anchor", "receipt"])
def test_damaged_stop_accounting_is_retained_and_blocks_recovery(book, mutation):
    reconcile(book, execution_row(book))
    raw = book.service.load()
    record = raw["native_stops"]["TEST"]
    if mutation == "history_shape": raw["native_stop_history"] = {}
    if mutation == "history_record": raw["native_stop_history"] = [None]
    if mutation == "repeat_ref": raw["native_stop_history"] = [copy.deepcopy(record)]
    if mutation == "quantity": record["accounted_qty"] = True
    if mutation == "flag": record["accounting_settled"] = "yes"
    if mutation == "anchor": record["held_anchor"] = -1
    if mutation == "receipt": record["accounting"]["fee_usd"] = 0
    book.service.save(raw)
    persisted = book.service.path.read_bytes()
    with pytest.raises(ValueError, match="recovery"):
        stop_fills.reconcile(book.service, force=True)
    assert book.service.stop_accounting_error and book.service.path.read_bytes() == persisted


def test_other_accounts_and_order_references_are_not_this_stop(book):
    row = execution_row(book)
    reconcile(book, dict(row, account_id="OTHER"), dict(row, execution_id="unrelated.01", order_ref="unrelated"))
    assert not stop_rows() and book.service.load()["managed"]["TEST"]["shares"] == 2


def test_recovered_execution_counts_on_its_trade_day_not_restart_day(book):
    stamp = (datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    reconcile(book, execution_row(book, ts=stamp))
    day = datetime.fromisoformat(stamp).astimezone(desk.paper_loop_mod.NY_TZ).date().isoformat()
    ledger = desk.load_ledger()
    assert ledger["broker_counted_orders"][stop_rows()[0]["order_id"]] == day
    assert stop_rows()[0]["ts"] == stamp


def test_history_failure_cannot_clear_pending_settlement(book, monkeypatch):
    reconcile(book, execution_row(book, fee=None))
    before = copy.deepcopy(book.service.load())
    monkeypatch.setitem(router.__dict__, "execution_history", lambda **kw: {"ok": False, "error": "offline"})
    stop_fills.reconcile(book.service, force=True)
    assert book.service.load() == before
    assert stops.pending_error(before, book.cfg["broker_identity"])


def test_restarted_desk_blocks_manual_order_before_quote_until_fresh_history(book, monkeypatch):
    s = book
    s.service = agent.LiveAgent(desk)
    monkeypatch.setattr(desk, "_live_agent", s.service)
    cfg = desk.load_config()
    cfg.update(mode="live_manual", session_active=True)
    cfg["live_agent"]["enabled"] = False
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "fetch_last_price", lambda *a: pytest.fail("Restart guard must run before a quote or order"))
    result = desk.execute_gated_broker_or_paper({"id": "manual-next", "ticker": "TEST", "side": "sell"}, cfg, source="test", via="test")
    assert not result["ok"] and "fresh check" in result["error"]
    assert len(s.entered.sent) == 1
    stop_fills.reconcile(s.service, force=True)
    assert stop_fills.gate_error(s.service, s.service.load(), cfg["broker_identity"]) is None
    assert desk.load_config() == cfg


def test_runtime_disk_failure_hold_is_account_scoped_and_survives_failed_history(book, monkeypatch):
    s = book
    identity = s.cfg["broker_identity"]
    monkeypatch.setattr(s.service, "save", lambda *a: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError): reconcile(s, execution_row(s))
    assert stop_fills.gate_error(s.service, s.service.load(), identity)
    assert stop_fills.gate_error(s.service, s.service.load(), dict(identity, account_id="OTHER")) is None
    monkeypatch.setitem(router.__dict__, "execution_history", lambda **kw: {"ok": False})
    stop_fills.reconcile(s.service, force=True)
    assert stop_fills.gate_error(s.service, s.service.load(), identity)
