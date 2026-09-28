import copy
import json
import socket
import threading
from types import SimpleNamespace as NS

import pytest
import companion_chat as chat
import llm_trader


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Unexpected network"))
    cfg = {"mode": "manual", "llm_enabled": True, "broker_identity": {"account_id": "SECRET-ACCOUNT"},
           "live_agent": {"policy": {"model_budget_usd": 2}}}
    def load(path, default):
        return json.loads(path.read_text()) if path.exists() else copy.deepcopy(default)
    def save(path, raw):
        assert path.name == "companion_conversations.json", "No config or broker writes"
        path.write_text(json.dumps(raw))
    desk = NS(DATA_DIR=tmp_path, _CORRUPT_PATHS=set(), _lock=threading.RLock(), _load_json=load,
              _save_json=save, load_config=lambda: copy.deepcopy(cfg), load_signals=lambda: [],
              _llm_cfg_for_calls=lambda _: {"model": "fixture", "api_key": "SECRET-KEY"})
    companion = NS(load=lambda: {"settings": {"use_model": True}},
                   after_close=NS(status=lambda: {"latest": None}), weekly=NS(status=lambda: {"latest": None}))
    monkeypatch.setattr(llm_trader, "load_llm_config", lambda: {"model": "fixture", "api_key": "SECRET-KEY"})
    jobs = []
    class Deferred:
        def __init__(self, target, args, **kw): self.job = lambda: target(*args)
        def start(self): jobs.append(self.job)
    monkeypatch.setattr(chat.threading, "Thread", Deferred)
    return chat.Chat(desk, companion), cfg, jobs


def body(text="Explain learning", **kw):
    return dict(message=text, request_id=chat.uuid.uuid4().hex, role="both", workspace="live", page="research", **kw)


def test_local_reply_restart_history_scope_and_no_config_changes(service):
    s, cfg, jobs = service
    before = copy.deepcopy(cfg)
    r = s.start(body())
    assert s.load()["conversations"][r["conversation_id"]]["turns"][0]["status"] == "working"
    jobs.pop()()
    state = chat.Chat(s.desk, s.companion).state("live")
    assert state["conversations"][0]["turns"][0]["provider_used"] == "Local explanation"
    assert not s.state("paper")["conversations"]
    cfg["broker_identity"]["account_id"] = "other"
    assert not s.state("live")["conversations"]
    cfg["broker_identity"] = before["broker_identity"]
    assert cfg == before


def test_idempotent_duplicate_conflicting_receipt_and_restart_no_paid_retry(service):
    s, _, jobs = service
    b = body("Explain the missing data")
    one = s.start(b)
    assert s.start(b)["conversation_id"] == one["conversation_id"] and len(jobs) == 1
    with pytest.raises(ValueError, match="different"):
        s.start(dict(b, message="different"))
    restarted = chat.Chat(s.desk, s.companion)
    assert restarted.state("live")["conversations"][0]["turns"][0]["status"] == "interrupted"
    assert restarted.start(b)["turn"]["status"] == "interrupted" and len(jobs) == 1


def test_model_gets_bounded_history_scrubbed_facts_roles_and_budget(service, monkeypatch):
    s, cfg, jobs = service
    calls = []
    def model(prompt, **kwargs):
        assert llm_trader.current_cost_scope() == "companion_chat"
        assert kwargs["max_output_tokens"] == 1600
        assert "SECRET-ACCOUNT" not in prompt and "SECRET-KEY" not in prompt
        calls.append(json.loads(prompt))
        return "Changing Woman: settings are historical. Fox: inspect current checks. [desk_status]"
    monkeypatch.setattr(llm_trader, "gemini_generate", model)
    r = s.start(body("Explain SECRET-ACCOUNT and SECRET-KEY"));jobs.pop()()
    s.start(body("And what does that mean?", conversation_id=r["conversation_id"]));jobs.pop()()
    assert len(calls) == 2 and len(calls[1]["conversation"]) == 2
    assert calls[1]["conversation"][0]["assistant"].startswith("Changing Woman")
    cfg["llm_enabled"] = False
    s.start(body("Open-ended question", conversation_id=r["conversation_id"]));jobs.pop()()
    assert len(calls) == 2 and s.state("live")["conversations"][0]["turns"][-1]["status"] == "failed"


def test_correction_forgetting_revokes_old_replies_from_future_context(service, monkeypatch):
    s, _, jobs = service
    s.memory({"action": "save", "text": "A note needing correction"}, "live")
    note = s.state("live")["memories"][0]
    calls = []
    monkeypatch.setattr(llm_trader, "gemini_generate", lambda prompt, **kw: calls.append(json.loads(prompt)) or "A short answer. [desk_status]")
    r = s.start(body("First question"));jobs.pop()()
    s.memory({"action": "save", "id": note["id"], "version": note["version"], "text": "Corrected note"}, "live")
    with pytest.raises(ValueError, match="another tab"):
        s.memory({"action": "save", "id": note["id"], "version": note["version"], "text": "stale edit"}, "live")
    s.start(body("Second question", conversation_id=r["conversation_id"]));jobs.pop()()
    assert len(calls[1]["conversation"]) == 1 and calls[1]["notes"][0]["text"] == "Corrected note"
    s.memory({"action": "forget", "id": note["id"]}, "live")
    s.start(body("Third question", conversation_id=r["conversation_id"]));jobs.pop()()
    assert not calls[-1]["notes"] and len(calls[-1]["conversation"]) == 1


def test_real_model_regression_qualified_does_not_mean_winning(service, monkeypatch):
    s, _, jobs = service
    monkeypatch.setattr(llm_trader, "gemini_generate", lambda *a, **k: "There are no successful results (qualified outcomes) yet. [desk_status]")
    s.start(body("What is a qualified outcome?")); jobs.pop()()
    failed = s.state("live")["conversations"][0]["turns"][0]
    assert failed["status"] == "failed" and "Valid losses" in failed["error"]
    monkeypatch.setattr(llm_trader, "gemini_generate", lambda *a, **k: "Qualified means usable evidence, not winning trades. Valid losses count too. [desk_status]")
    s.start(body("Do valid losses count?")); jobs.pop()()
    assert s.state("live")["conversations"][0]["turns"][0]["status"] == "complete"


def test_incomplete_model_reply_has_safe_actionable_error_without_retry(service, monkeypatch):
    s, _, jobs = service
    calls = []
    def fail(*a, **k):
        assert k['bounded_reasoning'] is True
        calls.append(1)
        raise RuntimeError('gemini_incomplete:MAX_TOKENS')
    monkeypatch.setattr(llm_trader, 'gemini_generate', fail)
    s.start(body('Please explain')); jobs.pop()()
    turn = s.state('live')['conversations'][0]['turns'][0]
    assert turn['error_code'] == 'gemini_incomplete' and 'partial answer was withheld' in turn['error']
    assert calls == [1] and not jobs


def test_mixed_unknown_citation_cannot_hide_inside_comma_list(service, monkeypatch):
    s, _, jobs = service
    monkeypatch.setattr(llm_trader, 'gemini_generate', lambda *a, **k: 'A claim [desk_status, invented_source]')
    s.start(body('Explain a source')); jobs.pop()()
    turn = s.state('live')['conversations'][0]['turns'][0]
    assert turn['status'] == 'failed' and 'unsupported reference' in turn['error']


def test_note_revoked_during_model_withholds_answer(service, monkeypatch):
    s, _, jobs = service
    s.memory({"action": "save", "text": "old note"}, "live")
    note = s.state("live")["memories"][0]
    def model(*a, **k):
        s.memory({"action": "forget", "id": note["id"]}, "live")
        return "Old answer [desk_status]"
    monkeypatch.setattr(llm_trader, "gemini_generate", model)
    s.start(body("Discuss the note"));jobs.pop()()
    t = s.state("live")["conversations"][0]["turns"][0]
    assert t["status"] == "cancelled" and "reply" not in t


def test_cancel_and_forget_during_work_never_resurrect(service, monkeypatch):
    s, _, jobs = service
    def model(*a, **k):
        with s.lock:
            s.save({"conversations": {}, "memories": [], "revision": 0})
        return "Old answer [desk_status]"
    monkeypatch.setattr(llm_trader, "gemini_generate", model)
    s.start(body("A new question"));jobs.pop()()
    assert not s.state("live")["conversations"]


def test_unknown_citation_no_model_fallback_and_corrupt_store_preserved(service, monkeypatch):
    s, _, jobs = service
    calls = []
    monkeypatch.setattr(llm_trader, "gemini_generate", lambda *a, **k: calls.append(1) or "Wrong [invented]")
    s.start(body("A question"));jobs.pop()()
    assert s.state("live")["conversations"][0]["turns"][0]["status"] == "failed" and calls == [1]
    s.path.write_text('{"conversations": []}')
    before = s.path.read_bytes()
    with pytest.raises(ValueError, match="recovery"): s.state("live")
    assert s.path.read_bytes() == before


def test_expiry_exclusion_retention_and_bounds(service):
    s, _, jobs = service
    with pytest.raises(ValueError): s.start(body("x" * 3001))
    with pytest.raises(ValueError): s.start(body(ticker="<script>"))
    s.memory({"action": "save", "text": "expires"}, "live")
    raw = s.load();raw["memories"][0]["expires_at"] = "2000-01-01T00:00:00+00:00";s.save(raw)
    assert not s.state("live")["memories"]
