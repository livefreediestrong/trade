import copy
import json
import threading
from types import SimpleNamespace as NS

import pytest
from flask import Flask
import trading_goals as goals


@pytest.fixture
def fixture(monkeypatch):
    cfg = {"mode": "auto_live", "session_active": True, "broker_identity": {"account_id": "test"},
           "live_agent": {"enabled": True, "policy": {"max_order_usd": 5}}, "daily_profit_target_usd": 3,
           "kill_switch": {"max_daily_loss_usd": 2}, "paper_profit_target_usd": 10}
    desk = NS(load_config=lambda: copy.deepcopy(cfg), save_config=lambda x: (cfg.clear(), cfg.update(x)),
              _lock=threading.RLock(), _CORRUPT_PATHS=set(), append_journal=lambda *a: None,
              load_ledger=lambda: {"pending_broker_orders": []}, daily_stats=lambda _: {"pnl": 4},
              _today_str=lambda: "2026-09-25")
    monkeypatch.setattr(goals,"broker_view",lambda *_:{"risk_ready":False,"day_pnl":None,"error":"Missing P&L"})
    app = Flask(__name__);goals.register(app,desk)
    return app.test_client(), cfg, desk


@pytest.mark.parametrize("scope,preset,target", [("live","five",5),("paper","twenty_five",25),("live","observe",None)])
def test_route_saves_only_selected_goal_and_does_not_activate_or_raise_limits(fixture,scope,preset,target):
    client,cfg,_=fixture;before=copy.deepcopy(cfg)
    r=client.post('/api/trading-goals',json={"scope":scope,"preset":preset,"revision":goals.revision(cfg)})
    assert r.status_code==200,r.json
    assert cfg[goals.FIELDS[scope]]==target
    for key in before:
        if key != goals.FIELDS[scope]: assert cfg[key]==before[key]
    assert r.json['live']['progress']['pnl'] is None
    assert r.json['live']['progress']['percent'] is None
    assert r.json['paper']['progress']['pnl']==4


@pytest.mark.parametrize("value",[True,-1,"NaN","Infinity","0.001",1000000001])
def test_invalid_targets_leave_entire_config_unchanged(fixture,value):
    client,cfg,_=fixture;before=copy.deepcopy(cfg)
    r=client.post('/api/trading-goals',json={"scope":"live","preset":"custom","target":value,"revision":goals.revision(cfg)})
    assert r.status_code==400 and cfg==before


def test_conflict_and_cross_scope_preset(fixture):
    client,cfg,_=fixture;old=goals.revision(cfg);cfg['daily_profit_target_usd']=7
    assert client.post('/api/trading-goals',json={"scope":"live","preset":"five","revision":old}).status_code==409
    assert client.post('/api/trading-goals',json={"scope":"live","preset":"fifty","revision":goals.revision(cfg)}).status_code==400


def test_missing_or_corrupt_evidence_does_not_complete_milestones(fixture):
    client,cfg,desk=fixture;desk._CORRUPT_PATHS.add('ledger')
    d=client.get('/api/trading-goals').json
    assert d['paper']['progress']['pnl'] is None
    assert d['paper']['milestones'][0]['complete'] is None
    assert d['live']['milestones'][1]['complete'] is None
    assert client.post('/api/trading-goals',json={"scope":"paper","preset":"ten","revision":goals.revision(cfg)}).status_code==409


def test_paper_config_uses_only_paper_target():
    import app
    cfg=copy.deepcopy(app.DEFAULT_CONFIG)
    cfg.update(daily_profit_target_usd=100,paper_profit_target_usd=12)
    assert app.paper_research_config(cfg)['daily_profit_target_usd']==12
    assert cfg['daily_profit_target_usd']==100
