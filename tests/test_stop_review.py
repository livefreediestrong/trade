"""Correction review uses actual ledger/state writes and intercepted broker reads."""
import copy
import threading

import pytest

import app as desk
import broker_router as router
import live_agent
import stop_fills
import stop_review
import stock_protection
from test_stop_fills import book, execution_row, reconcile, active, stop_rows  # noqa: F401
from test_execution_repairs import execution, BASE  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_trade_intelligence import entered  # noqa: F401
from test_stock_protection import lifecycle, no_network  # noqa: F401


@pytest.fixture
def corrected(book):
    s = book
    original = execution_row(s)
    reconcile(s, original)
    s.corrected = dict(original, execution_id="first.02", price=98, commission=.3)
    reconcile(s, s.corrected)
    cfg = desk.load_config()
    cfg.update(mode="manual", session_active=False)
    cfg["live_agent"]["enabled"] = False
    desk.save_config(cfg)
    return s


def test_review_shows_change_and_acknowledgement_survives_reconciliation_restart(corrected, monkeypatch):
    s = corrected
    cfg = copy.deepcopy(desk.load_config())
    review = stop_review.prepare(s.service, s.ref)["review"]
    assert review["can_accept"] and review["before"]["price"] == 99.2 and review["after"]["price"] == 98
    assert review["after"]["fees"] == .3 and review["remaining"] == 1
    assert active(s)["review_required"]
    assert stop_review.accept(s.service, review["token"], True)["ok"]
    assert not active(s)["review_required"] and active(s)["state"] == "protected"
    assert len(active(s)["correction_reviews"]) == 1
    assert not s.service.load()["managed"]["TEST"].get("tracking_error")
    s.service = live_agent.LiveAgent(desk)
    monkeypatch.setattr(desk, "_live_agent", s.service)
    reconcile(s, s.corrected)
    assert not active(s)["review_required"]
    assert stock_protection.pending_error(s.service.load(), cfg["broker_identity"]) is None
    assert desk.load_config() == cfg and len(s.commands) == 1 and len(s.entered.sent) == 1


def test_new_correction_after_ack_requires_new_review(corrected):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    stop_review.accept(s.service, token, True)
    reconcile(s, dict(s.corrected, execution_id="first.03", price=97))
    assert active(s)["review_required"] and active(s)["correction_before"]["filled_avg_price"] == 98
    assert len(active(s)["correction_reviews"]) == 1


@pytest.mark.parametrize("field", ["session", "agent", "real_account", "busy", "pending_order", "wrong_ref"])
def test_review_never_bypasses_execution_state_guards(corrected, field):
    s = corrected
    cfg = desk.load_config()
    if field == "session": cfg["session_active"] = True
    if field == "agent": cfg["live_agent"]["enabled"] = True
    if field == "real_account": cfg["broker_identity"]["paper_mode"] = False
    if field == "busy": s.service.cycle_lock.acquire()
    if field == "pending_order":
        ledger = desk.load_ledger(); ledger["pending_broker_orders"] = [{"order_id": "unresolved"}]; desk.save_ledger(ledger)
    desk.save_config(cfg)
    with pytest.raises(ValueError): stop_review.prepare(s.service, "tstop-"+"0"*32 if field == "wrong_ref" else s.ref)
    assert active(s)["review_required"] and not getattr(s.service, "stop_review_tokens", {})


@pytest.mark.parametrize("change", ["fee", "price", "holding", "stop", "config", "session", "account"])
def test_evidence_change_during_review_needs_new_confirmation(corrected, change):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    if change == "fee": s.rows[0]["commission"] = .5
    if change == "price": s.rows[0].update(execution_id="first.03", price=97)
    if change == "holding": s.view["held"] = 2
    if change == "stop": s.view["order"]["stop_price"] = 90
    if change in {"config", "session", "account"}:
        cfg = desk.load_config()
        if change == "config": cfg["live_agent"]["revision"] = "changed"
        if change == "session": cfg["session_active"] = True
        if change == "account": cfg["broker_identity"]["account_id"] = "OTHER"
        desk.save_config(cfg)
    with pytest.raises(ValueError): stop_review.accept(s.service, token, True)
    assert active(s)["review_required"] and not active(s).get("correction_reviews")
    with pytest.raises(ValueError): stop_review.accept(s.service, token, True)


@pytest.mark.parametrize("problem", ["missing_fee", "positions", "weak_stop", "cancelled", "new_position", "other_error"])
def test_unsafe_reconciliation_explains_problem_without_an_accept_token(corrected, problem):
    s = corrected
    if problem == "missing_fee": s.rows[0].update(execution_id="first.03", commission=None, commission_currency=None)
    if problem == "positions": s.view["held"] = 2
    if problem == "weak_stop": s.view["order"]["stop_price"] = 90
    if problem == "cancelled": s.view["order"]["status"] = "Cancelled"
    if problem in {"new_position", "other_error"}:
        raw = s.service.load()
        if problem == "new_position": raw["managed"]["TEST"]["opened_at"] = "2020-01-01T00:00:00+00:00"
        else: raw["managed"]["TEST"]["tracking_error"] = "Another entry fill needs reconciliation"
        s.service.save(raw)
    review = stop_review.prepare(s.service, s.ref)["review"]
    assert not review["can_accept"] and review["issues"] and "token" not in review
    assert active(s)["review_required"]


def test_expired_duplicate_and_restart_tokens_do_not_acknowledge(corrected):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    s.service.stop_review_tokens[token]["expires"] = 0
    with pytest.raises(ValueError, match="expired"): stop_review.accept(s.service, token, True)
    review = stop_review.prepare(s.service, s.ref)["review"]
    with pytest.raises(ValueError): stop_review.accept(live_agent.LiveAgent(desk), review["token"], True)
    stop_review.accept(s.service, review["token"], True)
    with pytest.raises(ValueError): stop_review.accept(s.service, review["token"], True)
    assert len(active(s)["correction_reviews"]) == 1


def test_zero_remaining_archived_price_correction_can_be_reviewed(book):
    s = book
    row = execution_row(s, qty=2, cumulative=2)
    reconcile(s, row, status="Filled", filled=2, held=0)
    reconcile(s, dict(row, execution_id="first.02", price=98), status="Filled", filled=2, held=0)
    cfg = desk.load_config(); cfg.update(session_active=False); cfg["live_agent"]["enabled"] = False; desk.save_config(cfg)
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    stop_review.accept(s.service, token, True)
    raw = s.service.load()
    assert not raw["managed"] and not raw["native_stops"]
    assert not raw["native_stop_history"][0]["review_required"]
    assert stock_protection.pending_error(raw, cfg["broker_identity"]) is None


def test_failed_ack_save_retains_hold_and_requires_a_new_review(corrected, monkeypatch):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    original = s.service.save
    def fail(raw):
        if raw["native_stops"]["TEST"].get("correction_reviews"): raise OSError("disk full")
        original(raw)
    monkeypatch.setattr(s.service, "save", fail)
    with pytest.raises(OSError): stop_review.accept(s.service, token, True)
    assert active(s)["review_required"] and not active(s).get("correction_reviews")
    with pytest.raises(ValueError): stop_review.accept(s.service, token, True)


def test_http_review_strict_payload_no_activation_or_order_routes(corrected):
    client = desk.app.test_client()
    assert client.post("/api/live-agent/corrections/review", base_url=BASE, json={"ref": corrected.ref,"ignore_risk": True}).status_code == 400
    review = client.post("/api/live-agent/corrections/review", base_url=BASE, json={"ref": corrected.ref}).json["review"]
    assert client.post("/api/live-agent/corrections/accept", base_url=BASE, json={"token": review["token"],"acknowledge": False}).status_code == 400
    result = client.post("/api/live-agent/corrections/accept", base_url=BASE, json={"token": review["token"],"acknowledge": True})
    assert result.status_code == 200 and "paused" in result.json["message"]
    assert not desk.load_config()["session_active"] and not desk.load_config()["live_agent"]["enabled"]
    assert len(corrected.commands) == 1 and len(corrected.entered.sent) == 1


def test_duplicate_concurrent_acknowledgements_have_one_durable_receipt(corrected):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    results = []
    def accept():
        try: results.append(stop_review.accept(s.service, token, True)["ok"])
        except ValueError: results.append(False)
    threads = [threading.Thread(target=accept) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(5)
    assert sorted(results) == [False, True] and not any(t.is_alive() for t in threads)
    assert len(active(s)["correction_reviews"]) == 1


def test_bust_correction_requires_matching_broker_counts_and_working_protection(book):
    s = book
    original = execution_row(s)
    reconcile(s, original)
    bust = dict(original, execution_id="first.02", shares=0, price=0, voided=True)
    reconcile(s, bust, filled=0, held=2)
    s.view["available"] = 2
    cfg = desk.load_config(); cfg["session_active"] = False; cfg["live_agent"]["enabled"] = False; desk.save_config(cfg)
    review = stop_review.prepare(s.service, s.ref)["review"]
    assert review["can_accept"] and review["after"]["shares"] == 0 and review["remaining"] == 2
    stop_review.accept(s.service, review["token"], True)
    reconcile(s, bust, filled=0, held=2)
    assert active(s)["state"] == "protected" and not active(s)["review_required"]
    assert s.service.load()["managed"]["TEST"]["shares"] == 2 and stop_rows()[0]["shares"] == 0


def test_damaged_review_receipt_cannot_release_a_correction(corrected):
    s = corrected
    token = stop_review.prepare(s.service, s.ref)["review"]["token"]
    stop_review.accept(s.service, token, True)
    raw = s.service.load(); raw["native_stops"]["TEST"]["correction_reviews"] = []
    s.service.save(raw)
    with pytest.raises(ValueError, match="recovery"): s.service.load()


def test_cross_site_request_cannot_acknowledge_and_get_cannot_create_review(corrected):
    client = desk.app.test_client()
    token = stop_review.prepare(corrected.service, corrected.ref)["review"]["token"]
    response = client.post("/api/live-agent/corrections/accept", base_url=BASE,
        headers={"Origin":"https://untrusted.example","Sec-Fetch-Site":"cross-site"},
        json={"token":token,"acknowledge":True})
    assert response.status_code == 403 and active(corrected)["review_required"]
    assert client.get("/api/live-agent/corrections/review", base_url=BASE).status_code == 405
