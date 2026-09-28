"""Actual desk submission path, cumulative fills and exposure changes; no network."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import app as desk
import auto_live_options as auto
import broker_router as router
import broker_ibkr as ibkr
import live_agent as agent
import option_lifecycle as life
import option_review
from test_execution_repairs import execution  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_broker_lifecycle import gateway  # noqa: F401


@pytest.fixture
def options(live, monkeypatch):
    cfg = desk.load_config()
    cfg['live_agent']['auto_options'] = auto.validate(dict(enabled=True, automatic_exits=True, max_contracts=2))
    cfg['alert_cooldown_sec'] = 0
    desk.save_config(cfg)
    env = NS(live=live, cfg=cfg, positions=[], orders=[], fill_qty=2, fill_price=1., fill_terminal=True,
             bid=1.6, ask=1.65, quote_type=1)
    def snap(identity):
        return dict(ok=True, identity=copy.deepcopy(identity), checked_at=life.now_utc().isoformat(),
                    positions=copy.deepcopy(env.positions), orders=copy.deepcopy(env.orders))
    env.snapshot = snap
    monkeypatch.setitem(router.__dict__, 'option_lifecycle_snapshot', snap)
    def quote(specs):
        return dict(ok=True, source='IBKR streaming bid/ask',identity=copy.deepcopy(live.identity),received_at=life.now_utc().isoformat(), legs=[
            dict(**p,con_id=100 if p['strike']==100 else 105, multiplier=100, market_data_type=env.quote_type,
                 halted=0,bid_size=10,ask_size=10,bid_observed_at=life.now_utc().isoformat(),ask_observed_at=life.now_utc().isoformat(),
                 bid=env.bid if p['strike']==100 else .3, ask=env.ask if p['strike']==100 else .35)
            for p in specs])
    monkeypatch.setitem(router.__dict__, 'option_quotes', quote)
    def fill(*a, **kw):
        return dict(state='filled' if env.fill_terminal else 'partially_filled', terminal=env.fill_terminal,
                    filled_qty=env.fill_qty, filled_avg_price=env.fill_price)
    monkeypatch.setitem(router.__dict__, 'wait_for_fill', fill)
    def send(order):
        live.sent.append(copy.deepcopy(order))
        return dict(ok=True, broker='ibkr', order_id='option-'+str(len(live.sent)), status='paper_submitted', paper_mode=True)
    monkeypatch.setattr(desk, 'live_broker_place_order', send)
    return env


def signal(env, strategy='long_call', qty=2):
    now = life.now_utc()
    bag = '_' in strategy and strategy.split('_')[1] in ('debit','credit')
    short = strategy.startswith('short_')
    credit = strategy.endswith('credit')
    row = dict(id='entry', ticker='TEST', source='live_agent', workspace='live', status='pending', verdict='PASS',
               confidence=.9, side='sell' if short or credit else 'buy', suggested_shares=qty, contracts=qty,
               asset_type='BAG' if bag else 'OPT', right='C', expiry='2030-10-18', strike=100.,
               long_strike=100., short_strike=105., option_strategy=strategy,
               option_intent='OPEN' if bag else 'STO' if short else 'BTO', allow_naked_short=short,
               signal_price=1., quote=dict(price=1., market_time=now.isoformat(), source='ibkr_live', fresh=True),
               ts=now.isoformat(), expires_at=(now+timedelta(seconds=30)).isoformat(),
               agent_identity=copy.deepcopy(env.live.identity), agent_revision='policy-1', agent_run_id='run-1',
               agent_exit_policy=copy.deepcopy(env.live.policy),
               review_contract=dict(con_id=100, multiplier=100, symbol='TEST', sec_type='BAG' if bag else 'OPT'))
    if bag:
        row['review_contract']['legs'] = [dict(con_id=100,strike=100.),dict(con_id=105,strike=105.)]
    return row


def held(cid=100, qty=2, asset='OPT', **patch):
    return dict(symbol='TEST', con_id=cid, asset_type=asset, currency='USD', qty=qty,
                multiplier=100 if asset=='OPT' else None, right='C', strike=float(cid), expiry='20301018', **patch)


def entered(env, strategy='long_call'):
    sig = signal(env, strategy)
    result = desk._ingest_one_signal(sig, cfg_override=env.cfg)
    assert result.get('fill'), result.get('reject_reason')
    row = env.live.service.load()['option_positions']['entry']
    env.positions = [held(p['con_id'], row['remaining']*p['sign']) for p in row['legs']]
    return row


def test_real_pipeline_entry_exit_restart_and_paper_book_untouched(options):
    row = entered(options)
    assert row['remaining']==2 and row['entry_price']==1
    assert options.live.sent[0]['option_lifecycle_guard']['options']=={}
    assert life.manage_exits(options.live.service, desk.load_config()) is True
    order = options.live.sent[-1]
    assert order['option_intent']=='STC' and order['contracts']==2 and order['type']=='limit'
    assert order['option_lifecycle_guard']['options']=={'100':2}
    assert options.live.service.load()['option_positions']['entry']['remaining']==0
    assert not desk.load_ledger().get('fills')
    restarted=agent.LiveAgent(desk)
    life.sync_ledger(restarted)
    assert restarted.load()['option_positions']['entry']['remaining']==0


def test_close_ignores_new_entry_budget_and_loss_limits(options, monkeypatch):
    entered(options)
    cfg=desk.load_config(); cfg['live_agent']['policy'].update(max_order_usd=.01, max_orders_per_day=1, max_daily_loss_usd=1.)
    desk.save_config(cfg)
    monkeypatch.setattr(desk,'_broker_day_pnl',lambda:(-1000.,100000.,None))
    assert life.manage_exits(options.live.service,cfg)
    assert len(options.live.sent)==2 and options.live.sent[-1]['contracts']==2


def test_partial_close_counts_once_and_does_not_resubmit_pending(options):
    entered(options)
    options.fill_qty=1; options.fill_terminal=False
    assert life.manage_exits(options.live.service, options.cfg)
    assert options.live.service.load()['option_positions']['entry']['remaining']==1
    life.sync_ledger(options.live.service); life.sync_ledger(options.live.service)
    assert options.live.service.load()['option_positions']['entry']['remaining']==1
    assert life.manage_exits(options.live.service,options.cfg)
    assert len(options.live.sent)==2 and desk.load_ledger()['pending_broker_orders']


@pytest.mark.parametrize('change',['missing_leg','extra_stock','partial_assignment','other_option','adjusted','opposite'])
def test_unexplained_exposure_is_held_and_never_sold_as_stock(options,change):
    entered(options,'call_debit')
    if change=='missing_leg': options.positions.pop()
    elif change=='extra_stock': options.positions.append(held(42,100,'STK'))
    elif change=='partial_assignment': options.positions[-1]['qty']=-1; options.positions.append(held(42,-100,'STK'))
    elif change=='other_option': options.positions.append(held(110,1))
    elif change=='adjusted': options.positions[0]['multiplier']=10
    elif change=='opposite': options.positions[0]['qty']=-2
    assert life.monitor(options.live.service,options.cfg,force=True)
    row=options.live.service.load()['option_positions']['entry']
    assert row['hold'] and row['remaining']==2 and row['events']
    assert life.manage_exits(options.live.service,options.cfg)
    assert len(options.live.sent)==1
    options.positions=[held(100,2),held(105,-2)]
    assert life.monitor(agent.LiveAgent(desk),options.cfg,force=True)  # sticky even if exposure changes back


@pytest.mark.parametrize('patch',[{'ok':False},{'identity':{}},{'positions':None},{'orders':None},
                                 {'checked_at':'2001-01-01T00:00:00+00:00'}])
def test_bad_broker_evidence_keeps_position(options,monkeypatch,patch):
    before=entered(options)
    snap=options.snapshot(options.live.identity); snap.update(patch)
    monkeypatch.setitem(router.__dict__,'option_lifecycle_snapshot',lambda identity:snap)
    assert life.monitor(options.live.service,options.cfg,force=True)
    row=options.live.service.load()['option_positions']['entry']
    assert row['remaining']==before['remaining'] and len(options.live.sent)==1


@pytest.mark.parametrize('which',['pause','revision','account','session'])
def test_change_during_snapshot_prevents_exit(options,monkeypatch,which):
    entered(options)
    calls=[]
    def changing(identity):
        calls.append(1)
        if len(calls)==2: # monitor first, then execution preflight
            cfg=desk.load_config()
            if which=='pause': cfg['live_agent']['enabled']=False
            elif which=='revision': cfg['live_agent']['revision']='new'
            elif which=='account': cfg['broker_identity']['account_id']='OTHER'
            else: cfg['session_active']=False
            desk.save_config(cfg)
        return options.snapshot(identity)
    monkeypatch.setitem(router.__dict__,'option_lifecycle_snapshot',changing)
    life.manage_exits(options.live.service,options.cfg)
    assert len(options.live.sent)==1


@pytest.mark.parametrize('field,value',[('bid',0),('bid',2),('ask',5),('quote_type',3),('bid',float('nan'))])
def test_nonexecutable_quotes_do_not_send(options,field,value):
    entered(options); setattr(options,field,value)
    assert life.manage_exits(options.live.service,options.cfg)
    assert len(options.live.sent)==1


def test_disabling_new_option_entries_does_not_disable_existing_exits(options):
    entered(options)
    cfg=desk.load_config(); cfg['live_agent']['auto_options']['enabled']=False; desk.save_config(cfg)
    life.manage_exits(options.live.service,cfg)
    assert len(options.live.sent)==2


def test_opt_in_paper_feature_does_not_disable_legacy_live_controls(options):
    cfg=desk.load_config(); cfg['live_agent']['auto_options']['automatic_exits']=False
    cfg['broker_identity']['paper_mode']=False
    desk.save_config(cfg)
    assert life.manage_exits(options.live.service,cfg) is False
    assert desk.load_config()['live_agent']['enabled'] is True


def test_new_feature_rejects_real_account_without_changing_controls(options):
    cfg=desk.load_config(); cfg['broker_identity']['paper_mode']=False; desk.save_config(cfg)
    assert life.manage_exits(options.live.service,cfg)
    assert desk.load_config()['live_agent']['enabled'] is True and not options.live.sent


@pytest.mark.parametrize('strategy,close,side',[('call_debit','CLOSE','sell'),('call_credit','CLOSE','buy'),('short_call','BTC','buy')])
def test_close_intent_preserves_whole_structure(options,strategy,close,side):
    # Use direct prepared ownership + confirmed ledger receipt to avoid pretending
    # the existing strike-based short-entry budget covers a $10,000 exposure.
    sig=signal(options,strategy)
    life.prepare(options.live.service,options.cfg,sig,options.snapshot(options.live.identity))
    ledger=desk.load_ledger();ledger['broker_fills']=[dict(signal_id='entry',confirmed=True,price_estimated=False,shares=2,price=1.)]
    desk.save_ledger(ledger);life.sync_ledger(options.live.service)
    row=options.live.service.load()['option_positions']['entry']
    options.positions=[held(p['con_id'],row['remaining']*p['sign']) for p in row['legs']]
    if strategy=='call_debit': options.bid=2.;options.ask=2.05
    elif strategy=='call_credit': options.bid=.8;options.ask=.85  # close credit formula is ask(short)-bid(long); negative -> refused
    elif strategy=='short_call': options.bid=.4;options.ask=.45
    if strategy=='call_credit':
        with pytest.raises(ValueError,match='positive executable'): life.closing_quote(row)
        return
    assert life.manage_exits(options.live.service,options.cfg)
    assert options.live.sent[-1]['option_intent']==close and options.live.sent[-1]['side']==side


@pytest.mark.parametrize('expiry,expected',[('2026-11-27','2026-11-25T15:30'),('2026-11-30','2026-11-27T12:30'),
                                           ('2026-10-17','2026-10-15T15:30')])
def test_expiry_deadline_counts_trading_sessions_and_early_close(expiry,expected):
    assert life.expiry_deadline(expiry,1).isoformat().startswith(expected)


def test_fill_correction_is_held_not_double_counted(options):
    entered(options)
    ledger=desk.load_ledger();ledger['broker_fills'][0]['shares']=1;desk.save_ledger(ledger)
    life.sync_ledger(options.live.service)
    row=options.live.service.load()['option_positions']['entry']
    assert row['remaining']==1 and 'corrected' in row['hold']


def test_assignment_monitor_runs_while_paused_after_hours(options,monkeypatch):
    entered(options)
    cfg=desk.load_config();cfg['live_agent']['enabled']=False;cfg['session_active']=False;desk.save_config(cfg)
    options.positions=[]
    monkeypatch.setattr(agent.paper_loop,'is_rth',lambda *a:False)
    options.live.service.tick()
    assert options.live.service.load()['option_positions']['entry']['hold']
    assert len(options.live.sent)==1


def test_final_broker_guard_catches_assignment_after_preflight(options):
    identity=options.live.identity
    order=dict(ticker='TEST',option_lifecycle_guard=dict(identity=identity,stock_qty=0.,options={'100':2}))
    fake=NS(positions=lambda account:[NS(account=identity['account_id'],position=100,contract=NS(
        symbol='TEST',conId=42,secType='STK',currency='USD'))],openTrades=lambda:[])
    assert 'holdings changed' in ibkr._option_lifecycle_guard(fake,order,identity)


def test_second_managed_position_cannot_open(options):
    entered(options)
    sig=signal(options);sig['id']='entry-2';sig['ticker']='OTHER'
    with pytest.raises(ValueError,match='one managed'):
        life.prepare(options.live.service,options.cfg,sig,options.snapshot(options.live.identity))


def review_ready(env):
    entered(env)
    env.positions=[]
    assert life.monitor(env.live.service,env.cfg,force=True)
    cfg=desk.load_config();cfg['live_agent']['enabled']=False;cfg['session_active']=False;desk.save_config(cfg)
    return desk.app.test_client()


def test_external_review_saves_audit_without_fabricating_fills_or_enabling(options):
    client=review_ready(options)
    before=copy.deepcopy(desk.load_ledger())
    response=client.post('/api/live-agent/options/review-closed',base_url='http://127.0.0.1:5056',json={'id':'entry'})
    assert response.status_code==200, response.json
    body=dict(token=response.json['token'],reference='2026-09-26 broker activity',acknowledge=True)
    reply=client.post('/api/live-agent/options/accept-closed',base_url='http://127.0.0.1:5056',json=body)
    assert reply.status_code==200,reply.json
    row=options.live.service.load()['option_positions']['entry']
    assert life.resolved(row) and row['remaining']==2 and not row['hold']
    assert row['external_resolution']['realized_pnl'] is None
    assert desk.load_ledger()==before and not desk.load_config()['live_agent']['enabled']
    assert client.post('/api/live-agent/options/accept-closed',base_url='http://127.0.0.1:5056',json=body).status_code==400
    assert len(options.live.sent)==1
    life.sync_ledger(agent.LiveAgent(desk))
    assert life.resolved(options.live.service.load()['option_positions']['entry'])


@pytest.mark.parametrize('change',['stock','option','working','config','expired','fill'])
def test_review_rechecks_fresh_evidence_and_single_use_token(options,change,monkeypatch):
    client=review_ready(options)
    response=client.post('/api/live-agent/options/review-closed',base_url='http://127.0.0.1:5056',json={'id':'entry'})
    assert response.status_code==200,response.json
    if change=='stock': options.positions=[held(42,100,'STK')]
    elif change=='option': options.positions=[held()]
    elif change=='working': options.orders=[dict(symbol='TEST',con_ids=[100])]
    elif change=='config':
        cfg=desk.load_config();cfg['live_agent']['revision']='changed';desk.save_config(cfg)
    elif change=='expired':
        original=option_review.time.monotonic
        monkeypatch.setattr(option_review.time,'monotonic',lambda:original()+100)
    elif change=='fill':
        ledger=desk.load_ledger();ledger['broker_fills'][0]['shares']=1;desk.save_ledger(ledger)
    reply=client.post('/api/live-agent/options/accept-closed',base_url='http://127.0.0.1:5056',json=dict(token=response.json['token'],reference='activity checked',acknowledge=True))
    assert reply.status_code==400,reply.json
    assert options.live.service.load()['option_positions']['entry']['hold'] and len(options.live.sent)==1


@pytest.mark.parametrize('active',['fox','session','worker'])
def test_review_cannot_pause_active_trading_for_user(options,active):
    client=review_ready(options)
    cfg=desk.load_config()
    if active=='fox': cfg['live_agent']['enabled']=True
    elif active=='session': cfg['session_active']=True
    else: options.live.service.cycle_lock.acquire()
    desk.save_config(cfg)
    try:
        assert client.post('/api/live-agent/options/review-closed',base_url='http://127.0.0.1:5056',json={'id':'entry'}).status_code==400
        assert desk.load_config()==cfg
    finally:
        if active=='worker': options.live.service.cycle_lock.release()


def test_review_does_not_accept_get_or_cross_site(options):
    client=review_ready(options)
    assert client.get('/api/live-agent/options/accept-closed',base_url='http://127.0.0.1:5056').status_code==405
    assert client.post('/api/live-agent/options/review-closed',base_url='http://127.0.0.1:5056',json={'id':'entry'},headers={'Origin':'https://foreign.example'}).status_code==403


def test_corrected_fill_after_external_review_reopens_hold(options):
    client=review_ready(options)
    token=client.post('/api/live-agent/options/review-closed',base_url='http://127.0.0.1:5056',json={'id':'entry'}).json['token']
    assert client.post('/api/live-agent/options/accept-closed',base_url='http://127.0.0.1:5056',json=dict(token=token,reference='activity reviewed',acknowledge=True)).status_code==200
    ledger=desk.load_ledger();ledger['broker_fills'][0]['shares']=1;desk.save_ledger(ledger)
    life.sync_ledger(options.live.service)
    row=options.live.service.load()['option_positions']['entry']
    assert row['hold'] and not life.resolved(row)


def test_credit_spread_close_can_buy_back_all_legs(options):
    sig=signal(options,'call_credit')
    sig.update(long_strike=105.,short_strike=100.)
    life.prepare(options.live.service,options.cfg,sig,options.snapshot(options.live.identity))
    ledger=desk.load_ledger();ledger['broker_fills']=[dict(signal_id='entry',confirmed=True,price_estimated=False,shares=2,price=1.)]
    desk.save_ledger(ledger);life.sync_ledger(options.live.service)
    options.positions=[held(105,2),held(100,-2)]
    options.bid=.4;options.ask=.45
    assert life.manage_exits(options.live.service,options.cfg)
    order=options.live.sent[-1]
    assert order['option_intent']=='CLOSE' and order['side']=='buy' and order['contracts']==2
    assert order['long_strike']==105 and order['short_strike']==100


def test_entry_loss_cap_leaves_no_contract_instead_of_raising_budget(options):
    cfg=desk.load_config();cfg['live_agent']['policy']['max_order_usd']=50.;desk.save_config(cfg)
    result=desk._ingest_one_signal(signal(options),cfg_override=cfg)
    assert not result.get('fill') and not options.live.sent
    assert 'budget below' in result['reject_reason'].lower()


def test_option_order_evidence_survives_signal_list_pruning(options,monkeypatch):
    entered(options)
    assert options.live.service.load()['option_positions']['entry']['broker_records']['entry']['live_response']
    desk.save_signals([])
    seen=[]
    monkeypatch.setattr(desk,'_LAST_EXECUTION_CORRECTIONS_AT',0)
    monkeypatch.setattr(ibkr,'refresh_execution_corrections',lambda ids,identity:seen.extend(ids) or dict(ok=False))
    desk._reconcile_execution_corrections()
    assert seen==['option-1']


@pytest.mark.parametrize('bad_records',[[],None,{'entry':None}])
def test_corrupt_retained_broker_records_are_rejected(options,bad_records):
    entered(options)
    rows=options.live.service.load()['option_positions']
    assert life.state_valid(rows)
    rows['entry']['broker_records']=bad_records
    assert not life.state_valid(rows)


@pytest.mark.parametrize('change',['none','during_read','disconnect','bad_qty'])
def test_adapter_waits_for_complete_positions_and_refuses_changes(gateway,monkeypatch,change):
    from ib_async import Option
    import threading
    monkeypatch.setattr(ibkr,'_WORKER',threading.current_thread())
    identity=ibkr.verify_execution_context()['identity']
    pos=NS(account=identity['account_id'],position=2,contract=Option('TEST','20301018',100,'C','SMART',multiplier='100',currency='USD',conId=100))
    calls=[]
    def downloaded():
        calls.append('positions_end')
        if change=='bad_qty':pos.position=float('nan')
        return [pos]
    def orders():
        calls.append('all_orders')
        if change=='disconnect':gateway.disconnected=True
        return []
    monkeypatch.setattr(gateway,'reqPositions',downloaded,raising=False)
    monkeypatch.setattr(gateway,'reqAllOpenOrders',orders)
    monkeypatch.setattr(gateway,'positions',lambda account:[] if change=='during_read' else [pos])
    result=ibkr.option_lifecycle_snapshot(identity)
    assert result['ok'] is (change=='none')
    assert calls[0]=='positions_end' and not gateway.trades and not gateway.canceled


@pytest.mark.parametrize('field,value',[('bid_observed_at',None),('ask_observed_at','2001-01-01T00:00:00+00:00'),
    ('bid_observed_at','2099-01-01T00:00:00+00:00'),('halted',None),('halted',1),('bid_size',0),('symbol','OTHER'),('action','BUY')])
def test_each_price_side_needs_current_receipt_halt_size_and_exact_terms(options,monkeypatch,field,value):
    entered(options)
    original=router.option_quotes
    def quotes(spec):
        result=original(spec);result['legs'][0][field]=value;return result
    monkeypatch.setitem(router.__dict__,'option_quotes',quotes)
    assert life.manage_exits(options.live.service,options.cfg)
    assert len(options.live.sent)==1
