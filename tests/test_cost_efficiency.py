"""Model spend: no paid call for ideas that can never trade, lean Gemini requests."""
from __future__ import annotations

import json

import pytest

import app as desk
import llm_trader as llm


def _scan(monkeypatch, verdict, **kw):
    import screener_logic

    cfg = desk.load_config()
    cfg.update(watchlist=["AAPL"], market_regime_gate_enabled=False, min_net_reward_risk=0)
    monkeypatch.setattr(screener_logic, "analyze_ticker", lambda _: {
        "ticker": "AAPL", "price": 100.0, "verdict": verdict, "verdict_text": "thin volume",
        "entry_quality": {"label": "early"}, "intraday": {"atr_usd": 2.0, "spread_atr": 0.01},
        "volume": {"rel_vol": 2.0, "session_is_today": True}, "checks": {}, "sources": []})
    calls = []
    monkeypatch.setattr(desk, "_enrich_signal_with_llm", lambda sig, analysis, cfg: calls.append(sig) or sig)
    return desk.generate_scan_signal(cfg, ticker="AAPL", **kw), calls


@pytest.mark.parametrize("verdict", ["WATCH", "AVOID"])
def test_pass_only_scan_spends_nothing_on_untradeable_ideas(monkeypatch, verdict):
    notes = {}
    sig, calls = _scan(monkeypatch, verdict, pass_only=True, notes=notes)
    assert sig is None and calls == []
    assert notes == {"ticker": "AAPL", "verdict": verdict, "text": "thin volume"}
    # The research desk still studies WATCH/AVOID ideas when it asks for them.
    sig, calls = _scan(monkeypatch, verdict)
    assert sig["verdict"] == verdict and len(calls) == 1


def test_pass_only_scan_still_researches_pass(monkeypatch):
    sig, calls = _scan(monkeypatch, "PASS", pass_only=True, notes={})
    assert sig["verdict"] == "PASS" and len(calls) == 1


@pytest.mark.parametrize("mode", ["auto_live", "live_manual"])
def test_live_modes_route_only_avoid_to_the_free_brain(monkeypatch, mode):
    monkeypatch.setenv("BRAIN_ROUTER", "1")
    cfg = {"mode": mode, "brain_mode": "gemini"}
    assert llm.should_route_cheap({"verdict": "AVOID"}, cfg) == (True, "verdict_avoid")
    junk = {"verdict": "PASS", "volume": {"rel_vol": 0.5}, "entry_quality": {"pos_in_30d": 99}}
    assert llm.should_route_cheap(junk, cfg) == (False, "live_mode_no_mock")
    assert llm.should_route_cheap(junk, dict(cfg, mode="manual"))[0] is True


def test_observe_only_audit_uses_the_free_check(monkeypatch):
    monkeypatch.delenv("SHADOW_AUDITOR", raising=False)
    monkeypatch.delenv("SHADOW_GATE", raising=False)
    monkeypatch.setattr(llm, "load_llm_config", lambda: {"configured": True, "api_key": "fixture"})
    monkeypatch.setattr(llm, "gemini_generate", lambda *a, **k: pytest.fail("paid audit call"))
    assert llm.shadow_audit_decision("buy", "Volume is strong, so higher.")["method"] != "gemini"
    monkeypatch.setenv("SHADOW_GATE", "1")
    monkeypatch.setattr(llm, "gemini_generate", lambda *a, **k: '{"pass": true, "reason": "ok"}')
    assert llm.shadow_audit_decision("buy", "higher")["method"] == "gemini"


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._payload, self.text = status, payload, json.dumps(payload)

    def json(self):
        return self._payload


OK = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}],
      "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


@pytest.fixture
def gemini(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path / "usage.json")
    monkeypatch.delenv("GEMINI_THINKING_LEVEL", raising=False)
    llm._NO_THINKING_LEVEL.clear()
    bodies = []
    replies = []

    def post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        return replies.pop(0) if replies else _Resp(200, OK)

    monkeypatch.setattr(llm.requests, "post", post)
    yield bodies, replies
    llm._NO_THINKING_LEVEL.clear()


def test_json_calls_ask_gemini_3_for_low_thinking(gemini):
    bodies, _ = gemini
    llm.gemini_generate("q", json_mode=True, cfg={"api_key": "k", "model": "gemini-3.6-flash"})
    assert bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    llm.gemini_generate("q", json_mode=True, cfg={"api_key": "k", "model": "gemini-2.5-flash"})
    assert "thinkingConfig" not in bodies[1]["generationConfig"]


def test_thinking_level_refused_once_then_dropped(gemini):
    bodies, replies = gemini
    replies.append(_Resp(400, {"error": {"message": "thinking_level is not supported for this model"}}))
    assert llm.gemini_generate("q", json_mode=True, cfg={"api_key": "k", "model": "gemini-3-x"}) == "{}"
    assert "thinkingConfig" in bodies[0]["generationConfig"]
    assert "thinkingConfig" not in bodies[1]["generationConfig"]
    llm.gemini_generate("q", json_mode=True, cfg={"api_key": "k", "model": "gemini-3-x"})
    assert len(bodies) == 3 and "thinkingConfig" not in bodies[2]["generationConfig"]


def test_thinking_level_setting(monkeypatch):
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "default")
    assert llm.gemini_thinking_level("gemini-3.6-flash") is None
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "HIGH")
    assert llm.gemini_thinking_level("gemini-3.6-flash") == "high"
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "turbo")
    assert llm.gemini_thinking_level("gemini-3.6-flash") is None


def test_prompt_facts_are_compact_json():
    blob = llm._analysis_context_blob({"ticker": "AAPL", "volume": {"rel_vol": 2.0}, "ignored": 1})
    assert json.loads(blob) == {"ticker": "AAPL", "volume": {"rel_vol": 2.0}}
    assert "\n" not in blob and ": " not in blob


def test_sector_history_and_earnings_are_shared_between_screens(monkeypatch):
    import pandas as pd
    import screener_logic as sl

    fetched = []
    frame = pd.DataFrame({"Close": [1.0, 2.0]})
    monkeypatch.setattr(sl, "_sector_daily_uncached", lambda etf: fetched.append(etf) or frame)
    assert sl._sector_daily("XLK").equals(frame) and sl._sector_daily("XLK").equals(frame)
    assert fetched == ["XLK"]
    monkeypatch.setattr(sl, "_sector_daily_uncached", lambda etf: fetched.append(etf) or None)
    sl._sector_daily("XLE"); sl._sector_daily("XLE")
    assert fetched == ["XLK", "XLE", "XLE"]  # a failed fetch is not cached

    from datetime import datetime, timedelta
    soon = (datetime.utcnow().date() + timedelta(days=3)).isoformat()
    monkeypatch.setattr(sl, "_fetch_earnings_uncached",
                        lambda t, y, i=None: fetched.append(t) or ({"date": soon, "days_away": 3, "is_soon": True}, "Finnhub"))
    first = sl._fetch_earnings("AAPL", None)
    assert sl._fetch_earnings("AAPL", None) == first and fetched.count("AAPL") == 1
    past = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    sl._EARNINGS_CACHE["AAPL"] = (sl._EARNINGS_CACHE["AAPL"][0], {"date": past}, "Finnhub")
    sl._fetch_earnings("AAPL", None)
    assert fetched.count("AAPL") == 2  # a report date that has passed is looked up again
