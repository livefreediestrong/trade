"""Regressions for the 2026-09-28 review findings: no broker or network calls."""
import copy
import math
from types import SimpleNamespace as NS


import app as desk
import live_agent as agent
import llm_trader as llm
import option_lifecycle
import policy_evidence
import stock_protection
from test_execution_repairs import execution  # noqa: F401 - fixture
from test_live_agent import live, run_again  # noqa: F401 - fixture


def test_chat_answers_with_a_numeric_model_budget(execution, monkeypatch):
    cfg, _, _ = execution
    cfg["live_agent"] = {"policy": dict(agent.DEFAULTS), "revision": "r", "enabled": False}
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "_rate_limited", lambda *a: False)
    monkeypatch.setattr(desk, "_llm_public_status", lambda cfg: {"configured": True})
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "A plain reply")
    response = desk.app.test_client().post("/api/llm/chat", base_url="http://127.0.0.1:5056", json={"message": "hello"})
    assert response.status_code == 200 and response.json["ok"]


def test_stop_accounting_failure_still_runs_stock_exits_and_blocks_entries(live, monkeypatch):
    exits = []
    def failing_reconcile(service):
        raise RuntimeError("journal unavailable")
    monkeypatch.setattr(agent.stop_fills, "reconcile", failing_reconcile)
    monkeypatch.setattr(live.service, "_manage_exits", lambda cfg, policy: exits.append("checked") or False)
    live.service.tick()
    assert exits == ["checked"]
    assert live.calls == [] and live.service.phase == "protection_review"


def test_missing_history_on_several_symbols_is_treated_as_an_outage(live, monkeypatch):
    live.cfg["watchlist"] = ["TEST", "OTHER", "THIRD"]
    live.cfg["live_agent"]["policy"]["symbols"] = ["TEST", "OTHER", "THIRD"]
    desk.save_config(live.cfg)
    def no_history(cfg, *, ticker, notes=None, **kwargs):
        live.calls.append(ticker)
        notes.update(error="Market data was unavailable", coverage_unavailable=True)
        return None
    monkeypatch.setattr(desk, "generate_scan_signal", no_history)
    live.service.tick()
    run_again(live)
    assert len(live.service.load()["research_coverage"]) == 2
    run_again(live)
    assert len(set(live.calls)) == 3
    assert live.service.load()["research_coverage"] == {}  # outage: no hour-long cooldowns


def view(held, filled=0):
    return {"held": held, "available": held, "min_tick": .01,
            "order": {"status": "Submitted", "quantity": 10, "filled": filled, "stop_price": 95.,
                      "permanent_id": 7, "order_id": 1, "client_id": 37, "why_held": "", "con_id": 1}}


def test_holdings_sold_outside_the_stop_shrink_the_tracked_position():
    record = {"ref": "tstop-" + "0" * 32, "accounted_qty": 0}
    plan = stock_protection.decision(record, view(6), 10, 95.)
    assert plan["state"] == "shrink" and plan["held"] == 6


def test_unaccounted_stop_fill_waits_for_its_own_settlement():
    record = {"ref": "tstop-" + "0" * 32, "accounted_qty": 0}
    assert stock_protection.decision(record, view(6, filled=4), 10, 95.)["state"] == "waiting"


def test_reservations_from_an_earlier_process_do_not_consume_later_days():
    data = {"days": {
        "2026-09-01": {"providers": {}, "model_usd": 0., "calls": 0, "reservations": {
            "old": {"status": "pending", "scope": "live_agent", "estimate_usd": 4., "process": "earlier"},
            "legacy": {"status": "pending", "scope": "live_agent", "estimate_usd": 1.}}},
        "2026-09-28": {"providers": {}, "model_usd": 0., "calls": 0, "reservations": {
            "mine": {"status": "pending", "scope": "live_agent", "estimate_usd": .5, "process": llm._PROCESS_ID}}}}}
    assert llm._committed_usage(data, "2026-09-28", "live_agent")["committed_usd"] == .5
    assert llm._committed_usage(data, "2026-09-01", "live_agent")["committed_usd"] == 5.5


def test_policy_receipt_accepts_missing_numbers():
    facts = {"sma": {"sma50": float("nan"), "sma20": 10.}, "rows": [float("inf")]}
    receipt = policy_evidence.capture({"mode": "manual"}, facts, "2026-09-28T00:00:00+00:00", "code")
    same = policy_evidence.capture({"mode": "manual"}, {"sma": {"sma50": None, "sma20": 10.}, "rows": [None]},
                                   "2026-09-28T00:00:00+00:00", "code")
    assert receipt["inputs_hash"] == same["inputs_hash"] and math.isnan(facts["sma"]["sma50"])


class Store:
    def __init__(self, raw, pending=()):
        self.raw = raw
        self.desk = NS(_lock=__import__("threading").RLock(),
                       load_ledger=lambda: {"pending_broker_orders": [{"signal": {"id": p}} for p in pending]})
    def load(self):
        return copy.deepcopy(self.raw)
    def save(self, raw):
        self.raw = raw


def test_failed_dispatch_removes_only_an_unsent_prepared_entry():
    store = Store({"option_positions": {"sig": {"awaiting_entry": True, "fills": {}}}})
    option_lifecycle.discard_prepared(store, {"id": "sig"})
    assert store.raw["option_positions"] == {}
    kept = Store({"option_positions": {"sig": {"awaiting_entry": True, "fills": {}}}}, pending=["sig"])
    option_lifecycle.discard_prepared(kept, {"id": "sig"})
    assert "sig" in kept.raw["option_positions"]

