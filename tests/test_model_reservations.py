"""Exercise shared dollar reservations without any paid model requests."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace as NS
import json

import pytest
import requests

import llm_trader as llm
import claude_brain as claude


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: pytest.fail("Paid calls forbidden"))


def test_concurrent_workers_cannot_spend_same_headroom():
    gate = Barrier(2)
    def attempt(scope):
        with llm.cost_scope(scope, total_cap=.1):
            gate.wait()
            try:
                return llm.reserve_model_attempt(.06, model="fixture")
            except RuntimeError as exc:
                return str(exc)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(attempt, "paper")
        b = pool.submit(attempt, "nightly")
        results = [a.result(), b.result()]
    assert results.count("model_dollar_budget_exceeded") == 1
    assert llm.model_cost_today()["reserved_usd"] == .06


def test_unknown_cost_survives_reload_and_failed_retry_is_not_free():
    with llm.cost_scope("live_agent", scope_cap=.1):
        reservation = llm.reserve_model_attempt(.06, model="fixture")
        llm.settle_model_attempt(reservation)
        assert llm.model_cost_today()["unknown_cost_usd"] == .06
        with pytest.raises(RuntimeError, match="dollar_budget"):
            llm.reserve_model_attempt(.06, model="fixture")
    assert json.loads(llm.USAGE_PATH.read_text())["days"][reservation[0]]["reservations"][reservation[1]]["status"] == "unknown"


def test_settlement_uses_dispatch_day_and_cannot_double_count(monkeypatch):
    day = ["2026-09-24"]
    monkeypatch.setattr(llm, "_usage_day", lambda: day[0])
    with llm.cost_scope("live_agent", scope_cap=.1):
        r = llm.reserve_model_attempt(.08, model="fixture")
        day[0] = "2026-09-25"
        assert llm.model_cost_today()["reserved_usd"] == .08
        with pytest.raises(RuntimeError, match="dollar_budget"):
            llm.reserve_model_attempt(.03, model="fixture")
        llm.settle_model_attempt(r, .02)
    data = json.loads(llm.USAGE_PATH.read_text())
    assert data["days"]["2026-09-24"]["model_usd"] == .02
    assert llm.model_cost_today()["committed_usd"] == 0
    with pytest.raises(RuntimeError, match="already_settled"):
        llm.settle_model_attempt(r, .02)


def test_live_scope_keeps_existing_independence_from_paper_spend():
    with llm.cost_scope("moss_paper"):
        llm.record_model_cost(10)
    with llm.cost_scope("live_agent", scope_cap=.1):
        r = llm.reserve_model_attempt(.08, model="fixture")
        llm.settle_model_attempt(r, .03)
    assert llm.model_cost_today()["scopes"]["live_agent"]["committed_usd"] == .03


def test_gemini_retry_reserves_every_http_attempt(monkeypatch):
    called = []
    def post(*a, **k):
        called.append(1)
        assert llm.model_cost_today()["reserved_usd"] == .06
        return NS(status_code=503, headers={}, json=lambda: {"error": {"message": "unavailable"}})
    monkeypatch.setattr(llm, "estimate_gemini_cost", lambda **kw: .06)
    monkeypatch.setattr(llm.requests, "post", post)
    monkeypatch.setattr(llm._time, "sleep", lambda _: None)
    with llm.cost_scope("after_close", total_cap=.1):
        with pytest.raises(RuntimeError, match="dollar_budget"):
            llm._gemini_generate("q", cfg={"api_key": "fixture"})
    assert called == [1]
    assert llm.model_cost_today()["unknown_cost_usd"] == .06


def test_timeout_keeps_unknown_estimate(monkeypatch):
    def timeout(*a, **k):
        raise requests.Timeout()
    monkeypatch.setattr(llm.requests, "post", timeout)
    with pytest.raises(RuntimeError, match="llm_timeout"):
        llm._gemini_generate("q", cfg={"api_key": "fixture"})
    status = llm.model_cost_today()
    assert status["unknown_cost_usd"] > 0 and status["reserved_usd"] == 0


def test_corrupt_ledger_blocks_before_network():
    llm.USAGE_PATH.write_text('{"days": []}')
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        llm._gemini_generate("q", cfg={"api_key": "fixture"})


@pytest.mark.parametrize("damaged", [[], {"providers": {}, "model_usd": 0, "calls": 0, "reservations": {"r": {"status": "lost", "scope": "desk", "estimate_usd": .2}}}])
def test_nested_corruption_is_unavailable_not_free_headroom(damaged):
    llm.USAGE_PATH.write_text(json.dumps({"days": {llm._usage_day(): damaged}}))
    assert llm.model_cost_today()["error"] == "model_usage_unavailable"
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        llm.reserve_model_attempt(.01, model="fixture")


def test_claude_reservation_and_actual_usage(monkeypatch):
    monkeypatch.setattr(claude, "is_configured", lambda: True)
    def create(**kw):
        assert llm.model_cost_today()["reserved_usd"] > 0
        return NS(model="claude-opus-5", usage=NS(input_tokens=100, output_tokens=100),
                  stop_reason="end_turn", content=[NS(type="text", text=json.dumps({"side":"flat","horizon":"flat","confidence":0,"thesis":"Wait", "risks":[]}))])
    monkeypatch.setattr(claude, "_get_client", lambda: NS(beta=NS(messages=NS(create=create))))
    result = claude.decide({"ticker":"TEST"})
    assert not result.get("error")
    assert llm.model_cost_today()["reserved_usd"] == 0
    assert llm.model_cost_today()["model_usd"] == .003


def test_claude_failed_settlement_holds_and_retains_pending(monkeypatch):
    monkeypatch.setattr(claude, "is_configured", lambda: True)
    def failed(**kw):
        raise RuntimeError("provider failed")
    def no_disk(*a, **kw):
        raise OSError("disk unavailable")
    monkeypatch.setattr(claude, "_get_client", lambda: NS(beta=NS(messages=NS(create=failed))))
    monkeypatch.setattr(llm, "settle_model_attempt", no_disk)
    result = claude.decide({"ticker": "TEST"})
    assert result["error"] == "model_usage_unavailable" and result["side"] == "flat"
    assert llm.model_cost_today()["reserved_usd"] > 0


def test_shadow_inherits_budget_in_new_thread(monkeypatch):
    import app as desk
    seen = []
    def decide(*a, **kw):
        seen.append((llm.current_cost_scope(), dict(llm._COST_SCOPE.limits)))
        return {"side": "flat"}
    monkeypatch.setattr(claude, "decide", decide)
    with llm.cost_scope("live_agent", scope_cap=.12, total_cap=.5):
        shadow = desk._ClaudeShadow({"ticker": "TEST"}, 20)
    assert shadow.result(2) == {"side": "flat"}
    assert seen == [("live_agent", {"scope_cap": .12, "total_cap": .5})]
