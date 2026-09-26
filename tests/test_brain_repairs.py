"""Research regressions, isolated from market feeds, credentials and broker orders."""
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app as desk
import claude_brain
import lessons
import llm_trader as llm
import news_stream
import paper_loop
import session_track


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL", "LESSONS"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    monkeypatch.setattr(desk, "_decision_ring", paper_loop.DecisionRing(tmp_path / "decisions.json"))
    monkeypatch.setattr(desk, "_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: pytest.fail("Unexpected model network call"))
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *a, **k: pytest.fail("No live orders in brain tests"))
    desk._CORRUPT_PATHS.clear()


def analysis():
    return {"ticker": "TEST", "price": 100., "verdict": "PASS", "entry_quality": {"label": "fair"},
            "volume": {"rel_vol": 2}, "quote": {"price": 100., "fresh": True, "source": "fixture",
            "market_time": datetime.now(timezone.utc).isoformat()}}


def thesis(**updates):
    out = dict(side="buy", horizon="higher", confidence=.9, thesis="Momentum supports higher prices.",
               risks=[], brain_mode="gemini", llm_model="gemini-fixture", error=None)
    out.update(updates)
    return out


@pytest.mark.parametrize("field", ["brain_mode", "model"])
@pytest.mark.parametrize("key", ["", "fixture-key-must-not-unlock"])
def test_jev_selection_rejected_without_config_or_journal_changes(monkeypatch, field, key):
    monkeypatch.setattr(llm, "typesafe_api_key", lambda: key)
    cfg = copy.deepcopy(desk.DEFAULT_CONFIG)
    cfg.update(brain_mode="gemini", llm_enabled=True)
    desk.save_config(cfg)
    before = desk.CONFIG_PATH.read_bytes()
    response = desk.app.test_client().post("/api/config", base_url="http://127.0.0.1:5056",
        json={field: " JEV ", "llm_enabled": False, "watchlist": ["CHANGED"]})
    assert response.status_code == 409
    assert response.get_json()["code"] == "brain_unavailable"
    assert "repaired and verified" in response.get_json()["error"]
    assert desk.CONFIG_PATH.read_bytes() == before
    assert not desk.JOURNAL_PATH.exists()


def test_saved_jev_holds_without_network_or_provider_fallback(monkeypatch):
    monkeypatch.setattr(llm, "typesafe_api_key", lambda: "fixture-key-must-not-unlock")
    monkeypatch.setattr(llm, "jev_trade_thesis", lambda *a, **kw: pytest.fail("JEV is locked"))
    monkeypatch.setattr(llm, "trade_thesis_from_analysis", lambda *a, **kw: pytest.fail("No silent Gemini switch"))
    out = llm.decide_trade_thesis(analysis(), {"brain_mode": "jev"})
    assert out["side"] == "hold" and out["confidence"] == 0 and out["abstain"]
    assert out["brain_mode"] == "jev" and "temporarily unavailable" in out["error"]
    status = llm.status_public_extended({"brain_mode": "jev"})
    assert status["configured"] is False and status["selection_error"]
    assert "fixture-key" not in json.dumps(status)


def test_other_brains_remain_selectable():
    for mode in ("gemini", "mock", "claude"):
        assert llm.brain_selection_error(mode) is None


def test_claude_dispatch_and_status_are_truthful(monkeypatch):
    calls = []
    monkeypatch.setattr(claude_brain, "decide", lambda a, **kw: calls.append(kw) or thesis(brain_mode="claude"))
    monkeypatch.setattr(claude_brain, "is_configured", lambda: False)
    monkeypatch.setattr(llm, "trade_thesis_from_analysis", lambda *a, **k: pytest.fail("Claude must not call Gemini"))
    assert llm.decide_trade_thesis(analysis(), {"brain_mode": "claude", "decision_horizon_min": 10})["brain_mode"] == "claude"
    assert calls == [{"horizon_min": 10}]
    status = llm.status_public_extended({"brain_mode": "claude"})
    assert status["provider"] == "claude" and status["configured"] is False


@pytest.mark.parametrize("changes", [{"side": "not-a-side"}, {"thesis": ""}, {"risks": "none"},
                                      {"confidence": float("nan")}, {"confidence": True}, {"confidence": 90}])
def test_malformed_thesis_holds(changes):
    data = thesis(**changes)
    out = llm._normalize_thesis(data, json.dumps(data), "fixture")
    assert out["error"] and out["side"] == "flat" and out["confidence"] == 0


def test_bad_audits_stay_unknown(monkeypatch):
    scores = llm._parse_advisory_scores({k: float("nan") for k in ("thesis_coherent", "regime_ok", "risk_ok", "tradeable_now")})
    assert llm.policy_label_from_advisory(scores) == "UNKNOWN"
    monkeypatch.setenv("ADVISORY_SOFT_SIZE", "1")
    assert llm._advisory_size_mult(scores, "UNKNOWN") == 0
    monkeypatch.setattr(llm, "shadow_auditor_enabled", lambda: True)
    monkeypatch.setenv("SHADOW_AUDITOR", "gemini")
    monkeypatch.setattr(llm, "load_llm_config", lambda: {"configured": True, "api_key": "fixture"})
    monkeypatch.setattr(llm, "gemini_generate", lambda *a, **k: "{}")
    assert llm.shadow_audit_decision("buy", "higher")["coherent"] is None


@pytest.mark.parametrize("source", ["scanner", "evaluation", "thesis_api", "loop"])
def test_shared_research_records_inputs_and_outcome_for_all_paths(source, monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "decide_trade_thesis", lambda a, *args, **kw: seen.append(copy.deepcopy(a)) or thesis())
    monkeypatch.setattr(llm, "shadow_audit_decision", lambda *a, **k: {"coherent": True, "method": "fixture"})
    monkeypatch.setattr(llm, "run_advisory_panel", lambda *a, **k: {"size_mult": 1, "method": "fixture"})
    cfg = dict(desk.load_config(), mode="live_manual", brain_mode="gemini", llm_on_scan=True, paper_research_enabled=True)
    original = analysis()
    result = desk._research_thesis(original, cfg, source=source)
    row = desk._decision_ring.latest(1)[0]
    assert row["id"] == result["decision_record_id"] and row["outcome_pending"]
    assert row["source"] == source and row["shadow"]["method"] == row["advisory"]["method"] == "fixture"
    assert row["input_hash"] and row["requested_model"] and row["prompt_version"]
    assert "research_evidence" in seen[0] and row["execution_attempted"] is False
    original["quote"]["price"] = 500
    assert row["inputs"]["quote"]["price"] == 100
    assert not desk.load_ledger().get("broker_fills")


def test_scanner_cannot_override_soft_kill_with_direction(monkeypatch):
    monkeypatch.setattr(llm, "decide_trade_thesis", lambda *a, **k: thesis())
    monkeypatch.setattr(llm, "shadow_audit_decision", lambda *a, **k: {"coherent": True})
    monkeypatch.setattr(llm, "run_advisory_panel", lambda *a, **k: {"size_mult": 0, "policy_label": "KILL"})
    monkeypatch.setenv("ADVISORY_SOFT_SIZE", "1")
    out = desk._enrich_signal_with_llm({"ticker": "TEST", "side": "buy", "suggested_shares": 10, "signal_price": 100}, analysis(), {"brain_mode": "gemini"})
    assert out["side"] == "hold" and out["abstain"] and out["suggested_shares"] == 0


def test_paper_loop_zero_multiplier_never_invokes_execution(tmp_path, monkeypatch):
    import macro_calendar
    cfg = dict(session_active=True, loop_enabled=True, mode="auto_paper", rth_only=False,
               watchlist=["TEST"], watchlist_focus="all", loop_interval_sec=30)
    deps = dict(load_config=lambda: cfg, append_journal=lambda *a, **k: None,
                load_ledger=lambda: {"equity": 100000, "positions": []},
                daily_target_progress=lambda *a: {"pnl": 0, "target_hit": False}, get_preset=lambda *a: {"max_daily_loss_pct": 2},
                execute_loop_decision=lambda **k: pytest.fail("Kill must never reach execution"),
                analyze_ticker=lambda t: analysis(), trade_thesis=lambda *a: thesis(),
                run_advisory_panel=lambda *a, **k: {"policy_label": "KILL", "size_mult": 0}, advisory_soft_size_enabled=lambda: True)
    loop = paper_loop.PaperLoop(decisions=paper_loop.DecisionRing(tmp_path / "loop.json"), get_deps=lambda: deps)
    loop._running_flag = True
    monkeypatch.setattr(macro_calendar, "risk_adjustment", lambda *a, **k: {})
    loop._tick()
    assert any(row.get("advisory", {}).get("policy_label") == "KILL" and not row.get("filled")
               for row in loop.decisions.latest(5) if row.get("advisory"))


def pending(when, **updates):
    event = dict(id="pending", event="decision", ticker="TEST", intended_side="buy", mid_at_decision=100,
                 outcome_intended_side="buy", outcome_pending=True, horizon_min=20,
                 outcome_due_ts=when.isoformat(), scoring_version="horizon-net-v2", slip_bps=10, fee_bps=1)
    event.update(updates)
    return event


def test_overdue_horizon_never_uses_current_price(monkeypatch):
    desk._decision_ring.append(pending(datetime.now(timezone.utc) - timedelta(hours=2)))
    monkeypatch.setattr(desk, "fetch_last_price", lambda *a: pytest.fail("Too late to sample this horizon"))
    row = desk.check_decision_outcomes()[0]
    assert row["outcome_status"] == "missed_horizon_window" and not row.get("outcome")
    assert not lessons.recent(desk.LESSONS_PATH)


def test_old_pending_survives_ring_and_due_selection(tmp_path):
    ring = paper_loop.DecisionRing(tmp_path / "small.json", max_events=4)
    now = datetime.now(timezone.utc)
    ring.append(pending(now - timedelta(seconds=2)))
    for i in range(50):
        ring.append({"event": "skip", "id": f"skip-{i}"})
    reloaded = paper_loop.DecisionRing(ring.path, max_events=4)
    assert reloaded.pending_due(now, 1)[0]["id"] == "pending"


def test_same_window_and_costs_used_for_main_shadow_and_lessons(monkeypatch):
    now = datetime.now(timezone.utc)
    desk._decision_ring.append(pending(now - timedelta(seconds=15), shadow_claude={"side": "buy", "model": "claude-fixture"}))
    desk._QUOTE_SNAPSHOTS["TEST"] = {"fresh": True, "market_time": now.isoformat()}
    monkeypatch.setattr(desk, "fetch_last_price", lambda t: 100.13)
    row = desk.check_decision_outcomes()[0]
    assert row["outcome"] == row["shadow_claude_outcome"] == "helped"
    assert row["net_outcome"] == row["shadow_claude_net_outcome"] == "hurt"
    assert row["outcome_executable_move_bps"] == row["shadow_claude_executable_move_bps"] == -9
    assert lessons.recent(desk.LESSONS_PATH)[0]["net_outcome"] == "hurt"


def test_quote_before_due_is_not_graded(monkeypatch):
    now = datetime.now(timezone.utc)
    desk._decision_ring.append(pending(now - timedelta(seconds=15)))
    desk._QUOTE_SNAPSHOTS["TEST"] = {"fresh": True, "market_time": (now-timedelta(minutes=1)).isoformat()}
    monkeypatch.setattr(desk, "fetch_last_price", lambda t: 100.13)
    assert desk.check_decision_outcomes() == []


def test_lessons_scope_and_net_metric_exclude_other_models_and_mock():
    scope = {"brain": "gemini", "requested_model": "fixture", "llm_model": "fixture", "prompt_version": "v2", "horizon_min": 20, "scoring_version": "horizon-net-v2"}
    for i, changes in enumerate([{}, {"requested_model": "other"}, {"llm_model": "unexpected-version"}, {"horizon_min": 60}, {"mock": True}]):
        ev = dict(id=str(i), ticker="TEST", intended_side="buy", verdict="PASS", lateness_label="fair",
                  brain_mode="gemini", outcome="helped", net_outcome="hurt", outcome_status="scored", **{k:v for k,v in scope.items() if k != "brain"})
        ev.update(changes)
        lessons.record_outcome(desk.LESSONS_PATH, ev)
    rec = lessons.track_record(desk.LESSONS_PATH, ticker="TEST", verdict="PASS", lateness="fair", scope=scope)
    assert rec["setup_count"] == 1 and rec["setup_results"]["buy"]["hurt"] == 1


def test_cached_news_is_dated_and_never_fetches(monkeypatch):
    now = datetime.now(timezone.utc).timestamp()
    rows = [{"title": "current", "published_ts": now-30, "link": "https://example.com/news", "source": "fixture"},
            {"title": "future", "published_ts": now+1, "link": "https://example.com/future"},
            {"title": "missing", "link": "https://example.com/missing"}]
    monkeypatch.setattr(news_stream, "_cache", {"at": now, "payload": {"by_ticker": {"TEST": rows}}})
    evidence = news_stream.cached_evidence("TEST", now=now)
    blob = json.loads(llm._analysis_context_blob({"learning_context": {"sample_count": 5}, "research_evidence": evidence}))
    assert len(blob["research_evidence"]["items"]) == 1 and blob["learning_context"]["sample_count"] == 5
    assert news_stream.cached_evidence("TEST", now=now+121)["status"] == "unavailable"


def test_budget_and_cost_survive_new_instances_and_corruption():
    llm._GeminiBudget(daily=1).acquire()
    assert llm._GeminiBudget(daily=1).status()["daily_used"] == 1
    with pytest.raises(RuntimeError, match="daily_budget"):
        llm._GeminiBudget(daily=1).acquire()
    llm.record_model_cost(.25, meta={"brain": "fixture"})
    assert json.loads(llm.USAGE_PATH.read_text())["days"][llm._usage_day()]["model_usd"] == .25
    assert llm.model_cost_today()["model_usd"] == .25
    llm.USAGE_PATH.write_text("broken")
    with pytest.raises(RuntimeError, match="usage_unavailable"):
        llm._GeminiBudget().acquire()
    assert llm.model_cost_today()["model_usd"] is None


def test_archive_preserves_original_prediction_when_outcome_is_patched():
    row = desk._decision_ring.append(pending(datetime.now(timezone.utc), inputs={"price": 100}, input_hash="fixture"))
    row["inputs"]["price"] = 200
    desk._decision_ring.patch(row["id"], {"outcome": "hurt", "outcome_pending": False})
    archived = list((desk._decision_ring.path.parent / "research_history").glob("*.jsonl"))
    original = json.loads(archived[0].read_text().splitlines()[0])
    assert original["inputs"]["price"] == 100 and original["outcome_pending"]
    assert "outcome" not in original


@pytest.mark.parametrize("changes", [{"execution_block": "shadow_not_verified"}, {"advisory_size_mult": 0},
                                      {"advisory_size_mult": float("nan")}, {"error": "malformed"}])
def test_execution_adapter_independently_rejects_invalid_brain_decisions(changes, monkeypatch):
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: pytest.fail("Blocked decisions cannot fill"))
    out = desk.execute_loop_decision(analysis=analysis(), thesis=thesis(**changes), cfg=desk.load_config(), mid=100.)
    assert not out["ok"] and out["abstain"]


def test_provider_failure_is_not_replaced_by_optimistic_local_audit(monkeypatch):
    monkeypatch.setattr(llm, "load_llm_config", lambda: {"api_key": "fixture", "configured": True})
    monkeypatch.setattr(llm, "gemini_generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setenv("ADVISORY_SOFT_SIZE", "1")
    out = llm.gemini_advisory_panel("buy", "higher")
    assert out["method"] == "gemini" and out["policy_label"] == "UNKNOWN" and out["size_mult"] == 0


def test_gemini_schema_and_incomplete_response(monkeypatch):
    bodies = []
    response = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": json.dumps(thesis())}]}}],
                "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 200}}
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: bodies.append(k["json"]) or SimpleNamespace(status_code=200, json=lambda: response))
    with pytest.raises(RuntimeError, match="incomplete"):
        llm.gemini_generate("fixture", cfg={"api_key": "fixture"}, json_mode=True, response_schema=llm.THESIS_SCHEMA)
    assert bodies[0]["generationConfig"]["responseJsonSchema"] == llm.THESIS_SCHEMA
    assert llm.model_cost_today()["calls"] == 1
    assert llm.estimate_gemini_cost(input_tokens=1000, output_tokens=200, model="gemini-3.6-flash") == pytest.approx(.0015)
