"""Actual ticket/review/approval routes with broker transport intercepted."""
import copy
from types import SimpleNamespace as NS

import pytest
import app as desk
import broker_router as router
import broker_ibkr as ibkr
from test_execution_repairs import execution
from test_broker_lifecycle import gateway
from broker_fixtures import deadline

BASE = "http://127.0.0.1:5056"
BODY = {"ticker":"TEST", "intent":"buy", "shares":2, "order":{"type":"limit","limit_price":100}}


@pytest.fixture
def ticket(execution, monkeypatch):
    identity = dict(execution[0]["broker_identity"], broker="ibkr", paper_mode=False, endpoint="fixture")
    execution[0]["broker_identity"] = identity
    desk.save_config(execution[0])
    monkeypatch.setitem(router.__dict__, "public_status", lambda: {"broker":"ibkr","connected":True,"paper_mode":False})
    monkeypatch.setitem(router.__dict__, "verify_execution_context", lambda: {"ok":True,"identity":copy.deepcopy(identity)})
    monkeypatch.setitem(router.__dict__, "estimate_order", lambda order: {"contract_verified":True,"commission_estimate":1.,"currency":"USD","guaranteed":False,
        "contract":{"con_id":101,"symbol":"TEST","primary_exchange":"NASDAQ","currency":"USD"}})
    monkeypatch.setitem(router.__dict__, "wait_for_fill", lambda *a, **kw: {"state":"filled","terminal":True,"filled_qty":execution[2][-1]["shares"],"filled_avg_price":100})
    return execution


def preview(body=None):
    return desk.app.test_client().post('/api/live/ticket/review', base_url=BASE, json=body or copy.deepcopy(BODY))


def approve(data, **extra):
    return desk.app.test_client().post(f'/api/signals/{data["signal_id"]}/approve',base_url=BASE,
        json={"review_token":data["review_token"],"ack_ticker":data["ticker"],**extra})


def test_preview_does_not_send_and_exact_ticket_submits_once(ticket):
    before_config=desk.CONFIG_PATH.read_bytes(); before_paper=copy.deepcopy(desk.load_ledger()["fills"])
    response=preview(); assert response.status_code==200,response.get_json()
    data=response.get_json()
    assert not ticket[2] and data['estimated_cash_change']==-201
    assert data['identity']['paper_mode'] is False and data['order']['shares']==2
    result=approve(data, shares=999, side='sell', limit_price=1)
    assert result.status_code==200,result.get_json()
    assert len(ticket[2])==1 and ticket[2][0]['shares']==2 and ticket[2][0]['side']=='buy'
    assert ticket[2][0]['limit']==100 and ticket[2][0]['position_intent']=='buy'
    assert ticket[2][0]['contract_identity']['con_id']==101
    assert approve(data).status_code==400 and len(ticket[2])==1
    assert desk.CONFIG_PATH.read_bytes()==before_config and desk.load_ledger()['fills']==before_paper


@pytest.mark.parametrize('patch',[
    {'ticker':'AAPL MSFT'}, {'ticker':'<script>'}, {'shares':True}, {'shares':0}, {'shares':1.5},
    {'shares':'NaN'}, {'shares':'Infinity'}, {'intent':'short'}, {'order':None},
    {'order':{'type':'stop','limit_price':100}}, {'order':{'type':'limit','limit_price':float('nan')}},
    {'order':{'type':'market','max_total':10}}, {'broker_identity':{'paper_mode':True}},
])
def test_invalid_input_never_reaches_submission(ticket, patch):
    body=copy.deepcopy(BODY); body.update(patch)
    assert preview(body).status_code==400
    assert not ticket[2]


@pytest.mark.parametrize('mode',['auto_live','auto_paper','manual'])
def test_direct_ticket_requires_manual_broker_mode(ticket, mode):
    desk.save_config(dict(ticket[0], mode=mode))
    assert preview().status_code==409 and not ticket[2]


def test_no_quote_and_missing_pnl_block_new_risk(ticket,monkeypatch):
    monkeypatch.setattr(desk,'fetch_last_price',lambda _:None)
    assert preview().status_code==409
    assert not ticket[2]


def test_missing_pnl_blocks_buy_but_allows_verified_sell(ticket,monkeypatch):
    monkeypatch.setattr(desk,'_broker_day_pnl',lambda:(None,100000.,'P&L unavailable'))
    assert preview().status_code==409
    monkeypatch.setattr(desk,'_broker_position_qty',lambda _:(10.,None))
    data=preview(dict(BODY,intent='sell')).get_json()
    assert data['ok'] and data['estimated_cash_change']==199
    result=approve(data)
    assert result.status_code==200,result.get_json()
    assert ticket[2][0]['position_intent']=='sell'


@pytest.mark.parametrize('intent,before,after', [('sell',10,0),('sell',10,1),('cover',-10,0),('cover',-10,2),('buy',0,-10)])
def test_position_change_never_turns_close_into_new_risk(ticket,monkeypatch,intent,before,after):
    monkeypatch.setattr(desk,'_broker_position_qty',lambda _:(before,None))
    data=preview(dict(BODY,intent=intent)).get_json(); assert data['ok'],data
    monkeypatch.setattr(desk,'_broker_position_qty',lambda _:(after,None))
    result=approve(data)
    assert result.status_code==400 and not ticket[2]


def test_working_remainder_reserved_and_fractional_remainder_not_rounded_up(ticket,monkeypatch):
    monkeypatch.setattr(desk,'_broker_position_qty',lambda _:(5.5,None))
    monkeypatch.setitem(router.__dict__,'get_open_orders',lambda:{'ok':True,'orders':[{'symbol':'TEST','side':'sell','qty':5,'filled_qty':1}]})
    response=preview(dict(BODY,intent='sell'))
    assert response.status_code==409 and '1 whole' in response.get_json()['error']
    assert not ticket[2]


def test_risk_cap_does_not_silently_resize_user_ticket(ticket):
    response=preview(dict(BODY,shares=999))
    assert response.status_code==409 and 'shares' in response.get_json()['error']
    assert not ticket[2]


def test_contract_unverified_rejects_draft(ticket,monkeypatch):
    monkeypatch.setitem(router.__dict__,'estimate_order',lambda _:{'commission_estimate':None,'contract_verified':False})
    assert preview().status_code==409
    assert all(s['status']=='rejected' for s in desk.load_signals() if s.get('source')=='manual_ticket')


def test_account_or_mode_change_during_estimate_invalidates(ticket,monkeypatch):
    def estimate(_):
        desk.save_config(dict(ticket[0],mode='auto_live'))
        return {'contract_verified':True,'contract':{'con_id':101}}
    monkeypatch.setitem(router.__dict__,'estimate_order',estimate)
    assert preview().status_code==409 and not ticket[2]


def test_review_terms_cannot_change_and_discard_revokes(ticket):
    data=preview().get_json()
    client=desk.app.test_client()
    other=client.post(f'/api/signals/{data["signal_id"]}/review',base_url=BASE,json={'order':{'type':'market'}})
    assert other.status_code==409
    assert client.post(f'/api/signals/{data["signal_id"]}/reject',base_url=BASE,json={}).status_code==200
    assert approve(data).status_code==400 and not ticket[2]


def test_unknown_commission_stays_unknown(ticket,monkeypatch):
    monkeypatch.setitem(router.__dict__,'estimate_order',lambda _:{'contract_verified':True,'commission_estimate':None,'currency':'USD','contract':{'con_id':101}})
    data=preview().get_json()
    assert data['ok'] and data['estimated_cash_change'] is None


def test_pending_response_is_explicit_and_cannot_submit_twice(ticket,monkeypatch):
    data=preview().get_json()
    monkeypatch.setitem(router.__dict__,'wait_for_fill',lambda *a,**kw:{'state':'pending','terminal':False})
    result=approve(data)
    assert result.status_code==400 and result.get_json()['pending'] is True
    assert preview().status_code==409 and len(ticket[2])==1


@pytest.mark.parametrize('latest,working,allowed',[(5,0,True),(0,0,False),(1,0,False),(5,4,False)])
def test_real_ibkr_submit_checks_cached_position_after_qualification(gateway,monkeypatch,latest,working,allowed):
    identity=ibkr.get_account()['identity']; sent=[]
    def qualify(contract):
        contract.conId=101;contract.primaryExchange='NASDAQ'
        gateway.positions=lambda account:[NS(account=account,contract=contract,position=latest)]
        gateway.openTrades=lambda:[NS(contract=contract,order=NS(account=identity['account_id'],action='SELL',totalQuantity=working),orderStatus=NS(filled=0))] if working else []
        return [contract]
    monkeypatch.setattr(gateway,'qualifyContracts',qualify)
    def place(contract,order):
        sent.append(order);order.orderId=42;order.clientId=37
        return NS(order=order)
    monkeypatch.setattr(gateway,'placeOrder',place)
    result=ibkr.place_from_desk_order({'ticker':'TEST','side':'sell','shares':2,'type':'limit','limit':100,
        'signal_id':'direct-ticket','broker_identity':identity,'valid_until':deadline(),'position_intent':'sell',
        'contract_identity':{'con_id':101,'symbol':'TEST','primary_exchange':'NASDAQ','currency':'USD'},
        'risk_authorization':{'equity':100000.,'day_pnl':-250.,'reducing':True}})
    assert result['ok'] is allowed,result
    assert bool(sent) is allowed


def test_template_contains_explicit_ticket_without_changing_mode(ticket):
    page=desk.app.test_client().get('/',base_url=BASE).get_data(as_text=True)
    assert 'id="live-stock-ticket"' in page and '/static/live_ticket.js' in page
    assert 'name="live-ticket-intent"' in page and 'Type the stock symbol to confirm' in page


def test_failed_review_exception_rejects_unusable_draft(ticket, monkeypatch):
    def failure(*args):
        raise RuntimeError('transport failed')
    monkeypatch.setattr(desk, '_create_broker_review', failure)
    assert preview().status_code == 503
    assert not ticket[2]
    assert all(s['status'] == 'rejected' for s in desk.load_signals() if s.get('source') == 'manual_ticket')


def test_wrong_acknowledgement_cannot_submit(ticket):
    data = preview().get_json()
    assert approve(data, ack_ticker='OTHER').status_code == 400
    assert not ticket[2]


def test_account_change_after_review_cannot_submit(ticket):
    data = preview().get_json()
    cfg = copy.deepcopy(ticket[0])
    cfg['broker_identity']['account_id'] = 'U999999'
    desk.save_config(cfg)
    assert approve(data).status_code == 409
    assert not ticket[2]


def test_direct_ticket_cannot_run_without_exact_review(ticket):
    data = preview().get_json()
    sig = next(s for s in desk.load_signals() if s['id'] == data['signal_id'])
    result = desk.execute_gated_broker_or_paper(sig, ticket[0], source='test', via='test')
    assert not result['ok'] and not ticket[2]


def test_cover_market_ticket_reduces_short_position(ticket, monkeypatch):
    monkeypatch.setattr(desk, '_broker_position_qty', lambda _: (-5.5, None))
    data = preview(dict(BODY, intent='cover', order={'type':'market'})).get_json()
    assert data['available_whole_shares'] == 5
    assert approve(data).status_code == 200
    assert ticket[2][0]['side'] == 'buy' and ticket[2][0]['position_intent'] == 'cover'
    assert ticket[2][0]['shares'] == 2 and ticket[2][0]['type'] == 'market'


@pytest.mark.parametrize('contract', [None, {'con_id':102,'symbol':'TEST','primary_exchange':'NASDAQ','currency':'USD'}])
def test_adapter_rejects_changed_or_missing_reviewed_contract(gateway, monkeypatch, contract):
    identity = ibkr.get_account()['identity']
    def qualify(value):
        value.conId = 101
        value.primaryExchange = 'NASDAQ'
        return [value]
    monkeypatch.setattr(gateway, 'qualifyContracts', qualify)
    gateway.openTrades = lambda: []
    result = ibkr.place_from_desk_order({'ticker':'TEST','side':'buy','shares':1,'type':'market',
        'signal_id':'contract-ticket','broker_identity':identity,'valid_until':deadline(),
        'position_intent':'buy','contract_identity':contract,
        'risk_authorization':{'equity':100000.,'day_pnl':-250.,'reducing':False}})
    assert not result['ok'] and 'reviewed instrument' in result['error']
    assert not gateway.trades
