import copy
import datetime as dt
import json
import socket
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import app as desk
import broker_ibkr
import moss_paper as worker
import moss_policy as policy
import paper_loop
import real_trade_journal as real
import screener_logic

NOW=dt.datetime(2026,9,23,18,tzinfo=dt.timezone.utc)
BASE='http://127.0.0.1:5056'


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(desk,'DATA_DIR',tmp_path)
    for name in ('CONFIG','LEDGER','SIGNALS','JOURNAL','DECISIONS','LESSONS'):
        monkeypatch.setattr(desk,name+'_PATH',tmp_path/(name.lower()+'.json'))
    monkeypatch.setattr(desk,'_CORRUPT_PATHS',set())
    monkeypatch.setattr(desk,'_QUOTE_SNAPSHOTS',{})
    monkeypatch.setattr(desk,'_decision_ring',paper_loop.DecisionRing(desk.DECISIONS_PATH))
    monkeypatch.setattr(socket.socket,'connect',lambda *a,**k:pytest.fail('Network forbidden'))
    monkeypatch.setattr(desk,'live_broker_place_order',lambda *a,**k:pytest.fail('Broker order forbidden'))
    monkeypatch.setattr(desk,'execute_gated_broker_or_paper',lambda *a,**k:pytest.fail('Broker route forbidden'))
    monkeypatch.setattr(worker,'now_utc',lambda:NOW)
    cfg=desk.load_config()
    cfg.update(mode='live_manual',session_active=False,paper_research_enabled=True,paper_auto_approve=True,
               moss_paper=dict(policy.DEFAULTS,enabled=True,symbols=['AAA','BBB']),paper_equity=1000,paper_fractional_enabled=True)
    desk.save_config(cfg)
    desk.save_ledger(dict(desk.load_ledger(),cash=1000.,equity=1000.,positions=[],fills=[]))
    return desk.app.test_client()


def quote(stamp=NOW,price=100):
    return dict(price=price,market_time=stamp.isoformat(),fresh=True,source='recorded market feed')


def analysis(symbol='AAA',now=NOW):
    return dict(ticker=symbol,price=100,quote=quote(now),verdict='PASS',checks={},sources=['recorded'],
                volume={'avg_30d':1_000_000},entry_quality={'label':'early'},
                intraday={'fresh':True,'completed_bar_at':(now-dt.timedelta(minutes=5)).isoformat(),
                          'realized_volatility_pct':.8,'vwap':99})


def event(i,**changes):
    start=dt.datetime(2026,9,23,13,40,tzinfo=dt.timezone.utc)+dt.timedelta(minutes=11*i)
    end=start+dt.timedelta(minutes=10)
    return dict(dict(id=f'row-{i}',ts=start.isoformat(),outcome_ts=end.isoformat(),outcome_market_time=end.isoformat(),
        quote=quote(start),outcome_quote=quote(end,100.3),ticker='AAA',source='moss_paper',moss_policy_version=policy.VERSION,moss_personality='empirical_bayes',
        moss_setup='above_vwap',moss_quality={'ok':True},horizon_min=10,intended_side='buy',llm_model='actual-model',
        prompt_version='v1',input_hash=f'hash-{i}',scoring_version='horizon-net-v2',outcome_status='scored',
        outcome_executable_move_bps=20,slip_bps=5.,fee_bps=1.,mid=100,outcome_mid=100.3),**changes)


def test_shrinkage_thresholds_and_empirical_prior():
    at=dt.datetime(2026,9,23,20,tzinfo=dt.timezone.utc)
    weights={n:policy.fit([event(i) for i in range(n)],at)['weights'][0] for n in (5,9,10,19,20)}
    assert weights[5]['alpha_fraction'] < weights[9]['alpha_fraction'] < weights[10]['alpha_fraction'] < weights[19]['alpha_fraction'] < 1
    assert weights[5]['baseline_win_rate'] < weights[5]['posterior_win_rate'] < 1
    assert weights[20]['alpha_fraction']==1 and weights[20]['posterior_win_rate']==1
    assert weights[20]['paper_growth_fraction']==1 and weights[19]['paper_growth_fraction']==0
    assert weights[20]['prior_source']=='neutral_fallback'
    peers=[event(i,id=f'{sym}-{i}',ticker=sym,outcome_executable_move_bps=10 if i%5 else -10) for sym in ('BBB','CCC') for i in range(10)]
    fitted=policy.fit([event(i) for i in range(5)]+peers,at)
    a=next(w for w in fitted['weights'] if w['ticker']=='AAA')
    assert a['prior_source']=='empirical_peers' and a['baseline_win_rate']==.8
    assert policy.fit([event(i,outcome_executable_move_bps=-1000 if i==0 else 1) for i in range(20)],at)['weights'][0]['paper_growth_fraction']==0


@pytest.mark.parametrize('count,reference', [(5,.33333333333333),(9,.47368421052632),(10,.5),(15,.8),(19,.96551724137931),(20,1.)])
def test_wolfram_reference_policy_weights(count, reference):
    # Independent Wolfram Language arithmetic, retained in WOLFRAM_MATH_AUDIT.md.
    at=dt.datetime(2026,9,23,20,tzinfo=dt.timezone.utc)
    row=policy.fit([event(i) for i in range(count)],at)['weights'][0]
    assert row['alpha_fraction']==pytest.approx(reference,abs=1e-12)


def test_wolfram_reference_wilson_and_policy_distinction():
    at=dt.datetime(2026,9,23,20,tzinfo=dt.timezone.utc)
    rows=[event(i,outcome_executable_move_bps=20 if i%10<7 else -20) for i in range(20)]
    row=policy.fit(rows,at)['weights'][0]
    assert row['win_rate_lower_95']==pytest.approx(.48102322377102,abs=1e-12)
    assert row['win_rate_upper_95']==pytest.approx(.85452472603101,abs=1e-12)
    # This legacy field is the policy-adjusted score, not the pure Beta posterior.
    assert row['posterior_win_rate']==.7
    assert (14+row['prior_alpha'])/(20+row['prior_alpha']+row['prior_beta'])==pytest.approx(.63333333333333)
    assert row['paper_growth_fraction']==0, '70% observed wins is not enough for the scaling gate'


@pytest.mark.parametrize('changes',[
    {'mock':True},{'routed':'cheap'},{'error':'data_error'},{'execution_block':'audit_failed'},
    {'input_hash':None},{'outcome_ts':'2026-09-24T14:00:00+00:00'},
    {'quote':quote(dt.datetime(2026,9,23,13,40,1,tzinfo=dt.timezone.utc))},
    {'outcome_market_time':'2026-09-23T21:00:00+00:00'},
    {'moss_execution_attempted':True}, {'moss_fill':{'latency_sec':31}},
    {'moss_execution_error':'stale_execution'}, {'outcome_mid':-1}, {'outcome_quote':dict(quote(),source='mock')},
    {'moss_max_quote_age_sec':'NaN'}, {'moss_fill':{'latency_sec':1,'ts':'2027-01-01T15:00:00Z','price':100,'shares':1}}])
def test_strict_evidence_exclusions(changes):
    result=policy.fit([event(0,**changes)],NOW)
    assert result['qualified_samples']==0 and sum(result['excluded'].values())==1


def test_overlapping_samples_and_asof_training():
    first=event(0)
    overlap=event(0,id='overlap',ts='2026-09-23T13:41:00+00:00',quote=quote(dt.datetime(2026,9,23,13,41,tzinfo=dt.timezone.utc)),outcome_ts='2026-09-23T13:51:00+00:00',outcome_market_time='2026-09-23T13:51:00+00:00')
    overlap['outcome_quote']=quote(dt.datetime(2026,9,23,13,51,tzinfo=dt.timezone.utc),100.3)
    result=policy.fit([first,overlap,first,event(1)],NOW)
    assert result['qualified_samples']==2
    assert result['excluded']['overlapping_ticker_horizon']==1 and result['excluded']['duplicate_or_missing_id']==1
    assert policy.fit([first],dt.datetime(2026,9,23,13,49,tzinfo=dt.timezone.utc))['qualified_samples']==0


def test_completed_bar_volatility_rejects_erroneous_ticks_and_early_close():
    idx=pd.date_range('2026-09-23 13:25',periods=7,freq='5min',tz='UTC')
    df=pd.DataFrame({'High':[101]*7,'Low':[99]*7,'Close':[100,100.2,99.9,100.3,100.1,100.4,100.2],'Volume':[10000]*7},index=idx)
    r=screener_logic.intraday_signals(df,atr_usd=2,now=dt.datetime(2026,9,23,14,tzinfo=dt.timezone.utc))
    assert r['realized_volatility_pct']>0 and r['realized_volatility_bars']==5
    df.iloc[-1,df.columns.get_loc('Close')]=1000
    assert screener_logic.intraday_signals(df,atr_usd=2,now=dt.datetime(2026,9,23,14,tzinfo=dt.timezone.utc))['unavailable_reason']=='erroneous_bars'
    assert policy.session(dt.date(2026,11,26))==(None,None)
    assert policy.session(dt.date(2026,11,27))[1].hour==13


def test_configuration_only_changes_paper_fields_and_can_pause(isolated):
    before=desk.load_config()
    result=isolated.post('/api/companion/paper',base_url=BASE,json=dict(policy.DEFAULTS,enabled=True,personality='empirical_bayes'))
    assert result.status_code==200
    after=desk.load_config()
    for key in before:
        if key not in ('moss_paper','paper_research_enabled','paper_auto_approve','paper_fractional_enabled'):
            assert after[key]==before[key]
    assert isolated.post('/api/companion/paper',base_url=BASE,json={'enabled':True,'mode':'auto_live'}).status_code==400
    isolated.post('/api/companion/paper',base_url=BASE,json={'enabled':False})
    assert not desk.load_config()['paper_auto_approve'] and desk.load_config()['mode']=='live_manual'


def test_paper_workday_calls_only_local_fill_and_records_attempt(isolated,monkeypatch):
    monkeypatch.setattr(screener_logic,'analyze_ticker',lambda symbol:analysis(symbol))
    monkeypatch.setattr(desk.llm_trader,'model_cost_today',lambda:{'model_usd':0})
    cfg_before=copy.deepcopy(desk.load_config())
    fills=[]
    def thesis(a,cfg,**kwargs):
        assert cfg['mode']=='auto_paper' and cfg['_moss_paper'] and kwargs['source']=='moss_paper'
        e=desk._decision_ring.append(dict(event(0),ticker=a['ticker'],outcome_status='pending'))
        return {'side':'buy','confidence':.8,'llm_model':'actual-model','prompt_version':'v1','brain_mode':'gemini','decision_record_id':e['id'],'advisory_size_mult':.5}
    monkeypatch.setattr(desk,'_research_thesis',thesis)
    def fill(sig,cfg,source):
        assert sig['workspace']=='paper' and cfg['mode']=='auto_paper'
        fills.append(copy.deepcopy(sig))
        return {'ok':True,'fill':{'id':'paper-fill','ts':NOW.isoformat(),'price':100,'shares':.049,'fee_usd':.001}}
    monkeypatch.setattr(desk,'paper_fill',fill)
    engine=worker.PaperWorkday(desk);engine._work()
    assert len(fills)==1 and 2.4 < fills[0]['suggested_shares']*100 <= 2.5
    assert desk.load_config()==cfg_before
    rows=worker.history(desk)
    assert rows[0]['moss_fill']['id']=='paper-fill'
    assert engine.load()['days']['2026-09-23']['model_calls']==1


def test_budget_stop_keeps_observations_and_no_ai_call(isolated,monkeypatch):
    monkeypatch.setattr(screener_logic,'analyze_ticker',lambda symbol:analysis(symbol))
    monkeypatch.setattr(desk.llm_trader,'model_cost_today',lambda:{'model_usd':99})
    monkeypatch.setattr(desk,'_research_thesis',lambda *a,**k:pytest.fail('Budget exceeded'))
    engine=worker.PaperWorkday(desk);engine._work()
    progress=engine.load()['days']['2026-09-23']
    assert progress['qualified_candidates']==2 and progress['model_calls']==0
    assert len(list((desk.DATA_DIR/'moss_observations').glob('*.jsonl')))==1


def test_closed_market_and_restart_deduplicate_cycles(isolated,monkeypatch):
    engine=worker.PaperWorkday(desk)
    monkeypatch.setattr(desk,'check_decision_outcomes',lambda:[])
    monkeypatch.setattr(engine,'close_due',lambda *a:None)
    monkeypatch.setattr(engine,'close_report',lambda *a:None)
    now=dt.datetime(2026,11,26,15,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(worker,'now_utc',lambda:now)
    engine.tick();assert not engine.busy
    now=NOW
    engine.save({'days':{},'cursor':0,'last_cycle_at':NOW.isoformat()})
    replacement=worker.PaperWorkday(desk)
    monkeypatch.setattr(replacement,'close_due',lambda *a:None)
    monkeypatch.setattr(replacement,'close_report',lambda *a:None)
    replacement.tick();assert not replacement.busy


def test_final_entry_gate_catches_latency_revocation_future_ticks_and_overlap(isolated):
    cfg=desk.paper_research_config(desk.load_config())
    sig={'ticker':'AAA','moss_config_hash':policy.fingerprint(policy.settings(cfg)),
         'decision_completed_at':NOW.isoformat(),'llm_model':'actual-model','side':'buy','verdict':'PASS'}
    ledger=desk.load_ledger()
    assert worker.entry_error(sig,cfg,ledger,quote(),NOW) is None
    assert worker.entry_error(sig,cfg,ledger,quote(NOW+dt.timedelta(seconds=1)),NOW)=='future_quote'
    assert worker.entry_error(sig,cfg,ledger,quote(NOW+dt.timedelta(seconds=31)),NOW+dt.timedelta(seconds=31))=='stale_execution'
    assert worker.entry_error(sig,dict(cfg,paper_auto_approve=False),ledger,quote(),NOW)
    assert worker.entry_error(sig,cfg,dict(ledger,positions=[{'ticker':'AAA','shares':1}]),quote(),NOW)


def execution(identifier='trade.01',**changes):
    return dict(dict(execution_id=identifier,account_id='U1234',paper_mode=False,verified=True,
         ts='2026-09-23T14:00:00+00:00',ticker='TEST',side='buy',shares=1.,price=100.,currency='USD',
         commission_currency=None,commission=None,broker_realized_pnl=None,permanent_order_id=123,
         source='IBKR execution report'),**changes)


def test_actual_trade_journal_dedup_late_fees_corrections_and_account_isolation(isolated):
    journal=real.TradeJournal(desk)
    response={'ok':True,'identity':{'account_id':'U1234','paper_mode':False},'executions':[execution()]}
    journal.merge(response);journal.merge(response)
    assert journal.status()['real']['executions']==1 and journal.status()['real']['by_currency']['USD']['fees_missing']==1
    journal.merge(dict(response,executions=[execution(commission=.35,commission_currency='USD',broker_realized_pnl=2)]))
    journal.merge(response)
    assert journal.status()['real']['by_currency']['USD']['fees_reported']==.35
    journal.merge(dict(response,executions=[execution('trade.02',commission=.4,commission_currency='USD',broker_realized_pnl=3),execution('foreign.01',account_id='U9999')]))
    status=journal.status()
    assert status['real']['executions']==1 and status['real']['by_currency']['USD']['broker_realized_pnl']==3
    assert len(journal.load()['executions'])==2 and status['excluded']==1
    journal.merge({'ok':True,'identity':{'account_id':'DU1234','paper_mode':True},'executions':[execution('paper.01',account_id='DU1234',paper_mode=True,broker_realized_pnl=500)]})
    assert journal.status()['real']['executions']==0
    assert next(a for a in journal.status()['accounts'] if a['paper_mode'] is False)['review']['by_currency']['USD']['broker_realized_pnl']==3
    assert journal.status()['broker_paper']['by_currency']['USD']['broker_realized_pnl']==500
    journal.merge({'ok':False,'error':'Gateway offline'})
    assert journal.status()['real']['executions']==0 and journal.status()['error']=='Gateway offline'


def test_ibkr_history_reads_actual_execution_and_preserves_unknown_commission(monkeypatch):
    stamp=dt.datetime(2026,9,23,14,tzinfo=dt.timezone.utc)
    ex=NS(execId='trade.01',acctNumber='U1234',time=stamp,shares=0.5,price=100,side='BOT',exchange='NYSE',orderId=1,clientId=37,permId=9,orderRef='manual')
    fill=NS(execution=ex,contract=NS(symbol='TEST',conId=123,secType='STK',currency='USD'),
            commissionReport=NS(execId='',commission=0,currency='',realizedPNL=0))
    calls=[]
    fake=NS(reqExecutions=lambda f:(calls.append(f.acctCode) or [fill]),sleep=lambda sec:None)
    @contextmanager
    def session():yield fake
    monkeypatch.setattr(broker_ibkr,'_session',session)
    monkeypatch.setattr(broker_ibkr,'_identity',lambda ib:{'account_id':'U1234','paper_mode':False})
    result=broker_ibkr.execution_history.__wrapped__()
    assert result['ok'] and calls==['U1234']
    assert result['executions'][0]['shares']==.5 and result['executions'][0]['commission'] is None
    fill.commissionReport=NS(execId='trade.01',commission=.1,currency='USD',realizedPNL=float('inf'))
    result=broker_ibkr.execution_history.__wrapped__()
    assert result['executions'][0]['commission']==.1 and result['executions'][0]['broker_realized_pnl'] is None


def freeze_fill_clock(monkeypatch):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(desk,'datetime',Clock)
    monkeypatch.setattr(paper_loop,'datetime',Clock)
    import data_sources
    monkeypatch.setattr(data_sources,'datetime',Clock)
    monkeypatch.setattr(desk,'_MARKS',{})
    def price(symbol):
        desk._QUOTE_SNAPSHOTS[symbol]=quote()
        return 100.
    monkeypatch.setattr(desk,'fetch_last_price',price)
    return price


def moss_signal(cfg):
    return dict(id='moss-fill',workspace='paper',side='buy',ticker='AAA',signal_price=100.,suggested_shares=.049,
                quote=quote(),confidence=.9,verdict='PASS',moss_policy_version=policy.VERSION,
                moss_config_hash=policy.fingerprint(policy.settings(cfg)),decision_completed_at=NOW.isoformat(),
                moss_budget=5.,decision_record_id='decision-1')


def test_real_paper_ledger_entry_exit_and_revocation(isolated,monkeypatch):
    price=freeze_fill_clock(monkeypatch)
    cfg=desk.load_config();pcfg=desk.paper_research_config(cfg)
    sig=moss_signal(cfg)
    low=desk.paper_fill(dict(sig,confidence=.1),pcfg,'auto_paper')
    assert not low['ok'] and 'confidence' in low['error'] and not desk.load_ledger()['fills']
    result=desk.paper_fill(sig,pcfg,'auto_paper')
    assert result['ok'],result
    ledger=desk.load_ledger();held=ledger['positions'][0]
    assert 0 < held['moss_owned_shares']==held['shares'] < .05
    assert result['fill']['notional']+result['fill']['fee_usd']<=5
    assert not ledger.get('broker_fills') and not ledger.get('pending_broker_orders')
    assert not desk.paper_fill(sig,pcfg,'auto_paper')['ok']
    ledger['positions'][0]['moss_exit_at']=(NOW-dt.timedelta(seconds=1)).isoformat()
    desk.save_ledger(ledger)
    engine=worker.PaperWorkday(desk)
    # A quote can turn stale between the worker's first read and the ledger write.
    calls=[]
    def delayed(symbol):
        calls.append(symbol)
        result=price(symbol)
        if len(calls)>1:desk._QUOTE_SNAPSHOTS[symbol]=quote(NOW-dt.timedelta(seconds=31))
        return result
    monkeypatch.setattr(desk,'fetch_last_price',delayed)
    engine.close_due(cfg,NOW)
    assert desk.load_ledger()['positions'] and len(desk.load_ledger()['fills'])==1
    # Pausing new entries still permits a fresh reducing exit of Moss's holding.
    desk.save_config(dict(cfg,paper_auto_approve=False,paper_research_enabled=False))
    monkeypatch.setattr(desk,'fetch_last_price',price)
    engine.close_due(desk.load_config(),NOW)
    assert not desk.load_ledger()['positions'] and len(desk.load_ledger()['fills'])==2
    desk.save_config(cfg)
    def revoke(symbol):
        result=price(symbol)
        desk.save_config(dict(cfg,paper_auto_approve=False))
        return result
    monkeypatch.setattr(desk,'fetch_last_price',revoke)
    result=desk.paper_fill(sig,pcfg,'auto_paper')
    assert not result['ok'] and 'turned off' in result['error']
    assert len(desk.load_ledger()['fills'])==2 and desk.load_config()['mode']=='live_manual'


def test_daily_report_restart_idempotent_and_missing_close_stays_visible(isolated,monkeypatch):
    freeze_fill_clock(monkeypatch)
    engine=worker.PaperWorkday(desk)
    cfg=desk.load_config();result=desk.paper_fill(moss_signal(cfg),desk.paper_research_config(cfg),'auto_paper')
    assert result['ok'],result
    engine.save({'days':{'2026-09-23':{'cycles':1,'paper_fills':1}},'cursor':0})
    after=dt.datetime(2026,9,23,20,3,tzinfo=dt.timezone.utc)
    engine.close_report(after)
    path=desk.DATA_DIR/'moss_reports'/'2026-09-23.json';saved=path.read_bytes()
    report=json.loads(saved)
    assert len(report['remaining_moss_positions'])==1 and report['evaluation']['qualified_samples']==0
    worker.PaperWorkday(desk).close_report(after+dt.timedelta(days=1))
    assert path.read_bytes()==saved and desk.load_ledger()['positions']


def test_pausing_collection_prevents_paid_request(isolated,monkeypatch):
    cfg=desk.load_config()
    def collect(symbol):
        desk.save_config(dict(cfg,paper_auto_approve=False))
        return analysis(symbol)
    monkeypatch.setattr(screener_logic,'analyze_ticker',collect)
    monkeypatch.setattr(desk,'_research_thesis',lambda *a,**kw:pytest.fail('Paused during collection'))
    engine=worker.PaperWorkday(desk);engine._work()
    assert engine.load()['days']['2026-09-23']['model_calls']==0


def test_empirical_weights_change_research_priority_with_matching_model(isolated,monkeypatch):
    cfg=desk.load_config();cfg['moss_paper']['personality']='empirical_bayes';desk.save_config(cfg)
    rows=[event(i,id=f'{sym}-{i}',ticker=sym,outcome_executable_move_bps=20 if sym=='BBB' else -20)
          for sym in ('AAA','BBB') for i in range(20)]
    monkeypatch.setattr(worker,'history',lambda desk:rows)
    monkeypatch.setattr(screener_logic,'analyze_ticker',lambda symbol:analysis(symbol))
    monkeypatch.setattr(desk,'_llm_public_status',lambda cfg:{'model':'actual-model'})
    monkeypatch.setattr(desk.llm_trader,'PROMPT_VERSION','v1')
    monkeypatch.setattr(desk.llm_trader,'model_cost_today',lambda:{'model_usd':0})
    selected=[]
    def thesis(a,cfg,**kw):
        selected.append(a['ticker'])
        row=desk._decision_ring.append(dict(event(0),outcome_status='pending'))
        return {'side':'hold','decision_record_id':row['id']}
    monkeypatch.setattr(desk,'_research_thesis',thesis)
    worker.PaperWorkday(desk)._work()
    assert selected==['BBB']
