"""Regressions for background work, transport, evidence batching and packaging."""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import json

import pytest

import app as desk
import desk_operations as ops
import release_tools


@pytest.mark.parametrize('active,paper,rth_only,opened,expected', [
    (True, False, True, False, False), (False, True, True, False, False),
    (True, False, True, True, True), (False, True, True, True, True),
    (False, False, False, True, False), (True, False, False, False, True),
])
def test_scheduled_scan_uses_the_session_clock(monkeypatch, active, paper, rth_only, opened, expected):
    monkeypatch.setattr(desk.paper_loop_mod, 'is_rth', lambda _: opened)
    assert desk._scheduled_scan_enabled(dict(session_active=active, paper_research_enabled=paper, rth_only=rth_only)) is expected


@pytest.mark.parametrize('opens,stops,expected', [(False, False, []), (True, False, ['scan','ingest']), (True, True, ['scan'])])
def test_actual_background_loop_skips_closed_and_revoked_scans(monkeypatch, opens, stops, expected):
    cfg = dict(desk.DEFAULT_CONFIG, session_active=True, mode='live_manual')
    calls = []
    ticks = iter([False, True])
    class Stop:
        def wait(self, **_): return next(ticks)
    def scan(*_, **__):
        calls.append('scan')
        if stops: cfg['session_active'] = False
        return {'id':'new'}
    monkeypatch.setattr(desk, '_bg_stop', Stop())
    monkeypatch.setattr(desk, 'load_config', lambda: dict(cfg))
    monkeypatch.setattr(desk, 'load_signals', lambda: [])
    monkeypatch.setattr(desk, 'expire_stale_signals', lambda rows: rows)
    monkeypatch.setattr(desk, '_reconcile_pending_broker_orders', lambda: None)
    monkeypatch.setattr(desk.paper_loop_mod, 'is_rth', lambda _: opens)
    monkeypatch.setattr(desk, 'generate_scan_signal', scan)
    monkeypatch.setattr(desk, 'ingest_signal', lambda _, **kwargs: calls.append('ingest'))
    monkeypatch.setattr(desk, 'append_journal', lambda *args: pytest.fail(str(args)))
    desk._bg_loop()
    assert calls == expected


def test_closed_early_session_clock():
    # Day after Thanksgiving closes at 1pm Eastern, including on the scan path.
    assert desk.paper_loop_mod.is_rth(datetime(2026,11,27,17,59,tzinfo=timezone.utc))
    assert not desk.paper_loop_mod.is_rth(datetime(2026,11,27,18,0,tzinfo=timezone.utc))


def test_stream_omits_only_unchanged_history_and_resends_corrections():
    payload = {'signals':{'pending':[{'id':'p'}], 'approving':[], 'broker_pending':[],
               'approved':[], 'rejected':[], 'expired':[{'id':'old','reason':'x'*10000,'quote':{'age_sec':100,'fresh':False}}]},
               'broker_book':{'risk_ready':False}, 'broker_ledger':{'pending_broker_orders':[{'id':'working'}]},
               'config':{'kill_switch':{'armed':False}}}
    prior = {}
    first = desk._stream_state_payload(payload, prior)
    assert set(first['signals']) == set(payload['signals'])
    payload['signals']['expired'][0]['quote']['age_sec'] += 5
    second = desk._stream_state_payload(payload, prior)
    assert set(second['signals']) == {'pending','approving','broker_pending'}
    assert second['broker_book'] == payload['broker_book']
    assert second['broker_ledger'] == payload['broker_ledger']
    assert second['config'] == payload['config']
    assert len(json.dumps(second)) < len(json.dumps(first))/10
    # Removing the last old row must send [], not leave the client with stale history.
    payload['signals']['expired'] = []
    assert desk._stream_state_payload(payload, prior)['signals']['expired'] == []
    assert 'expired' in desk._stream_state_payload(payload, {})['signals']  # reconnect
    payload['signals']['approved'] = [{'id':'a','fill':{'shares':2,'confirmed':True}}]
    assert desk._stream_state_payload(payload, prior)['signals']['approved'][0]['fill']['shares'] == 2


def test_evidence_batch_is_atomic_deduplicated_and_preserves_revisions(tmp_path, monkeypatch):
    store = ops.EvidenceStore(tmp_path)
    connections = []
    original = store.db
    @contextmanager
    def counted():
        connections.append(1)
        with original() as db: yield db
    monkeypatch.setattr(store, 'db', counted)
    rows = [('signal','paper:local',str(i),{'value':i}) for i in range(200)]
    ids = store.put_many(rows)
    assert len(connections) == 1
    assert store.put_many(rows) == ids
    assert len(store.list(limit=1000)) == 200
    corrected = store.put('signal','paper:local','0',{'value':20})
    assert corrected != ids[0] and store.get(ids[0])['payload'] == {'value':0}
    with pytest.raises(ValueError):
        store.put_many([('signal','paper:local','new',{'value':1}),('signal','paper:local','bad',{'value':float('nan')})])
    assert len(store.list(limit=1000)) == 201


def test_release_includes_setup_but_excludes_debug_material(tmp_path):
    for name in ('app.py','Launch.vbs','.env.example','.gitignore','_ui_shot.png','_probe_auth.ps1','debug_populate.ps1','smoke_desk.ps1','DEPLOY_NOTES.md','.env'):
        (tmp_path/name).write_text('x=1\n')
    names = {p.name for p in release_tools.source_files(tmp_path)}
    assert names == {'app.py','Launch.vbs','.env.example','.gitignore'}
    archive = tmp_path/'source.zip'
    release_tools.pack(tmp_path, archive)
    assert release_tools.verify(archive)['file_count'] == 4
