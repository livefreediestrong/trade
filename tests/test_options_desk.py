"""Options paper economics and execution races, isolated from every broker write."""
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import socket
from types import SimpleNamespace as NS

import pytest

import app as desk
import broker_ibkr
import options_desk as opt

NOW = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)
BASE = 'http://127.0.0.1:5056'


def ticket(**changes):
    return dict(dict(symbol='SPY', expiry='2026-09-25', strategy='long_call',
                     long_strike=100, contracts=1, budget=250, fee_per_contract=.65), **changes)


def quote(p, now=NOW):
    rows = []
    for i, leg in enumerate(opt.legs(p)):
        bid, ask = (1.1, 1.2) if i == 0 else ((2.4, 2.5) if p['kind']=='credit' else (.5, .6))
        rows.append(dict(leg, bid=bid, ask=ask, bid_size=10, ask_size=10, multiplier=100,
                         con_id=100+i, market_data_type=1, halted=0,
                         bid_observed_at=now.isoformat(), ask_observed_at=now.isoformat()))
    return dict(ok=True, source='IBKR streaming bid/ask', legs=rows)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(desk, 'CONFIG_PATH', tmp_path/'config.json')
    monkeypatch.setattr(desk, '_CORRUPT_PATHS', set())
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('Network forbidden'))
    monkeypatch.setattr(desk, 'live_broker_place_order', lambda *a, **k: pytest.fail('Broker write forbidden'))
    monkeypatch.setattr(desk, 'execute_gated_broker_or_paper', lambda *a, **k: pytest.fail('Broker route forbidden'))
    monkeypatch.setattr(opt, 'now_utc', lambda: NOW)
    desk.save_config(dict(desk.load_config(), mode='live_manual', paper_research_enabled=True))
    service = opt.OptionsDesk(desk)
    monkeypatch.setattr(service, 'quote', quote)
    return service


@pytest.mark.parametrize('strategy,long,short,risk,gain,break_even', [
    ('long_call',100,None,121.3,None,101.213), ('long_put',100,None,121.3,9878.7,98.787),
    ('call_debit',100,102,72.6,127.4,100.726), ('put_debit',102,100,72.6,127.4,101.274),
    ('call_credit',102,100,82.6,117.4,101.174), ('put_credit',100,102,82.6,117.4,100.826),
])
def test_all_six_payoffs(strategy,long,short,risk,gain,break_even):
    p=opt.plan(ticket(strategy=strategy,long_strike=long,short_strike=short))
    e=opt.economics(p,quote(p))
    assert e['maximum_loss_usd']==pytest.approx(risk)
    assert e['maximum_gain_usd']==(pytest.approx(gain) if gain is not None else None)
    assert e['break_even_at_expiry']==pytest.approx(break_even)


@pytest.mark.parametrize('changes',[
    {'strategy':[]},{'strategy':'naked_call'},{'contracts':.5},{'contracts':True},
    {'contracts':11},{'budget':'NaN'},{'fee_per_contract':-1},{'expiry':'2026-09-26'},
    {'long_strike':0},{'strategy':'call_debit','short_strike':99},{'short_strike':101},
    {'symbol':'AAPL;DROP'}, {'mode':'auto_live'},
])
def test_invalid_plans(changes):
    with pytest.raises(ValueError): opt.plan(ticket(**changes))


def test_paper_roundtrip_and_duplicate_without_broker_route(isolated, monkeypatch):
    cfg=copy.deepcopy(desk.load_config())
    r=isolated.preview(ticket()); f=isolated.fill(r['review_id'])
    assert f['live_execution'] is False and f['fill']['simulated']
    assert isolated.load()['free_cash']==878.7
    assert isolated.fill(r['review_id'])['duplicate']
    assert len(isolated.load()['fills'])==1
    # Closing remains possible when new entries are paused.
    desk.save_config(dict(cfg,paper_research_enabled=False))
    closing=isolated.preview(None, f['fill']['position_id'])
    assert closing['close_pnl_estimate']==-11.3 and closing['review_id']
    done=isolated.fill(closing['review_id'])
    assert done['fill']['realized_pnl']==-11.3
    book=isolated.load()
    assert not book['positions'] and book['free_cash']==988.7 and book['realized_pnl']==-11.3
    assert desk.load_config()['mode']=='live_manual'


@pytest.mark.parametrize('field,value',[
    ('bid',0),('bid',1.3),('bid',.5),('ask',float('nan')),('market_data_type',3),
    ('market_data_type',2),('halted',1),('halted',None),('halted',-1),('ask_size',0),('multiplier',10),
    ('con_id',None),('right','P'),('bid_observed_at',(NOW-timedelta(seconds=11)).isoformat()),
    ('ask_observed_at',(NOW+timedelta(seconds=1)).isoformat()),
])
def test_bad_quote_cannot_issue_or_fill_review(isolated,monkeypatch,field,value):
    r=isolated.preview(ticket())
    def bad(p):
        q=quote(p);q['legs'][0][field]=value;return q
    monkeypatch.setattr(isolated,'quote',bad)
    blocked=isolated.preview(ticket())
    assert not blocked['review_id'] and blocked['blockers']
    with pytest.raises(ValueError):isolated.fill(r['review_id'])
    assert isolated.load()['fills']==[]


@pytest.mark.parametrize('case',['paused','late','worse','contract','cash','count','session'])
def test_final_revalidation_after_quote_io(isolated,monkeypatch,case):
    r=isolated.preview(ticket())
    def changed(p):
        q=quote(p)
        if case=='paused':desk.save_config(dict(desk.load_config(),paper_research_enabled=False))
        if case=='late':monkeypatch.setattr(opt,'now_utc',lambda:NOW+timedelta(seconds=30));q=quote(p,NOW+timedelta(seconds=30))
        if case=='session':monkeypatch.setattr(opt,'now_utc',lambda:NOW.replace(hour=21));q=quote(p,NOW.replace(hour=21))
        if case=='worse':q['legs'][0]['ask']=1.21
        if case=='contract':q['legs'][0]['con_id']=999
        if case in ('cash','count'):
            b=isolated.load()
            if case=='cash':b['free_cash']=1
            else:b['positions']=[{'id':str(i)} for i in range(5)]
            desk._save_json(isolated.path,b)
        return q
    monkeypatch.setattr(isolated,'quote',changed)
    with pytest.raises(ValueError):isolated.fill(r['review_id'])
    assert isolated.load()['fills']==[]


def test_budget_cutoff_and_expiry_unresolved(isolated,monkeypatch):
    assert not isolated.preview(ticket(budget=100))['review_id']
    p=opt.plan(ticket(expiry='2026-09-23'))
    at=NOW.replace(hour=19,minute=30)
    assert opt.session_error(p,at) and not opt.session_error(p,at,closing=True)
    f=isolated.fill(isolated.preview(ticket())['review_id'])
    monkeypatch.setattr(opt,'now_utc',lambda:NOW+timedelta(days=3))
    assert isolated.status()['book']['positions'][0]['state']=='expired_unresolved'
    assert not isolated.preview(None,f['fill']['position_id'])['review_id']


def test_held_contract_is_rechecked_on_close(isolated,monkeypatch):
    f=isolated.fill(isolated.preview(ticket())['review_id'])
    def bad(p):
        q=quote(p);q['legs'][0]['con_id']=999;return q
    monkeypatch.setattr(isolated,'quote',bad)
    assert not isolated.preview(None,f['fill']['position_id'])['review_id']


def test_routes_reject_extra_fields_and_keep_local_book(isolated,monkeypatch):
    monkeypatch.setattr(desk._options_desk,'quote',quote)
    monkeypatch.setattr(desk._options_desk,'reviews',{})
    client=desk.app.test_client()
    r=client.post('/api/options/preview',base_url=BASE,json=ticket())
    assert r.status_code==200 and r.json['review_id']
    assert client.post('/api/options/paper-fill',base_url=BASE,json={'review_id':r.json['review_id'],'live':True}).status_code==400
    assert client.post('/api/options/paper-fill',base_url=BASE,json={'review_id':r.json['review_id']}).json['ok']
    assert client.get('/api/options',base_url=BASE).json['live_execution'] is False


@pytest.mark.parametrize('wrong_contract',[False,True])
def test_readonly_adapter_identity_and_subscription_cleanup(monkeypatch,wrong_contract):
    import eventkit
    subscriptions=[];cancelled=[]; requests=[]
    class IB:
        def qualifyContracts(self,c):
            c.conId=123
            if wrong_contract:c.right='P'
            return [c]
        def reqMktData(self,c,ticks,snapshot,regulatory):
            requests.append((ticks,snapshot,regulatory))
            t=NS(bid=1.1,ask=1.2,bidSize=10,askSize=10,halted=0,marketDataType=1,
                 modelGreeks=None,updateEvent=eventkit.Event(),ticks=[])
            subscriptions.append(t);return t
        def sleep(self,seconds):
            for t in subscriptions:
                t.ticks=[NS(tickType=i,time=datetime.now(timezone.utc)) for i in (1,2)]
                t.updateEvent.emit(t)
        def cancelMktData(self,c):cancelled.append(c.conId)
    @contextmanager
    def session():yield IB()
    monkeypatch.setattr(broker_ibkr,'_session',session)
    monkeypatch.setattr(broker_ibkr,'_identity',lambda ib: {'account_id':'FIXTURE','paper_mode':True})
    result=broker_ibkr.option_quotes(opt.legs(opt.plan(ticket())))
    if wrong_contract:
        assert not result['ok'] and not requests
    else:
        assert result['ok'] and result['legs'][0]['bid_observed_at'] and result['legs'][0]['ask_observed_at']
        assert requests==[('',False,False)] and cancelled==[123]
        assert len(subscriptions[0].updateEvent)==0
