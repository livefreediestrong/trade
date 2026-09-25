"""Regression tests for the live-market audit. No broker writes or network."""
import asyncio
import copy
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS
import time

import pytest
import app as desk
import broker_alpaca as alpaca
import broker_ibkr as ibkr
import broker_router as router
import data_sources as ds
from broker_fixtures import approval, deadline, IDENTITY, fresh_quote
from test_execution_repairs import execution

# Initialize Windows' local event-loop socket pair before network denial fixtures.
asyncio.set_event_loop(asyncio.new_event_loop())
from ib_insync import IB, Stock, Order, OrderState, OrderStatus, Trade, Execution, Fill, CommissionReport


BASE = "http://127.0.0.1:5056"
REAL_POSITION_QTY = desk._broker_position_qty


def idea(**changes):
    now = datetime.now(timezone.utc)
    result = dict(id="review", ticker="TEST", side="buy", suggested_shares=10, signal_price=100.,
                  status="pending", workspace="live", confidence=.9, verdict="PASS", llm_model="test-model",
                  ts=now.isoformat(), expires_at=(now+timedelta(minutes=15)).isoformat(),
                  quote=ds.quote_snapshot(100, now, "fixture"))
    result.update(changes)
    return result


@pytest.mark.parametrize("status,quantity,cached", [("Filled",5,True), ("Filled",5,False), ("Cancelled",2,False), ("Cancelled",0,False)])
def test_completed_orders_with_real_library_callback(execution, monkeypatch, status, quantity, cached):
    client=IB()
    contract=Stock("TEST", "SMART", "USD")
    order=Order(orderId=0,clientId=0,permId=101,account="TEST_ACCOUNT",totalQuantity=5,filledQuantity=quantity)
    original=Trade(contract,copy.copy(order),OrderStatus(status=status,filled=quantity,avgFillPrice=100),[],[])
    original.order.orderId, original.order.clientId = 17, 37
    client.wrapper.permId2Trade[101]=original
    client.wrapper._results['completedOrders']=[]
    client.wrapper.completedOrder(contract,order,OrderState(status=status))
    completed=client.wrapper._results['completedOrders']
    assert completed[0].orderStatus.filled == 0
    fills=[] if not quantity else [Fill(contract,Execution(execId='fill.1',acctNumber='TEST_ACCOUNT',permId=101,
             orderId=17,clientId=37,cumQty=quantity,avgPrice=100,shares=quantity,price=100),CommissionReport(),datetime.now(timezone.utc))]
    fake=NS(trades=lambda:[original] if cached else [],reqAllOpenOrders=lambda:[],
            reqCompletedOrders=lambda **k:completed,reqExecutions=lambda f:fills)
    @contextmanager
    def session(): yield fake
    monkeypatch.setattr(ibkr,'_session',session)
    monkeypatch.setattr(ibkr,'_identity',lambda ib:{'account_id':'TEST_ACCOUNT','client_id':37})
    result=ibkr._poll.__wrapped__('TEST_ACCOUNT:perm:101',0,.01)
    assert result['terminal'] and result['filled_qty']==quantity, result
    sig=idea()
    desk.save_signals([sig])
    fill,terminal=desk._apply_broker_update(sig,{'order_id':'TEST_ACCOUNT:perm:101','broker':'ibkr'},result,'test')
    assert terminal and not desk.load_ledger()['pending_broker_orders']
    assert (fill or {}).get('shares',0)==quantity
    assert sig['status']==('approved' if quantity else 'rejected')


def test_completed_partial_missing_executions_stays_unresolved(execution, monkeypatch):
    order=Order(permId=101,account='TEST_ACCOUNT',totalQuantity=5,filledQuantity=2)
    trade=Trade(Stock('TEST','SMART','USD'),order,OrderStatus(status='Cancelled'),[],[])
    result=ibkr._execution_state(NS(reqExecutions=lambda f:[]),[trade],'TEST_ACCOUNT:perm:101')
    assert not result['terminal'] and not result['execution_details_verified']


@pytest.mark.parametrize('change',['account','quantity','side','mode'])
def test_review_token_binds_account_and_order(execution, monkeypatch, change):
    cfg,sig,sent=execution
    client=desk.app.test_client()
    body=approval(client,'review')
    if change in ('account','mode'):
        cfg=desk.load_config()
        cfg.update(broker_identity=dict(IDENTITY,account_id='OTHER')) if change=='account' else cfg.update(mode='auto_live')
        desk.save_config(cfg)
    else:
        sig.update(suggested_shares=11) if change=='quantity' else sig.update(side='sell')
        desk.save_signals([sig])
    response=client.post('/api/signals/review/approve',base_url=BASE,json=body)
    assert response.status_code==409 and not sent


def test_live_acknowledgement_is_enforced_by_server(execution,monkeypatch):
    cfg,sig,sent=execution
    identity=dict(IDENTITY,paper_mode=False)
    cfg['broker_identity']=identity
    desk.save_config(cfg)
    monkeypatch.setattr(alpaca,'verify_execution_context',lambda:{'ok':True,'identity':identity})
    client=desk.app.test_client()
    body=approval(client,'review')
    response=client.post('/api/signals/review/approve',base_url=BASE,json=dict(body,ack_ticker='WRONG'))
    assert response.status_code==400 and not sent
    assert client.post('/api/signals/review/approve',base_url=BASE,json=body).status_code==200
    assert len(sent)==1
    assert client.post('/api/signals/review/approve',base_url=BASE,json=body).status_code==400
    assert len(sent)==1


@pytest.mark.parametrize('payload',[{}, {'review_token':'invented'}, []])
def test_unreviewed_broker_requests_never_submit(execution,payload):
    response=desk.app.test_client().post('/api/signals/review/approve',base_url=BASE,json=payload)
    assert response.status_code in (400,409) and not execution[2]


def test_expired_ticket_never_submits(execution):
    client=desk.app.test_client()
    body=approval(client,'review')
    desk._BROKER_REVIEWS[body['review_token']]['expires']=time.time()-1
    assert client.post('/api/signals/review/approve',base_url=BASE,json=body).status_code==409
    assert not execution[2]


def test_staleness_after_slow_checks_blocks_before_intent(execution,monkeypatch):
    cfg,sig,sent=execution
    sig.update(idea(expires_at=(datetime.now(timezone.utc)+timedelta(seconds=.7)).isoformat()))
    desk.save_signals([sig])
    client=desk.app.test_client()
    body=approval(client,'review')
    def fetch(ticker):
        desk._QUOTE_SNAPSHOTS[ticker]=ds.quote_snapshot(100,datetime.now(timezone.utc)-timedelta(seconds=119),'fixture')
        return 100.
    monkeypatch.setattr(desk,'fetch_last_price',fetch)
    monkeypatch.setattr(desk,'_broker_day_pnl',lambda:time.sleep(1.2) or (0.,100000.,None))
    response=client.post('/api/signals/review/approve',base_url=BASE,json=body)
    assert response.status_code==400 and not sent
    assert not desk.load_ledger().get('pending_broker_orders')


def test_ibkr_expiry_checked_after_contract_qualification(execution,monkeypatch):
    order={'ticker':'TEST','side':'buy','shares':1,'signal_id':'s','broker_identity':IDENTITY,'valid_until':deadline()}
    def qualify(contract):
        order['valid_until']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        return [contract]
    fake=NS(reqAllOpenOrders=lambda:[],reqCompletedOrders=lambda **k:[],qualifyContracts=qualify,
            placeOrder=lambda *a:pytest.fail('Expired order must not reach Gateway'))
    @contextmanager
    def session(): yield fake
    monkeypatch.setattr(ibkr,'_session',session)
    monkeypatch.setattr(ibkr,'_identity',lambda ib:IDENTITY)
    result=ibkr.place_from_desk_order.__wrapped__(order)
    assert not result['ok'] and not result['submission_attempted'] and 'expired' in result['error']


def test_paper_sizes_independently_of_live_preset(execution):
    cfg=desk.load_config()
    cfg.update(risk_preset='high',paper_risk_preset='low',paper_auto_approve=True,slip_bps=0)
    desk.save_config(cfg)
    ledger=desk.load_ledger()
    ledger.update(equity=100000.,cash=100000.)
    desk.save_ledger(ledger)
    sig=desk._analysis_to_signal({'ticker':'TEST','price':100,'verdict':'PASS','quote':idea()['quote']},cfg,desk.get_preset('high'))
    sig['llm_model']='mock-fixture'
    desk.ingest_signal(sig)
    paper=next(s for s in desk.load_signals() if s.get('workspace')=='paper')
    assert sig['suggested_shares']==40
    assert paper['suggested_shares']==10 and paper['status']=='approved',paper
    assert not execution[2]


@pytest.mark.parametrize('change',[{'auto_approve':False},{'risk_preset':'low'}])
def test_inflight_paper_fill_rechecks_controls(execution,monkeypatch,change):
    cfg=dict(desk.load_config(),paper_auto_approve=True,paper_risk_preset='high')
    desk.save_config(cfg)
    def price(ticker):
        reply=desk.app.test_client().post('/api/paper-research',base_url=BASE,json=change)
        assert reply.status_code==200
        return 100.
    monkeypatch.setattr(desk,'fetch_last_price',price)
    desk.ingest_signal(idea(workspace='paper',suggested_shares=1))
    assert not desk.load_ledger()['fills'] and not execution[2]
    assert desk.load_signals()[0]['status']=='pending'


def test_zero_paper_quantity_never_becomes_one_share(execution):
    result=desk.paper_fill(idea(workspace='paper',suggested_shares=0),desk.paper_research_config(desk.load_config()),source='manual_approve')
    assert not result['ok'] and result['error']=='zero_shares'


def test_restart_restores_pending_and_filled_evidence(execution):
    ledger=desk.load_ledger()
    ledger['pending_broker_orders']=[{'signal':{'id':'pending'}}]
    ledger['broker_fills']=[{'signal_id':'filled','shares':2}]
    desk.save_ledger(ledger)
    desk.save_signals([idea(id=i,status='approving') for i in ('pending','filled','interrupted')])
    desk.recover_interrupted_approvals()
    assert {s['id']:s['status'] for s in desk.load_signals()}=={'pending':'broker_pending','filled':'approved','interrupted':'rejected'}
    assert not execution[2]


def test_recovery_never_polls_another_account(execution,monkeypatch):
    ledger=desk.load_ledger()
    ledger['pending_broker_orders']=[{'order_id':'other','broker':{'broker':'alpaca','order_id':'other',
        'order':{'broker_identity':dict(IDENTITY,account_id='OTHER')}},'signal':idea()}]
    desk.save_ledger(ledger)
    monkeypatch.setattr(alpaca,'wait_for_fill',lambda *a,**k:pytest.fail('Different account must not be polled'))
    desk._reconcile_pending_broker_orders()
    assert len(desk.load_ledger()['pending_broker_orders'])==1


@pytest.mark.parametrize('qty',[None,'bad',float('nan'),float('inf')])
def test_invalid_positions_cannot_hide_existing_exposure(execution,monkeypatch,qty):
    monkeypatch.setattr(alpaca,'get_positions',lambda:{'ok':True,'positions':[{'symbol':'TEST','qty':qty}]})
    # Fixture overrides the public helper, so call its real saved implementation.
    assert REAL_POSITION_QTY('TEST')[1]


def test_upstream_disconnect_revokes_cached_identity(monkeypatch):
    monkeypatch.setattr(ibkr,'_VERIFIED',{'identity':IDENTITY})
    monkeypatch.setattr(ibkr,'_PNL_UPDATED',{'TEST':time.monotonic()})
    monkeypatch.setattr(ibkr,'_CONNECTION',{'connected':True})
    monkeypatch.setattr(ibkr,'_SERVER_UNAVAILABLE',False)
    monkeypatch.setattr(ibkr,'_RESYNC_REQUIRED',False)
    ibkr._server_error(-1,1100,'lost')
    assert ibkr._SERVER_UNAVAILABLE and not ibkr._VERIFIED and not ibkr._PNL_UPDATED
    ibkr._server_error(-1,1102,'restored')
    assert not ibkr._SERVER_UNAVAILABLE and not ibkr._RESYNC_REQUIRED
    assert not ibkr._VERIFIED and not ibkr._PNL_UPDATED
    ibkr._server_error(-1,1101,'restored, data lost')
    assert ibkr._RESYNC_REQUIRED


def test_prior_day_pnl_is_not_accepted_and_subscription_recovers(monkeypatch):
    clock=[100.]
    fake=NS(pnlEvent=object(), isConnected=lambda:True, reqCurrentTime=lambda:object(), accountSummary=lambda account:[
        NS(account=account,currency='USD',tag=k,value=v) for k,v in
        {'NetLiquidation':'100000','TotalCashValue':'90000','BuyingPower':'90000'}.items()])
    canceled=[]
    fake.cancelPnL=lambda account:canceled.append(account)
    def request(account):
        return NS(account=account, modelCode='', dailyPnL=-50.)
    fake.reqPnL=request
    def pump(seconds):
        clock[0] += seconds
        ibkr._pnl_update(ibkr._PNL[(id(fake),'TEST')]['value'])
    fake.sleep=pump
    @contextmanager
    def session(): yield fake
    monkeypatch.setattr(ibkr,'_session',session)
    monkeypatch.setattr(ibkr,'_identity',lambda ib:{'account_id':'TEST','paper_mode':True})
    monkeypatch.setattr(ibkr.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(ibkr,'_pnl_day',lambda:'today')
    monkeypatch.setattr(ibkr,'_ACCOUNT_READY',{})
    monkeypatch.setattr(ibkr,'_API_PULSE',{})
    monkeypatch.setattr(ibkr,'_SERVER_UNAVAILABLE',False)
    monkeypatch.setattr(ibkr,'_RESYNC_REQUIRED',False)
    monkeypatch.setattr(ibkr,'_PNL',{(id(fake),'TEST'):{'value':NS(dailyPnL=123.),'requested_at':100.,'day':'yesterday'}})
    monkeypatch.setattr(ibkr,'_PNL_UPDATED',{'TEST':50.})
    result=ibkr.get_account.__wrapped__()
    assert result['ok'] and result['account']['day_pnl'] is None and not result['risk_ready']
    clock[0]=100.+ibkr._PNL_SILENT_RETRY_SEC+1.  # silent-retry window (was 60s, now 180s)
    result=ibkr.get_account.__wrapped__()
    assert canceled==['TEST'] and result['account']['day_pnl']==-50 and result['risk_ready']


def test_broker_cache_is_bound_to_account(execution,monkeypatch):
    monkeypatch.setattr(desk,'_BROKER_BOOK_CACHE',{'at':0.,'val':None})
    account=['ACCOUNT_A']
    monkeypatch.setattr(alpaca,'get_account',lambda:{'ok':True,'account':{'id':account[0],'equity':100,'day_pnl':0}})
    monkeypatch.setattr(alpaca,'get_positions',lambda:{'ok':True,'positions':[]})
    assert desk._broker_book_cached()['account_id']=='ACCOUNT_A'
    cfg=desk.load_config()
    cfg['broker_identity']=dict(IDENTITY,account_id='ACCOUNT_B')
    desk.save_config(cfg)
    account[0]='ACCOUNT_B'
    assert desk._broker_book_cached()['account_id']=='ACCOUNT_B'
