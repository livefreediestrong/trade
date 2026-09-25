"""Automatic evidence review, with real broker and network calls forbidden."""
from datetime import timedelta
import copy

import pytest
import app as desk
from agent_review import AgentReview, conclusion
from desk_operations import EvidenceStore
from research_companion import Companion
from test_moss_workday import isolated, NOW, BASE, event, execution


@pytest.fixture
def review(isolated, monkeypatch):
    monkeypatch.setattr(desk, "_CORRUPT_PATHS", {})
    companion = Companion(desk)
    monkeypatch.setattr(desk, "_market_scanner", None)
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: pytest.fail("Review must not fill orders"))
    monkeypatch.setattr(companion, "run", lambda *a, **k: pytest.fail("Review must not call a model"))
    return companion.review


@pytest.mark.parametrize("n,days,mean,state", [(29,8,5,"collecting"),(100,4,5,"collecting"),
    (30,5,None,"no_edge"),(30,5,0,"no_edge"),(30,5,-2,"no_edge"),(30,5,2,"descriptive_positive")])
def test_conclusions_never_promote_live_trading(n, days, mean, state):
    result = conclusion({"overall":{"outcomes":n,"session_days":days,"mean_net_bps":mean}})
    assert result["state"] == state
    if state == "descriptive_positive":
        assert "does not qualify real-money" in result["next"]


def test_replayable_review_is_saved_once_and_recomputes_late_fees(review, isolated):
    config = copy.deepcopy(desk.load_config())
    desk._save_json(desk.DECISIONS_PATH,{"events":[event(0,confidence=.8),event(1,confidence=.8,mock=True)]})
    rows = [execution(),execution("future",ts=(NOW+timedelta(days=1)).isoformat()),
            execution("paper",paper_mode=True,account_id="DU1234")]
    desk._save_json(review.companion.trades.path,{"executions":rows})
    review.tick(force=True,now=NOW)
    first=review.status()["latest"]
    assert review.error is None
    assert first["paper"]["outcomes"] == 1
    assert first["excluded"]["mock_or_routed"] == 1
    assert first["execution_timestamp_exclusions"] == 1
    assert "executions" not in first["execution_quality"]
    assert {g["scope"] for g in first["execution_quality"]["groups"]} == {"live","broker_paper"}
    assert first["execution_quality"]["groups"][0]["fees_missing_or_other_currency"] == 1
    replay = isolated.post('/api/research/evaluate/'+first["evaluation_id"]+'/replay',base_url=BASE,json={})
    assert replay.status_code == 200 and replay.json["identical"]
    import research_metrics
    captured = EvidenceStore(desk.DATA_DIR).get(first["execution_evidence_id"])["payload"]
    assert research_metrics.execution_costs(captured["executions"], captured["signals"]) == captured["result"]
    review.tick(force=True,now=NOW+timedelta(seconds=10))
    assert review.status()["latest"]["evidence_id"] == first["evidence_id"]
    assert len(EvidenceStore(desk.DATA_DIR).list("agent_review")) == 1
    rows[0].update(commission=.35,commission_currency="USD")
    desk._save_json(review.companion.trades.path,{"executions":rows})
    review.tick(force=True,now=NOW+timedelta(seconds=20))
    changed=review.status()["latest"]
    assert changed["evidence_id"] != first["evidence_id"]
    assert changed["execution_quality"]["groups"][0]["fees_reported"] == .35
    assert desk.load_config() == config


@pytest.mark.parametrize("damaged",["review","events","trades","signals"])
def test_corrupt_evidence_does_not_become_empty_success(review, damaged):
    paths={"review":review.path,"events":desk.DECISIONS_PATH,"trades":review.companion.trades.path,"signals":desk.SIGNALS_PATH}
    review.tick(force=True,now=NOW)
    before=len(EvidenceStore(desk.DATA_DIR).list("agent_review"))
    paths[damaged].write_text('{broken',encoding='utf-8')
    review.tick(force=True,now=NOW)
    assert review.error and "repair" in review.error
    assert len(EvidenceStore(desk.DATA_DIR).list("agent_review")) == before


@pytest.mark.parametrize("state,stale,enabled,open_market,starts",[
    ("idle",False,True,True,1),("complete",True,True,True,1),("paused",True,True,True,0),
    ("running",True,True,True,0),("complete",False,True,True,0),("idle",False,False,True,0),
    ("idle",False,True,False,0)])
def test_daily_scan_respects_pause_and_market_schedule(review, monkeypatch, state, stale, enabled, open_market, starts):
    from types import SimpleNamespace
    import agent_review
    started=[]
    scanner=SimpleNamespace(snapshot=lambda:{"state":state,"stale":stale},start=lambda:started.append(True))
    monkeypatch.setattr(desk,"_market_scanner",lambda:scanner)
    monkeypatch.setattr(agent_review.paper_loop,"is_rth",lambda now:open_market)
    saved=review.companion.load();saved["settings"]["enabled"]=enabled;review.companion.save(saved)
    review.tick(force=True,now=NOW)
    assert review.error is None
    assert len(started)==starts


def test_review_failures_keep_last_conclusion_and_are_reported(review, monkeypatch):
    review.tick(force=True,now=NOW)
    before=review.status()["latest"]
    monkeypatch.setattr(review.companion,"events",lambda:1/0)
    review.tick(force=True,now=NOW)
    assert review.error
    assert review.status()["latest"]==before


def test_wrong_review_document_shape_is_not_overwritten(review):
    review.path.write_text('[]',encoding='utf-8')
    review.tick(force=True,now=NOW)
    assert "repair" in review.error
    assert review.path.read_text(encoding='utf-8')=='[]'


def test_manual_reference_and_layout_have_no_arming_side_effect(isolated):
    before=copy.deepcopy(desk.load_config())
    ref=isolated.get('/manual-live-enablement',base_url=BASE)
    assert ref.status_code==200 and ref.mimetype=='text/plain'
    assert 'IBKR_LIVE=true' in ref.text and '"mode": "auto_live"' in ref.text
    page=isolated.get('/',base_url=BASE).text
    assert page.index('id="live-automation"') < page.index('id="agent-research"') < page.index('id="desk-live"')
    for ident in ('live-automation','agent-research','stocks-on-sale','research-studio','live-execution-settings'):
        assert page.count('id="'+ident+'"')==1
    assert 'class="research-shelf" role="region"' in page
    assert desk.load_config()==before
