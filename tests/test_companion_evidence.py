import copy
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import narrative_checks as checks
import companion_evidence as context
import lessons


def report():
    return {"evidence": {"outcomes": {"today": {"outcomes": 3, "mean_net_bps": -2}, "verdict": {"title": "Collecting evidence"}}},
            "models": {"changing_woman": {"result": {"summary": "No live account returns, confirmed by zero executions.",
               "findings": [{"text": "Zero broker executions confirms no realized P&L.", "evidence_ids": ["executions"]},
                            {"text": "3 qualified observations averaged -2 bps", "evidence_ids": ["outcomes"]}],
               "hypotheses": [{"text": "Collect 30 qualified observations", "evidence_ids": ["outcomes"]}]}}}}


def test_historical_report_known_error_hidden_original_unchanged():
    raw = report()
    before = copy.deepcopy(raw)
    row = checks.checked_report(raw)
    model = row["models"]["changing_woman"]
    assert raw == before and model["result"] == before["models"]["changing_woman"]["result"]
    assert len(model["checks"]["flagged"]) == 2
    assert len(model["checked_result"]["findings"]) == 1
    assert len(model["checked_result"]["hypotheses"]) == 1
    assert "3 qualified paper" in model["checked_result"]["summary"]
    assert "-2.00 bps" in model["checked_result"]["summary"]
    assert checks.concerns("40 qualified paper observations", raw["evidence"])
    assert not checks.concerns("These executions cannot establish account returns.", raw["evidence"])


def test_recall_marks_changed_withdrawn_missing_without_replacing_original(tmp_path):
    path = tmp_path / "lessons.json"
    lessons._write(path, [{"id": "a", "revision": 2, "outcome_status": "scored"},
                         {"id": "b", "revision": 2, "outcome_status": "invalid"}])
    signal = {"learning_context": {"evidence_rows": [{"id": "a", "revision": 1}, {"id": "b"}, {"id": "c"}]}}
    got = context.recall_view(NS(LESSONS_PATH=path), signal)
    assert [r["current_status"] for r in got["evidence_rows"]] == ["corrected since recall", "withdrawn", "unavailable"]
    assert got["evidence_rows"][0]["revision"] == 1


def test_closed_market_does_not_hide_missing_pnl_and_poll_deduplicates(monkeypatch):
    monkeypatch.setattr(context, "broker_view", lambda *_: {"risk_ready": False, "error": "Daily P&L unavailable"})
    args = (NS(), {}, {"market_open": False, "enabled": True, "session_active": True},
            {"state": "waiting", "headline": "Waiting for market"}, {"fresh_sources": 0}, None, None)
    a = context.presentation(*args, datetime(2026,9,25,22,0,tzinfo=timezone.utc))
    b = context.presentation(*args, datetime(2026,9,25,22,1,tzinfo=timezone.utc))
    assert a["event_id"] == b["event_id"]
    assert "Daily P&L unavailable" in a["quality"]["execution"]["detail"]
    assert "closed" in a["quality"]["execution"]["detail"]
    assert a["quality"]["data"]["state"] == "unavailable"


def test_model_prompt_bounds_examples_without_destroying_full_receipt():
    import json
    import llm_trader
    analysis = {"learning_context": {"sample_count": 200, "evidence_rows": [{"id": str(i)} for i in range(200)]}}
    blob = json.loads(llm_trader._analysis_context_blob(analysis))
    assert len(blob["learning_context"]["evidence_rows"]) == 12
    assert blob["learning_context"]["retained_receipt_count"] == 200
    assert len(analysis["learning_context"]["evidence_rows"]) == 200
