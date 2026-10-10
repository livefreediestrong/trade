"""Provider boundary regressions; all HTTP and usage storage are isolated."""
import json
from types import SimpleNamespace as NS

import pytest
import llm_trader as llm


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(llm, "_JEV_BUDGET", llm._GeminiBudget(rpm=30, daily=500, provider="jev"))
    monkeypatch.setattr(llm, "typesafe_api_key", lambda: "fixture-not-a-secret")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: pytest.fail("Unexpected HTTP"))


def response(monkeypatch, payload):
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: NS(status_code=200, json=lambda: payload))


@pytest.mark.parametrize("confidence", [True, False, "NaN", "Infinity", float("nan"), float("inf"), -0.1, 42, "0.9", None])
def test_jev_invalid_confidence_holds(monkeypatch, confidence):
    response(monkeypatch, {"answers": {"side": {"type": "choice", "choice": "buy", "confidence": confidence,
                                              "probabilities": {"buy": .8, "sell": .1, "flat": .1}}}})
    out = llm.jev_trade_thesis({"ticker": "TEST"})
    assert out["error"] == "invalid_jev_schema" and out["side"] == "flat" and out["confidence"] == 0


@pytest.mark.parametrize("payload", [[], None, {}, {"answers": []}, {"answers": {"side": []}},
                                     {"answers": {"side": {"choice": "buy", "confidence": .9}}}])
def test_jev_missing_or_malformed_schema_holds(monkeypatch, payload):
    response(monkeypatch, payload)
    assert llm.jev_trade_thesis({"ticker": "TEST"})["error"] == "invalid_jev_schema"


@pytest.mark.parametrize("confidence", [0, 0.5, 1])
def test_jev_valid_confidence_preserved(monkeypatch, confidence):
    response(monkeypatch, {"answers": {"side": {"type": "choice", "choice": "buy", "confidence": confidence,
                                              "probabilities": {"buy": .8, "sell": .1, "flat": .1}}}})
    result = llm.jev_trade_thesis({"ticker": "TEST"})
    assert result["error"] is None and result["confidence"] == confidence


@pytest.mark.parametrize("probabilities", [None, {}, {"buy": .8}, {"buy": True, "sell": 0, "flat": 0},
    {"buy": .8, "sell": .8, "flat": .1}, {"buy": .1, "sell": .8, "flat": .1},
    {"buy": float("nan"), "sell": .1, "flat": .1}])
def test_jev_invalid_or_contradictory_distribution_holds(monkeypatch, probabilities):
    response(monkeypatch, {"answers": {"side": {"type": "choice", "choice": "buy", "confidence": .9,
                                               "probabilities": probabilities}}})
    assert llm.jev_trade_thesis({"ticker": "TEST"})["error"] == "invalid_jev_schema"


def test_jev_asks_one_direction_question_and_uses_its_own_confidence(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return NS(status_code=200, json=lambda: {"answers": {"side": {"type": "choice", "choice": "flat",
            "confidence": .6, "probabilities": {"buy": .1, "sell": .2, "flat": .7}}}, "usage": {"input_tokens": 50, "output_tokens": 30}})
    monkeypatch.setattr(llm.requests, "post", post)
    r = llm.jev_trade_thesis({"ticker": "TEST"})
    assert r["side"] == "flat" and r["confidence"] == .6 and r["error"] is None
    assert set(calls[0]["questions"]) == {"side"}


@pytest.mark.parametrize("lane", ["thesis", "advisory"])
@pytest.mark.parametrize("failure", ["daily", "rpm", "corrupt", "storage"])
def test_jev_reserves_budget_before_http(monkeypatch, lane, failure):
    if failure == "corrupt":
        llm.USAGE_PATH.write_text("{")
    elif failure == "storage":
        monkeypatch.setattr(llm, "_write_usage", lambda data: (_ for _ in ()).throw(OSError("fixture")))
    else:
        ledger = llm._read_usage()
        ledger["days"][llm._usage_day()]["providers"]["jev"] = {
            "attempts": 500 if failure == "daily" else 1,
            "recent": [llm._time.time()] * (30 if failure == "rpm" else 0)}
        llm.USAGE_PATH.write_text(json.dumps(ledger))
    result = llm.jev_trade_thesis({"ticker": "TEST"}) if lane == "thesis" else llm.jev_advisory_panel("buy", "fixture")
    if lane == "thesis":
        assert result["side"] == "flat" and result["error"]
    else:
        assert result["policy_label"] == "UNKNOWN" and result["reason"]


def test_jev_failed_attempts_share_budget_between_lanes(monkeypatch):
    calls = []
    def failed_http(*a, **k):
        calls.append(True)
        raise llm.requests.Timeout("fixture")
    monkeypatch.setattr(llm.requests, "post", failed_http)
    monkeypatch.setattr(llm, "_JEV_BUDGET", llm._GeminiBudget(rpm=30, daily=2, provider="jev"))
    assert llm.jev_trade_thesis({"ticker": "TEST"})["error"]
    assert llm.jev_advisory_panel("buy", "fixture")["policy_label"] == "UNKNOWN"
    assert llm.jev_trade_thesis({"ticker": "TEST"})["error"] == "jev_daily_budget_exceeded"
    assert len(calls) == 2 and llm._JEV_BUDGET.status()["daily_used"] == 2
