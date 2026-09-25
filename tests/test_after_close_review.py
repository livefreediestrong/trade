import copy
import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import after_close_review as night
import llm_trader
import moss_policy

NOW = datetime(2026, 9, 25, 21, tzinfo=timezone.utc)
DAY = "2026-09-25"


def event(day=DAY, ident="one", net=10):
    start = datetime.fromisoformat(day+"T15:00:00+00:00")
    end = start+timedelta(minutes=10)
    quote = lambda t: dict(price=100, market_time=t.isoformat(), fresh=True, source="recorded")
    return dict(id=ident, ts=start.isoformat(), outcome_ts=end.isoformat(), outcome_market_time=end.isoformat(),
                quote=quote(start), outcome_quote=quote(end), ticker="SPY", source="moss_paper", moss_policy_version=moss_policy.VERSION,
                moss_quality={"ok": True}, horizon_min=10, intended_side="buy", llm_model="real", prompt_version="v1", input_hash=ident,
                scoring_version="horizon-net-v2", outcome_status="scored", outcome_executable_move_bps=net, mid=100, outcome_mid=100.1, confidence=.7)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Unexpected network"))
    cfg = {"llm_enabled": True, "watchlist": ["SPY"], "live_agent": {"policy": {"model_budget_usd": 2}}, "mode": "auto_live"}
    def load(path, default):
        return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)
    def save(path, raw):
        assert path.name == "after_close_reviews.json", "No trading/config writes permitted"
        path.write_text(json.dumps(raw))
    desk = NS(DATA_DIR=tmp_path, DECISIONS_PATH=tmp_path/"decisions.json", _CORRUPT_PATHS=set(), _lock=threading.RLock(),
              _load_json=load, _save_json=save, load_config=lambda: copy.deepcopy(cfg))
    journal = {"executions": [], "identity": {"account_id": "SECRET-ACCOUNT", "paper_mode": False}}
    companion = NS(stop=threading.Event(), load=lambda: {"settings": {"enabled": True, "use_model": True}}, events=lambda: [event()],
                   trades=NS(path=tmp_path/"broker_execution_journal.json", load=lambda: copy.deepcopy(journal)),
                   news=NS(busy=False, refresh=lambda: None, snapshot=lambda **k: {"sources": [], "items": []}))
    monkeypatch.setattr(night.moss_paper, "history", lambda _: [event("2026-09-24", "prior", -20)])
    monkeypatch.setattr(night, "utcnow", lambda: NOW)
    bars = pd.DataFrame({"Close": [100, 102, 101, 999]}, index=pd.to_datetime(["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28"]))
    monkeypatch.setattr(night.data_sources, "get_daily_with_fallback", lambda *a, **k: (bars, "recorded_daily"))
    return night.AfterCloseReview(desk, companion), cfg, journal


def test_calendar_early_close_holiday_dst_and_catchup():
    # Thanksgiving: latest due is Wednesday; next is Friday's 13:30 ET early close.
    due, upcoming = night.schedule(datetime(2026, 11, 26, 22, tzinfo=timezone.utc))
    assert due.date().isoformat() == "2026-11-25"
    assert upcoming.isoformat() == "2026-11-27T13:30:00-05:00"
    assert night.schedule(datetime(2026, 9, 25, 20, 29, tzinfo=timezone.utc))[0].date().isoformat() == "2026-09-24"
    assert night.schedule(datetime(2026, 9, 25, 20, 30, tzinfo=timezone.utc))[0].date().isoformat() == DAY
    assert night.schedule(datetime(2026, 9, 27, 20, tzinfo=timezone.utc))[0].date().isoformat() == DAY


def test_actual_qualification_baseline_no_future_prices_or_account_leak(service):
    s, cfg, journal = service
    s.companion.events = lambda: [event(), event("2026-09-28", "future", 999)]
    def fill(account, mode):
        return {"account_id": account, "paper_mode": mode, "verified": True, "ts": DAY+"T15:00:00+00:00", "commission": 1,
                "broker_realized_pnl": 2, "currency": "USD", "order_id": 1}
    journal["executions"] = [fill("SECRET-ACCOUNT", False), fill("other", False), fill("SECRET-ACCOUNT", True)]
    before = copy.deepcopy(cfg)
    e = s.collect(DAY, NOW)
    assert e["outcomes"]["today"]["outcomes"] == 1
    assert e["outcomes"]["today"]["mean_net_bps"] == 10
    assert e["outcomes"]["prior_20_observed_sessions"]["mean_net_bps"] == -20
    assert e["market"][0]["close"] == 101 and e["market"][0]["as_of"] == DAY
    assert e["executions"]["live"]["executions"] == 1
    assert e["executions"]["broker_paper"]["executions"] == 0
    assert "SECRET-ACCOUNT" not in json.dumps(e)
    assert "future" not in e["outcomes"]["qualified_event_ids"]
    assert e["outcomes"]["verdict"]["state"] == "collecting"
    assert cfg == before


def test_current_invalid_revision_supersedes_archived_valid_sample(service, monkeypatch):
    s, _, _ = service
    monkeypatch.setattr(night.moss_paper, "history", lambda _: [event()])
    s.companion.events = lambda: [dict(event(), error="corrected invalid data"), event()]
    assert s.collect(DAY, NOW)["outcomes"]["today"]["outcomes"] == 0


def test_news_future_stale_and_postclose_attribution(service):
    s, _, _ = service
    def headline(ident, stamp, fresh=True):
        return {"id": ident, "published_ts": stamp, "title": ident, "fresh": fresh}
    s.companion.news.snapshot = lambda **k: {"items": [headline("old", NOW.timestamp()-7200), headline("later", NOW.timestamp()-60),
                                                       headline("future", NOW.timestamp()+60), headline("stale", NOW.timestamp()-10, False)]}
    rows = s.collect(DAY, NOW)["headlines"]
    assert {r["id"] for r in rows} == {"news_old", "news_later"}
    assert next(r for r in rows if r["id"] == "news_later")["after_close"]
    assert not next(r for r in rows if r["id"] == "news_old")["after_close"]


def test_restart_resumes_without_duplicate_paid_request(service, monkeypatch):
    s, _, _ = service
    e = s.collect(DAY, NOW)
    s.save({"reports": {DAY: {"day": DAY, "evidence": e, "status": "running", "models": {"changing_woman": {"status": "attempted"}}}}})
    called = []
    monkeypatch.setattr(s, "_review", lambda day, role, evidence, earlier: called.append(role) or {"status": "complete", "result": {}})
    s._work(DAY, NOW)
    row = s.load()["reports"][DAY]
    assert called == ["fox"] and row["status"] == "partial"
    assert row["models"]["changing_woman"]["status"] == "interrupted"
    assert not night.AfterCloseReview(s.desk, s.companion).tick(NOW+timedelta(hours=1))


def test_real_worker_persists_before_model_failure_and_does_not_trade(service, monkeypatch):
    s, _, _ = service
    class ImmediateThread:
        def __init__(self, target, args, **kwargs): self.target, self.args = target, args
        def start(self): self.target(*self.args)
    monkeypatch.setattr(night.threading, "Thread", ImmediateThread)
    calls = []
    def failing(day, role, evidence, earlier):
        saved = s.load()["reports"][day]
        assert saved["evidence"]["hash"] == evidence["hash"]
        assert saved["models"][role]["status"] == "attempted"
        calls.append(role)
        raise RuntimeError("API failure")
    monkeypatch.setattr(s, "_review", failing)
    assert s.tick(NOW)
    assert calls == list(night.ROLES) and not s.busy
    row = s.load()["reports"][DAY]
    assert row["status"] == "partial" and row["evidence"]
    assert not s.tick(NOW+timedelta(hours=1))


def test_model_citations_and_prospective_test_required():
    good = {"summary": "Evidence is limited.", "findings": [{"text": "Limited sample", "evidence_ids": ["outcomes"]}], "hypotheses": [], "uncertainties": []}
    assert night.validate_narrative(json.dumps(good), {"outcomes"}) == good
    good["findings"][0]["evidence_ids"] = ["invented"]
    with pytest.raises(ValueError, match="citation"): night.validate_narrative(json.dumps(good), {"outcomes"})
    good["findings"] = []
    good["hypotheses"] = [{"text": "Try something", "evidence_ids": ["outcomes"]}]
    with pytest.raises(ValueError): night.validate_narrative(json.dumps(good), {"outcomes"})


def test_gemini_sanitized_payload_budget_disable_and_output_limit(service, monkeypatch):
    s, cfg, _ = service
    evidence = s.collect(DAY, NOW)
    usage = {"model_usd": 0, "scopes": {}}
    monkeypatch.setattr(llm_trader, "model_cost_today", lambda: usage)
    monkeypatch.setattr(llm_trader, "load_llm_config", lambda: {"model": "gemini-3.6-flash", "api_key": "SECRET"})
    called = []
    def generate(prompt, **kw):
        assert "SECRET" not in prompt and "account_id" not in prompt and "qualified_event_ids" not in prompt
        assert kw["max_output_tokens"] == 4096
        assert llm_trader.current_cost_scope() == "after_close"
        called.append(prompt)
        return json.dumps({"summary": "Limited evidence", "findings": [], "hypotheses": [], "uncertainties": []})
    monkeypatch.setattr(llm_trader, "gemini_generate", generate)
    assert s._review(DAY, "changing_woman", evidence, None)["status"] == "complete"
    usage["model_usd"] = 2
    assert s._review(DAY, "fox", evidence, None)["status"] == "budget_held"
    usage["model_usd"] = float("nan")
    assert s._review(DAY, "fox", evidence, None)["status"] == "budget_unavailable"
    cfg["llm_enabled"] = False
    assert s._review(DAY, "fox", evidence, None)["status"] == "disabled"
    assert len(called) == 1


def test_corrupt_store_never_overwritten(service):
    s, _, _ = service
    s.path.write_text('{"reports": []}')
    original = s.path.read_bytes()
    with pytest.raises(ValueError): s.tick(NOW)
    assert s.path.read_bytes() == original
    assert "recovery" in s.status(NOW)["error"]


def test_retained_hypotheses_feed_next_review_as_unproven(service):
    s, _, _ = service
    s.save({"reports": {"2026-09-24": {"status": "complete", "evidence": {"outcomes": {"verdict": {"state": "no_edge"}}},
                                     "models": {"fox": {"result": {"hypotheses": [{"text": "Unproven idea", "test": "Paper only"}]}}}}}})
    e = s.collect(DAY, NOW)
    assert e["memories"][0]["verdict"]["state"] == "no_edge"
    assert "Unproven" in e["memories"][0]["label"]
    assert e["memories"][0]["hypotheses"][0]["text"] == "Unproven idea"


def test_transport_applies_output_bound_before_request(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_trader, "USAGE_PATH", tmp_path/"usage.json")
    seen = []
    def post(url, **kwargs):
        seen.append(kwargs["json"]["generationConfig"])
        return NS(status_code=200, json=lambda: {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]})
    monkeypatch.setattr(llm_trader.requests, "post", post)
    llm_trader.gemini_generate("bounded", json_mode=True, max_output_tokens=4096, cfg={"api_key": "fixture", "model": "gemini-fixture"})
    assert seen[0]["maxOutputTokens"] == 4096
    with pytest.raises(ValueError):
        llm_trader.gemini_generate("invalid", max_output_tokens=True, cfg={"api_key": "fixture"})
    assert len(seen) == 1
