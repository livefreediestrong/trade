import copy
from datetime import datetime, timezone
import json

import pytest
import app as desk
import overnight_study as study

NOW=datetime(2026,9,27,6,tzinfo=timezone.utc)
BASE='http://127.0.0.1:5056'


def params(**changes):
    return study.parameters(dict(symbols=['MU'],start='2026-09-21',end='2026-09-26',capital=1000,cost_bps=5,fill_pct=100) | changes,NOW)


def raw():
    days=['2026-09-18','2026-09-21','2026-09-22','2026-09-23','2026-09-24','2026-09-25']
    return {'chart':{'result':[{'meta':{'symbol':'MU','currency':'USD','instrumentType':'EQUITY','exchangeName':'NMS','exchangeTimezoneName':'America/New_York'},
        'timestamp':[int(datetime.fromisoformat(d+'T13:30:00+00:00').timestamp()) for d in days],
        'indicators':{'quote':[dict(open=[100,110,110,110,110,110],high=[100,120,120,120,120,120],low=[100,100,100,100,100,100],close=[100,120,120,120,120,120])],
                      'adjclose':[{'adjclose':[100,120,120,120,120,120]}]},'events':{}}]}}


def observation(payload=None, parameters=None, now=NOW):
    return study.parse('MU',payload or raw(),parameters or params(),now)


def test_components_reconcile_and_costs_are_each_side_per_trade():
    result=study.analyze(observation(),params())
    assert result['available'] and result['gross_reconciled'] and result['intervals']==5
    assert result['gross_buy_and_hold_pct']==pytest.approx(20)
    free,base,stress=result['scenarios']
    assert free['overnight']['compounded_pct']+100 > 0
    assert (1+free['overnight']['compounded_pct']/100)*(1+free['intraday']['compounded_pct']/100)==pytest.approx(1.2)
    assert base['buy_and_hold']['reinvested_pnl']==pytest.approx(1000*(1.2*.9995/1.0005-1))
    assert base['overnight']['reinvested_pnl'] < free['overnight']['reinvested_pnl']
    assert stress['overnight']['reinvested_pnl'] < base['overnight']['reinvested_pnl']


def test_fixed_dollars_are_not_reinvestment_and_partial_fill_keeps_cash_idle():
    result=study.measure([1.1,1.1],1000,0,100)
    assert result['fixed_dollar_pnl']==pytest.approx(200)
    assert result['reinvested_pnl']==pytest.approx(210)
    partial=study.measure([1.1,1.1],1000,0,50)
    assert partial['fixed_dollar_pnl']==pytest.approx(100)
    assert partial['reinvested_pnl']==pytest.approx(102.5)


def test_adjusted_open_prevents_mixed_dividend_price_basis_and_no_double_dividend():
    payload=raw(); data=payload['chart']['result'][0]
    data['indicators']['adjclose'][0]['adjclose'][0]=90
    data['events']={'dividends':{'1':{'date':data['timestamp'][1],'amount':10}}}
    obs=observation(payload); result=study.analyze(obs,params())
    assert obs['bars'][0]['adjusted_open']==90
    assert result['pairs'][0]['overnight_factor']==pytest.approx(110/90)
    assert result['pairs'][0]['adjustment_event']
    assert result['gross_buy_and_hold_pct']==pytest.approx((120/90-1)*100)
    assert obs['events'][0]['provider_record']['amount']==10


@pytest.mark.parametrize('field,value',[('open',None),('close',0),('low',121),('open',True),('high',float('inf')),('close',float('nan'))])
def test_missing_invalid_bar_never_bridges_a_session(field,value):
    payload=raw();payload['chart']['result'][0]['indicators']['quote'][0][field][2]=value
    # A real JSON response cannot contain NaN/Infinity. Other invalid bars yield explicit missing coverage.
    if isinstance(value,float) and not study.math.isfinite(value):
        with pytest.raises(ValueError):observation(payload)
    else:
        obs=observation(payload);result=study.analyze(obs,params())
        assert not result['available'] and '2026-09-22' in result['missing_dates']


@pytest.mark.parametrize('key,value',[('symbol','OTHER'),('currency','EUR'),('instrumentType','FUTURE'),('exchangeName','LSE'),('exchangeTimezoneName','Europe/London')])
def test_wrong_instrument_or_market_is_rejected(key,value):
    payload=raw();payload['chart']['result'][0]['meta'][key]=value
    with pytest.raises(ValueError,match='identity'):observation(payload)


def test_duplicate_dates_and_missing_adjustments_are_rejected():
    payload=raw();payload['chart']['result'][0]['timestamp'][2]=payload['chart']['result'][0]['timestamp'][1]
    with pytest.raises(ValueError,match='Duplicate'):observation(payload)
    payload=raw();del payload['chart']['result'][0]['indicators']['adjclose']
    with pytest.raises(ValueError,match='adjusted-close'):observation(payload)


def test_weekends_and_holiday_are_not_missing_sessions_and_early_close_buffer():
    start=study.date(2026,11,25);end=study.date(2026,11,30)
    before=datetime(2026,11,27,18,29,tzinfo=timezone.utc)
    after=datetime(2026,11,27,18,30,tzinfo=timezone.utc)
    assert study.sessions(start,end,before)==['2026-11-25']
    assert study.sessions(start,end,after)==['2026-11-25','2026-11-27']


def test_january_tenth_baseline_skips_special_market_closure():
    payload=raw();data=payload['chart']['result'][0]
    days=['2025-01-08','2025-01-10','2025-01-13','2025-01-14','2025-01-15','2025-01-16']
    data['timestamp']=[int(datetime.fromisoformat(d+'T14:30:00+00:00').timestamp()) for d in days]
    obs=observation(payload,params(start='2025-01-10',end='2025-01-16'))
    assert obs['complete'] and obs['bars'][0]['date']=='2025-01-08'


def test_missing_row_not_silently_replaced_with_zero():
    payload=raw();data=payload['chart']['result'][0]
    del data['timestamp'][2]
    for values in data['indicators']['quote'][0].values():del values[2]
    del data['indicators']['adjclose'][0]['adjclose'][2]
    result=study.analyze(observation(payload),params())
    assert not result['available'] and '2026-09-22' in result['missing_dates']


def test_missing_prior_close_blocks_the_entire_comparison():
    payload=raw();payload['chart']['result'][0]['indicators']['quote'][0]['close'][0]=None
    result=study.analyze(observation(payload),params())
    assert not result['available'] and result['missing_dates']==['2026-09-18']


def test_consistent_price_rescaling_does_not_create_a_split_profit():
    payload=raw();data=payload['chart']['result'][0]
    for field,values in data['indicators']['quote'][0].items():
        data['indicators']['quote'][0][field]=[v/10 for v in values]
    data['indicators']['adjclose'][0]['adjclose']=[v/10 for v in data['indicators']['adjclose'][0]['adjclose']]
    result=study.analyze(observation(payload),params())
    assert result['scenarios']==study.analyze(observation(),params())['scenarios']


def test_cache_observed_before_the_close_never_becomes_a_final_bar(monkeypatch):
    before=datetime(2026,9,25,19,59,tzinfo=timezone.utc)
    monkeypatch.setattr(study.market_catalog,'cached',lambda *a:dict(payload=raw(),retrieved_at=before.isoformat()))
    obs=study.fetch_observation('MU',params(),NOW)
    assert obs['expected_dates'][-1]=='2026-09-24' and obs['bars'][-1]['date']=='2026-09-24'
    assert obs['retrieved_at']==before.isoformat()


def test_buy_hold_drawdown_uses_daily_path_not_just_profitable_endpoint():
    obs=observation();obs['bars'][1]['adjusted_close']=200;obs['bars'][1]['adjusted_open']=110
    result=study.analyze(obs,params())
    assert result['scenarios'][0]['buy_and_hold']['compounded_pct']==pytest.approx(20)
    assert result['scenarios'][0]['buy_and_hold']['drawdown_pct']==pytest.approx(-40)


@pytest.mark.parametrize('changes',[{'symbols':['MU','MU']},{'symbols':['YF:EURUSD=X']},{'cost_bps':-1},{'capital':True},{'fill_pct':0},{'end':'2027-01-01'},{'start':'2020-01-01'},{'unexpected':True}])
def test_invalid_requests_are_rejected(changes):
    body={'symbols':['MU'],'start':'2026-09-21','end':'2026-09-26',**changes}
    with pytest.raises(ValueError):study.parameters(body,NOW)


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(desk,'DATA_DIR',tmp_path)
    monkeypatch.setattr(study,'fetch_observation',lambda s,p,n: observation(parameters=p))
    return desk.app.test_client()


def test_route_retains_replays_exports_and_deduplicates_without_changing_controls(client,tmp_path,monkeypatch):
    before=copy.deepcopy(desk.load_config())
    response=client.post('/api/research/overnight',json=params(),base_url=BASE)
    assert response.status_code==200,response.json
    item=response.json['study'];assert response.json['reused'] is False
    second=client.post('/api/research/overnight',json=params(),base_url=BASE)
    assert second.json['reused'] and second.json['study']['id']==item['id']
    monkeypatch.setattr(study,'fetch_observation',lambda *a:pytest.fail('Replay must not fetch revised history'))
    download=client.get('/api/research/overnight/'+item['id'],base_url=BASE)
    assert download.status_code==200 and download.json['identical']
    assert 'attachment' in download.headers['Content-Disposition']
    assert len(client.get('/api/research/overnight',base_url=BASE).json['studies'])==1
    assert desk.load_config()==before
    path=tmp_path/'overnight_studies.json';saved=json.loads(path.read_text());saved['studies'][0]['results'][0]['intervals']=999
    path.write_text(json.dumps(saved))
    assert client.get('/api/research/overnight',base_url=BASE).status_code==400


def test_concurrent_refresh_rejected_and_provider_failure_not_zero(client,monkeypatch):
    with study.LOCK:
        assert client.post('/api/research/overnight',json=params(),base_url=BASE).status_code==409
    monkeypatch.setattr(study,'fetch_observation',lambda *a:(_ for _ in ()).throw(ValueError('unavailable')))
    response=client.post('/api/research/overnight',json=params(),base_url=BASE)
    assert response.status_code==200 and response.json['study']['results'][0]['available'] is False


def test_optional_corruption_does_not_poison_execution_store(client,tmp_path):
    path=tmp_path/'overnight_studies.json';path.write_text('{bad')
    before=dict(desk._CORRUPT_PATHS)
    assert client.post('/api/research/overnight',json=params(),base_url=BASE).status_code==400
    assert path.read_text()=='{bad' and desk._CORRUPT_PATHS==before


def test_archive_limit_preserves_existing_evidence(client,tmp_path):
    response=client.post('/api/research/overnight',json=params(),base_url=BASE)
    assert response.status_code==200
    path=tmp_path/'overnight_studies.json';saved=json.loads(path.read_text())
    saved['studies']*=40;path.write_text(json.dumps(saved));before=path.read_bytes()
    response=client.post('/api/research/overnight',json=params(cost_bps=10),base_url=BASE)
    assert response.status_code==400 and 'Forty' in response.json['error'] and path.read_bytes()==before


def test_research_page_load_does_not_create_study(client,tmp_path):
    response=client.get('/desk/research',base_url=BASE)
    assert response.status_code==200 and b'overnight-study' in response.data and b'overnight_study.js' in response.data
    assert not (tmp_path/'overnight_studies.json').exists()


@pytest.mark.parametrize('silent',[True,False])
def test_failed_storage_is_not_reported_as_saved(client,monkeypatch,silent):
    def fail(*a):
        if not silent:raise OSError('private filesystem detail')
    monkeypatch.setattr(desk,'_save_json',fail)
    response=client.post('/api/research/overnight',json=params(),base_url=BASE)
    assert response.status_code==503 and response.json['ok'] is False
    assert 'private filesystem' not in response.json['error']
