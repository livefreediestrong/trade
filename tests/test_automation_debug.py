"""Scheduler/recovery regressions with network and broker writes intercepted."""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest
import app as desk
import research_companion as companion
import moss_paper
import screener_logic
from test_moss_workday import isolated, NOW, analysis, event


def background(monkeypatch, cfg, rows=None, scan=None, ticks=1):
    calls, errors = [], []
    clock = [100.]
    class Stop:
        count = 0
        def wait(self, **kwargs):
            self.count += 1
            clock[0] += 5
            return self.count > ticks
    def generate(config, **kwargs):
        calls.append(('scan', copy.deepcopy(config)))
        return scan(config, **kwargs) if scan else {'id':'generated'}
    monkeypatch.setattr(desk, '_bg_stop', Stop())
    monkeypatch.setattr(desk.time, 'monotonic', lambda:clock[0])
    monkeypatch.setattr(desk, 'load_config', lambda:copy.deepcopy(cfg))
    monkeypatch.setattr(desk, 'load_signals', lambda:copy.deepcopy(rows or []))
    monkeypatch.setattr(desk, 'expire_stale_signals', lambda rows:rows)
    monkeypatch.setattr(desk, '_reconcile_pending_broker_orders', lambda:None)
    monkeypatch.setattr(desk.paper_loop_mod, 'is_rth', lambda *args:True)
    monkeypatch.setattr(desk, 'generate_scan_signal', generate)
    monkeypatch.setattr(desk, 'ingest_signal', lambda sig, **kwargs:calls.append(('ingest',copy.deepcopy(sig))))
    monkeypatch.setattr(desk, 'append_journal', lambda kind, data:errors.append((kind,data)))
    desk._bg_loop()
    return calls, errors


def config(**changes):
    return dict(desk.DEFAULT_CONFIG, session_active=True, mode='live_manual', radar_enabled=False,
                scan_interval_sec=120, **changes)


def test_manual_ticket_without_ts_does_not_break_background(monkeypatch):
    calls, errors = background(monkeypatch, config(), [{'id':'manual','status':'rejected',
        'source':'manual_ticket','created_at':(datetime.now(timezone.utc)-timedelta(minutes=5)).isoformat()}])
    assert [name for name,_ in calls] == ['scan','ingest']
    assert not errors


@pytest.mark.parametrize('fails',[False,True])
def test_empty_or_failed_scan_obeys_interval(monkeypatch, fails):
    def scan(*args,**kwargs):
        if fails: raise RuntimeError('data failure')
        return None
    calls,_ = background(monkeypatch,config(),scan=scan,ticks=3)
    assert [name for name,_ in calls] == ['scan']


def test_stopped_live_session_with_moss_does_not_scan_live(monkeypatch):
    cfg=config(paper_research_enabled=True,moss_paper={'enabled':True})
    cfg['session_active']=False
    calls,_=background(monkeypatch,cfg)
    assert not calls


def test_paper_only_background_cannot_create_live_signal(monkeypatch):
    cfg=config(paper_research_enabled=True,paper_auto_approve=True)
    cfg['session_active']=False
    calls,_=background(monkeypatch,cfg)
    assert calls[0][1]['_paper_research'] is True
    assert calls[0][1]['mode']=='auto_paper'
    assert calls[1][1]['workspace']=='paper'


def test_mode_change_during_background_drops_old_decision(monkeypatch):
    cfg=config()
    def scan(*args,**kwargs):
        cfg['mode']='auto_live'
        return {'id':'old-manual-research'}
    calls,_=background(monkeypatch,cfg,scan=scan)
    assert [name for name,_ in calls] == ['scan']


def test_starting_companion_twice_does_not_duplicate_schedulers(isolated,monkeypatch):
    starts=[]
    class Thread:
        def __init__(self,**kwargs): self.name=kwargs['name']
        def start(self): starts.append(self.name)
        def is_alive(self): return self.name in starts
    monkeypatch.setattr(companion.threading,'Thread',Thread)
    service=companion.Companion(desk)
    service.start(); service.start()
    assert len(starts)==len(set(starts))==3


def test_one_failed_candidate_does_not_discard_good_candidates(isolated,monkeypatch):
    calls=[]
    def analyze(symbol):
        calls.append(symbol)
        if symbol=='AAA':raise TimeoutError('bad provider')
        return analysis(symbol)
    monkeypatch.setattr(screener_logic,'analyze_ticker',analyze)
    monkeypatch.setattr(desk.llm_trader,'model_cost_today',lambda:{'model_usd':99})
    engine=moss_paper.PaperWorkday(desk)
    engine._work()
    assert calls==['AAA','BBB']
    today=engine.load()['days']['2026-09-23']
    assert today['candidates_checked']==2 and today['qualified_candidates']==1 and today['model_calls']==0


def test_automatic_review_retains_archived_outcomes(isolated,monkeypatch):
    service=companion.Companion(desk)
    monkeypatch.setattr(desk,'_market_scanner',None)
    folder=desk.DATA_DIR/'research_history'; folder.mkdir()
    archived=event(0,confidence=.8)
    (folder/'2026-09-23.jsonl').write_text(json.dumps(archived)+'\n',encoding='utf-8')
    desk._save_json(desk.DECISIONS_PATH,{'events':[]})
    service.review.tick(force=True,now=NOW)
    assert service.review.error is None
    assert service.review.status()['latest']['paper']['outcomes']==1


def test_config_change_at_ingestion_boundary_drops_scan(isolated,monkeypatch):
    before=copy.deepcopy(desk.load_config())
    desk.save_config(dict(before, mode='auto_live'))
    monkeypatch.setattr(desk,'_ingest_one_signal',lambda *a,**k:pytest.fail('Old decision must not be ingested'))
    assert desk.ingest_signal({'id':'old','quote':{}},expected_config=before) is None


@pytest.mark.parametrize('revoke_at',['analysis','regime'])
def test_revoked_scan_never_starts_paid_enrichment(isolated,monkeypatch,revoke_at):
    permitted=[True]
    def analyze(symbol):
        if revoke_at=='analysis':permitted[0]=False
        return analysis(symbol)
    def regime(cfg):
        if revoke_at=='regime':permitted[0]=False
        return {}
    monkeypatch.setattr(screener_logic,'analyze_ticker',analyze)
    monkeypatch.setattr(desk,'market_regime_status',regime)
    monkeypatch.setattr(desk,'_analysis_to_signal',lambda *a,**kw:{'id':'candidate'})
    monkeypatch.setattr(desk,'_enrich_signal_with_llm',lambda *a,**kw:pytest.fail('Paid model must not run'))
    assert desk.generate_scan_signal(config(),ticker='AAA',still_authorized=lambda:permitted[0]) is None


@pytest.mark.parametrize('stamp',[None,'bad','2026-01-01T10:00:00','2099-01-01T10:00:00+00:00'])
def test_malformed_or_future_legacy_timestamp_does_not_stall_scan(monkeypatch,stamp):
    calls,errors=background(monkeypatch,config(),[{'id':'old','status':'rejected','workspace':'live','ts':stamp}])
    assert [name for name,_ in calls]==['scan','ingest'] and not errors


def test_pause_during_moss_collection_stops_rest_of_batch(isolated,monkeypatch):
    calls=[]
    def analyze(symbol):
        calls.append(symbol)
        desk.save_config(dict(desk.load_config(),paper_auto_approve=False))
        return analysis(symbol)
    monkeypatch.setattr(screener_logic,'analyze_ticker',analyze)
    monkeypatch.setattr(desk,'_research_thesis',lambda *a,**kw:pytest.fail('Paused workday must not call model'))
    engine=moss_paper.PaperWorkday(desk);engine._work()
    assert calls==['AAA']
    assert engine.load()['days']['2026-09-23']['model_calls']==0
