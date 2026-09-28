"""History test for Fox's buy rule: summary lines, gate states, the buy lock and idling."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import app as desk
import live_agent as agent
import rule_history
from test_execution_repairs import execution  # noqa: F401 - fixture
from test_live_agent import live, run_again  # noqa: F401 - fixtures


def trades(n_days, per_day, ret_bps, rule=True):
    start = datetime(2025, 1, 1)
    return [{"day": (start + timedelta(days=d)).strftime("%Y-%m-%d"), "rule": rule, "ret": (ret_bps + (i % 3) - 1) / 1e4}
            for d in range(n_days) for i in range(per_day)]


GOOD_WF = {"conclusion": "consistent_out_of_sample", "tested_folds": 10, "positive_rule_folds": 8}
BAD_WF = {"conclusion": "inconsistent_out_of_sample", "tested_folds": 30, "positive_rule_folds": 2}


def test_lines_subtract_costs_and_cluster_by_day():
    ln = rule_history.line(trades(50, 3, 30), 10.0)
    assert ln["trades"] == 150 and ln["days"] == 50 and ln["avg_bps"] == pytest.approx(20.0)
    assert ln["lower95_bps"] <= ln["avg_bps"] and ln["win_rate"] == 1.0


def test_summary_passes_only_with_profit_at_your_costs_and_consistent_folds():
    good = rule_history.summarize(trades(60, 6, 300), 245.0, walk=GOOD_WF)
    assert good["passed"] and good["lines"]["your_costs"]["avg_bps"] == pytest.approx(55.0)
    losing = rule_history.summarize(trades(60, 6, -3), 245.0, walk=GOOD_WF)
    assert not losing["passed"] and "loses money" in losing["reasons"][0]
    inconsistent = rule_history.summarize(trades(60, 6, 300), 245.0, walk=BAD_WF)
    assert not inconsistent["passed"] and "2 of 30" in inconsistent["reasons"][0]
    unknown = rule_history.summarize(trades(60, 6, 300), None, walk=GOOD_WF)
    assert not unknown["passed"] and "unknown" in unknown["reasons"][0]
    few = rule_history.summarize(trades(10, 5, 300), 245.0, walk=GOOD_WF)
    assert not few["passed"] and "needs 200" in few["reasons"][0]


def save(tmp_path, passed, age_days=0):
    d = NS(DATA_DIR=tmp_path)
    finished = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    summary = rule_history.summarize(trades(60, 6, 300 if passed else -3), 245.0, walk=GOOD_WF)
    (tmp_path / "rule_history.json").write_text(json.dumps({"finished_at": finished, "tickers": ["A"], "period": {},
                                                             "summary": summary}), encoding="utf-8")
    return d


def test_gate_states_fail_closed(tmp_path):
    assert rule_history.gate(NS(DATA_DIR=tmp_path))["state"] == "missing"
    assert rule_history.gate(save(tmp_path, True))["state"] == "passed"
    assert rule_history.gate(save(tmp_path, False))["state"] == "failed"
    stale = rule_history.gate(save(tmp_path, True, age_days=31))
    assert stale["state"] == "stale" and not stale["passed"]


@pytest.mark.promotion_gate
def test_buy_lock_requires_history(live, monkeypatch):
    monkeypatch.setattr(live.service, "history_gate", lambda: {"passed": False, "state": "failed", "plain": "lost money"})
    status = live.service.promotion(desk.load_config())
    assert not status["passed"] and "lost money" in status["plain"] and "a passing history test" in status["missing"]


def test_fox_idles_while_history_says_the_rule_loses(live, monkeypatch):
    monkeypatch.setattr(live.service, "history_gate", lambda: {"passed": False, "state": "failed", "plain": "lost money"})
    live.service.tick()
    assert live.calls == [] and live.sent == [] and live.service.phase == "rule_failed"


def test_missing_history_does_not_stop_research(live, monkeypatch):
    monkeypatch.setattr(live.service, "history_gate", lambda: {"passed": False, "state": "missing", "plain": "none"})
    live.service.tick()
    assert live.calls == ["TEST"]
