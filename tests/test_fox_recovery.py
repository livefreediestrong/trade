"""Recovery failures and ownership boundaries: no market or broker network calls."""
import copy
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from pathlib import Path

import pytest
import app as desk
import broker_ibkr as broker
import desk_backups as backups
import llm_trader as llm
import live_agent
import market_events as events
import release_tools
from test_execution_repairs import execution
from test_live_agent import live
from test_ibkr_pnl import pnl_gateway


@pytest.fixture
def model(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "USAGE_PATH", tmp_path/"usage.json")
    monkeypatch.setattr(llm, "_BRAIN_HEALTH", {})
    monkeypatch.setattr(llm, "_NO_THINKING_LEVEL", set())
    clock = [100.]
    monkeypatch.setattr(llm._time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(llm._time, "sleep", lambda n: clock.__setitem__(0, clock[0]+n))
    replies, calls = [], []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return replies.pop(0)
    monkeypatch.setattr(llm.requests, "post", post)
    return replies, calls, clock


def response(code, message="temporarily unavailable", **headers):
    data = {"error": {"message": message}} if code != 200 else {"candidates": [
        {"finishReason": "STOP", "content": {"parts": [{"text": "{}"}]}}]}
    return NS(status_code=code, json=lambda: data, headers=headers, text=message)


def test_overload_recovers_within_original_deadline_and_counts_each_call(model):
    replies, calls, _ = model
    replies.extend([response(503), response(429), response(200)])
    assert llm.gemini_generate("fixture", cfg={"api_key": "fixture", "model": "m"}, timeout_sec=10) == "{}"
    assert len(calls) == 3 and 0 < calls[2]["timeout"] < calls[1]["timeout"] < calls[0]["timeout"] <= 10
    usage = llm._GEMINI_BUDGET.status()
    assert usage["daily_used"] == 3
    assert llm.brain_health("m")["state"] == "healthy"


def test_auth_errors_do_not_retry_or_leak_secrets(model):
    replies, calls, _ = model
    replies.append(response(403, "rejected private-test-key"))
    with pytest.raises(RuntimeError) as caught:
        llm.gemini_generate("fixture", cfg={"api_key": "private-test-key", "model": "m"})
    assert len(calls) == 1 and "private-test-key" not in str(caught.value)
    assert "private-test-key" not in json.dumps(llm.brain_health("m"))


def test_retry_after_beyond_deadline_does_not_sleep_or_retry(model):
    replies, calls, clock = model
    replies.append(response(503, **{"Retry-After": "120"}))
    with pytest.raises(RuntimeError, match="gemini_http_503"):
        llm.gemini_generate("fixture", cfg={"api_key": "fixture"}, timeout_sec=5)
    assert len(calls) == 1 and clock[0] == 100


def test_stale_data_is_not_a_provider_error(monkeypatch):
    monkeypatch.setattr(desk, "_research_thesis", lambda *a, **k: pytest.fail("Must not spend on stale data"))
    sig = desk._enrich_signal_with_llm({}, {"quote": {"fresh": False}}, {})
    assert not sig["llm_error"] and sig["data_error"]
    assert desk.signal_execution_block(sig).startswith("Market data")
    assert desk.signal_execution_block({"llm_error": "stale_or_unverified_market_data"}).startswith("Market data")


def test_legacy_auto_live_cannot_bypass_fox(execution):
    cfg, sig, sent = execution
    cfg.update(mode="auto_live", live_agent=None)
    result = desk.execute_gated_broker_or_paper(sig, cfg, source="auto_live", via="auto_live")
    assert not result["ok"] and "policy" in result["error"] and not sent
    assert desk._scheduled_scan_config(cfg) is None


def test_reviews_are_actual_scoped_expiring_and_deduplicated(live, monkeypatch):
    service, cfg = live.service, live.cfg
    base = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)
    monkeypatch.setattr(live_agent, "now_utc", lambda: base)
    service.note_review(cfg, "TEST", None, {"verdict": "WATCH", "text": "Below volume floor"})
    service.note_review(cfg, "TEST", None, {"verdict": "PASS", "text": "Setup qualified"})
    service.note_review(cfg, "OTHER", None, {"verdict": "AVOID", "text": "Trend failed"})
    assert [r["ticker"] for r in service.status()["reviews"]] == ["TEST", "OTHER"]
    other = copy.deepcopy(cfg); other["broker_identity"]["account_id"] = "OTHER"
    assert service.current_reviews(service.load(), other) == []
    monkeypatch.setattr(live_agent, "now_utc", lambda: base+timedelta(hours=2))
    assert service.status()["reviews"] == []


def test_pnl_recovery_uses_replacement_callback(pnl_gateway, monkeypatch):
    fake, _, _, _, _ = pnl_gateway
    def recover(ib, account, old):
        row = dict(old, value=NS(account=account, modelCode="", dailyPnL=-7.25))
        broker._PNL[(id(ib), account)] = row
        broker._pnl_update(row["value"])
        return True
    monkeypatch.setattr(broker, "_recover_initial_pnl", recover)
    result = broker.get_account.__wrapped__()
    assert result["risk_ready"] and result["account"]["day_pnl"] == -7.25


@pytest.fixture
def archive_desk(tmp_path):
    data = tmp_path/"data"; data.mkdir()
    def save(path, value):
        path.write_text(json.dumps(value), encoding="utf-8")
    (data/"config.json").write_text('{"mode":"auto_live"}', encoding="utf-8")
    (data/".env").write_text("SECRET=never-copy", encoding="utf-8")
    return NS(DATA_DIR=data, _lock=threading.RLock(), _CORRUPT_PATHS=set(), _save_json=save)


def test_state_snapshot_includes_committed_wal_and_excludes_credentials(archive_desk):
    dbpath = archive_desk.DATA_DIR/"evidence.sqlite3"
    with sqlite3.connect(dbpath) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE trades (id INTEGER)")
        db.execute("INSERT INTO trades VALUES (42)"); db.commit()
        row = backups.create(archive_desk)
        import zipfile
        path = archive_desk.DATA_DIR/"backups"/row["archive"]
        manifest = backups.verify(path)
        assert set(manifest["files"]) == {"config.json", "evidence.sqlite3"}
        with zipfile.ZipFile(path) as z:
            restored = archive_desk.DATA_DIR/"test-copy.sqlite3"
            restored.write_bytes(z.read("evidence.sqlite3"))
        with sqlite3.connect(restored) as copied:
            assert copied.execute("SELECT id FROM trades").fetchall() == [(42,)]
    assert backups.status(archive_desk)["ok"]


def test_failed_backup_preserves_last_verified_snapshot(archive_desk):
    first = backups.create(archive_desk)
    (archive_desk.DATA_DIR/"config.json").write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        backups.create(archive_desk)
    assert backups.status(archive_desk)["archive"] == first["archive"]
    assert len(list((archive_desk.DATA_DIR/"backups").glob("state-*.zip"))) == 1


def test_source_pack_does_not_publish_invalid_python(tmp_path):
    root=tmp_path/"source";root.mkdir();(root/"app.py").write_text("def broken(")
    output=tmp_path/"release.zip"
    with pytest.raises(SyntaxError):
        release_tools.pack(root,output)
    assert not output.exists()
    assert not release_tools.allowed("") and not release_tools.allowed(".")


def test_calendar_rejects_naive_or_malformed_rows_and_snapshot_expires():
    now=datetime(2026,9,25,tzinfo=timezone.utc)
    rows=events.bls_offline_schedule(now)
    jobs=next(r for r in rows if r["title"]=="Employment Situation")
    assert jobs["start"] == "2026-10-02T12:30:00+00:00" and jobs["schedule_snapshot"]
    bad=[None, {"start":"2026-09-25T10:00:00"}, {**jobs,"impact":"invalid"}]
    assert events._window_filter(bad,now)==[]
    assert not events.bls_offline_schedule(datetime(2026,11,1,12,tzinfo=timezone.utc))


def test_corrupt_optional_calendar_cache_does_not_break_status(monkeypatch,tmp_path):
    monkeypatch.setattr(events,"_state",{"loaded":False,"at":0,"sources":{}})
    monkeypatch.setattr(events,"_data_dir",tmp_path)
    (tmp_path/"market_events.json").write_text(json.dumps({"at":"oops","by_source":{"fed":None}}))
    assert events.status()["refreshed_at"] is None


def test_chat_failure_is_an_api_error_not_a_success(execution, monkeypatch):
    monkeypatch.setattr(desk, "_rate_limited", lambda *a: False)
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "[error: llm_timeout]")
    monkeypatch.setattr(desk, "_llm_public_status", lambda cfg: {"configured": True})
    client=desk.app.test_client()
    response=client.post('/api/llm/chat',base_url='http://127.0.0.1:5056',json={"message":"fixture"})
    assert response.status_code==503 and not response.json["ok"]
    for body in (["invalid"], {"message":3}, {"message":"fixture","ticker":3}):
        assert client.post('/api/llm/chat',base_url='http://127.0.0.1:5056',json=body).status_code==400


def test_jev_error_redacts_echoed_key(monkeypatch,tmp_path):
    monkeypatch.setattr(llm,"typesafe_api_key",lambda:'private-jev-key')
    monkeypatch.setattr(llm,"USAGE_PATH",tmp_path/'usage.json')
    monkeypatch.setattr(llm.requests,"post",lambda *a,**k:response(503,'failed private-jev-key'))
    out=llm.jev_trade_thesis({"ticker":"TEST"})
    assert out["error"] and 'private-jev-key' not in json.dumps(out)


def test_empty_activation_cannot_authorize_fox(live):
    cfg=copy.deepcopy(live.cfg)
    cfg['live_agent'].update(run_id=None,revision=None)
    signal=dict(source='live_agent',agent_identity=cfg['broker_identity'],ticker='TEST')
    assert 'incomplete' in live_agent.authorization_error(signal,cfg)


def test_bls_failure_has_labeled_snapshot_and_success_replaces_it(monkeypatch):
    now=datetime(2026,9,25,15,tzinfo=timezone.utc)
    monkeypatch.setattr(events,'_state',{'loaded':True,'at':0,'sources':{},'by_source':{}})
    monkeypatch.setattr(events,'_save',lambda:None)
    monkeypatch.setattr(events,'_president_urls',lambda:[])
    monkeypatch.setattr(events,'_extra_urls',lambda:[])
    monkeypatch.setattr(events,'_source_fed',lambda:[])
    def fail():raise ValueError('HTTP 403')
    monkeypatch.setattr(events,'_source_bls',fail)
    first=events.refresh(now)
    assert not first['sources']['bls']['ok'] and first['sources']['bls']['fallback']=='dated schedule'
    assert events._state['by_source']['bls'][0]['schedule_snapshot']
    fresh=events._event('bls','Employment Situation',now,impact='high',kind='jobs_report')
    monkeypatch.setattr(events,'_source_bls',lambda:[fresh])
    assert events.refresh(now)['sources']['bls']['ok']
    assert events._state['by_source']['bls']==[fresh]


def test_backup_checksum_tampering_is_detected(archive_desk):
    import zipfile
    row=backups.create(archive_desk)
    archive=archive_desk.DATA_DIR/'backups'/row['archive']
    tampered=archive.with_name('tampered.zip')
    with zipfile.ZipFile(archive) as src,zipfile.ZipFile(tampered,'w') as dst:
        for name in src.namelist():
            dst.writestr(name,b'{}' if name=='config.json' else src.read(name))
    with pytest.raises(ValueError,match='checksum'):
        backups.verify(tampered)


def test_workspace_never_reports_old_broker_cache_ready(live,monkeypatch):
    monkeypatch.setattr(desk,'_BROKER_BOOK_CACHE',{'at':-1e9,'val':{'ok':True,'risk_ready':True,'day_pnl_usd':1},'key':('old-account',)})
    monkeypatch.setattr(llm,'status_public_extended',lambda cfg:{'configured':False})
    monkeypatch.setattr(events,'events',lambda *a:[])
    monkeypatch.setattr(events,'status',lambda:{'sources':{}})
    out=desk.app.test_client().get('/api/fox-workspace',base_url='http://127.0.0.1:5056').json
    assert out['ok'] and not out['broker']['risk_ready'] and out['broker']['day_pnl'] is None
    assert len(out['calendar']['days'])==7


def test_upkeep_surfaces_failed_backup(monkeypatch,tmp_path):
    from tools import desk_upkeep as upkeep
    for name in ('archive_root_clutter','prune_archive','rotate_logs','clean_caches','sqlite_maintenance','data_usage'):
        monkeypatch.setattr(upkeep,name,lambda:{})
    def fail():raise OSError('disk full')
    monkeypatch.setattr(upkeep,'verified_backup',fail)
    monkeypatch.setattr(upkeep,'health',lambda:{'ok':True})
    monkeypatch.setattr(upkeep,'_save',lambda *a:None)
    monkeypatch.setattr(upkeep,'update_tasks',lambda *a:None)
    monkeypatch.setattr(upkeep,'log',lambda *a:None)
    report=upkeep.daily(with_tests=False)
    assert any('backup' in p and 'disk full' in p for p in report['problems'])
