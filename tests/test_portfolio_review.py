"""Coverage and price-basis checks for the account research view; no orders."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
import research_studio as studio
import broker_ibkr as broker
from test_broker_lifecycle import gateway
from test_ib_async_protocol import wire

NOW=datetime(2026,9,27,12,tzinfo=timezone.utc)

def holding(**changes):
    changes.setdefault('local_symbol',changes.get('symbol','ABC'))
    return dict(dict(con_id=1,symbol='ABC',local_symbol='ABC',asset_type='STK',currency='USD',
        qty=10,market_price=100,multiplier=1,mark_status='reported',right='',expiry=''),**changes)

def snapshot(positions):
    return dict(ok=True,checked_at=NOW.isoformat(),paper_mode=True,positions=positions,orders=[])

def stock_book(**changes):
    return {'ok':True,'checked_at':datetime.now(timezone.utc).isoformat(),'positions':[dict(dict(ticker='ABC',shares=10,last=100,side='long',asset_type='STK',
        currency='USD',mark_is_cost_basis=False),**changes)]}

def test_purchase_price_is_never_a_market_mark():
    r=studio.portfolio_context(stock_book(mark_is_cost_basis=True),{})
    assert r['gross_usd'] is None and r['scenarios']==[]
    assert r['missing_marks_or_unsupported']==['ABC']

def test_all_instrument_inventory_reveals_excluded_holdings():
    data=snapshot([holding(),holding(con_id=2,symbol='EURCO',currency='EUR'),
        holding(con_id=3,symbol='FUTURE',asset_type='FUT'),holding(con_id=4,symbol='CALL',asset_type='OPT',qty=-2,multiplier=100,market_price=3)])
    r=studio.portfolio_context({}, {}, snapshot=data,now=NOW)
    assert len(r['holdings'])==4 and r['coverage']['total_holdings']==4
    assert r['coverage']['included_holdings']==2 and r['status']=='partial'
    assert r['gross_usd']==1600 and r['net_usd']==400
    assert set(r['missing_marks_or_unsupported'])=={'EURCO','FUTURE'}
    # Options have nonlinear underlying exposure; never apply a stock shock to them.
    assert r['scenarios'][0]['equity_position_change_usd']==-200
    assert r['short_option_count']==1 and r['coverage']['live_prices_verified'] is False

@pytest.mark.parametrize('field,value',[('market_price',None),('market_price',True),('market_price',float('nan')),
    ('market_price',0),('market_price',-1),('multiplier',None),('multiplier',True),('multiplier',0),('multiplier',2)])
def test_unsupported_or_invalid_mark_is_unknown(field,value):
    p=holding();p[field]=value
    r=studio.portfolio_context({}, {},snapshot=snapshot([p]),now=NOW)
    assert r['gross_usd'] is None and not r['scenarios']
    assert r['holdings'][0]['value_usd'] is None

@pytest.mark.parametrize('age',[46,-1])
def test_stale_or_future_account_read_has_no_amounts(age):
    s=snapshot([holding()]);s['checked_at']=(NOW-timedelta(seconds=age)).isoformat()
    r=studio.portfolio_context({}, {},snapshot=s,now=NOW)
    assert r['status']=='unavailable' and r['gross_usd'] is None and not r['scenarios']

def test_duplicate_contract_is_not_counted_twice_and_conflict_withholds_values():
    p=holding()
    r=studio.portfolio_context({}, {},snapshot=snapshot([p,copy.deepcopy(p)]),now=NOW)
    assert r['gross_usd']==1000 and len(r['holdings'])==1
    q=dict(p,qty=9)
    r=studio.portfolio_context({}, {},snapshot=snapshot([p,q]),now=NOW)
    assert r['status']=='unavailable' and r['gross_usd'] is None

def test_short_stock_value_and_shock_are_signed_without_netting_away_gross():
    r=studio.portfolio_context({}, {},snapshot=snapshot([holding(),holding(con_id=2,symbol='SHORT',qty=-5)]),now=NOW)
    assert r['gross_usd']==1500 and r['net_usd']==500
    assert r['scenarios'][0]['equity_position_change_usd']==-100

def test_unsupported_only_account_is_not_empty_or_zero_exposure():
    r=studio.portfolio_context({}, {},snapshot=snapshot([holding(asset_type='FUT')]),now=NOW)
    assert r['gross_usd'] is None and r['net_usd'] is None and not r['scenarios']
    assert r['coverage']['total_holdings']==1 and r['status']=='partial'

def test_actual_empty_complete_inventory_is_distinct_from_unavailable():
    r=studio.portfolio_context({}, {},snapshot=snapshot([]),now=NOW)
    assert r['status']=='empty' and r['coverage']['total_holdings']==0 and r['gross_usd']==0
    assert not r['scenarios']

def test_legacy_stock_subset_never_claims_whole_account_coverage():
    r=studio.portfolio_context(stock_book(),{})
    assert r['gross_usd']==1000 and r['coverage']['all_returned_asset_types'] is False
    assert r['status']=='partial'

def test_broker_marks_require_matching_contract_and_quantity(gateway,monkeypatch):
    contract=NS(conId=1,symbol='ABC',localSymbol='ABC',secType='STK',currency='USD',multiplier='',right='',lastTradeDateOrContractMonth='')
    monkeypatch.setattr(gateway,'positions',lambda account:[NS(account=account,contract=contract,position=10,avgCost=7)])
    monkeypatch.setattr(gateway,'reqAllOpenOrders',lambda:[])
    monkeypatch.setattr(gateway,'portfolio',lambda account:[NS(account=account,contract=contract,position=9,marketPrice=100)],raising=False)
    r=broker.get_safety_snapshot(include_marks=True)
    assert r['ok'] and r['positions'][0]['market_price'] is None
    assert r['positions'][0]['mark_status']=='quantity_mismatch'

def test_mark_observation_is_read_time_not_exchange_price_time(gateway,monkeypatch):
    contract=NS(conId=1,symbol='ABC',localSymbol='ABC',secType='STK',currency='USD',multiplier='',right='',lastTradeDateOrContractMonth='')
    monkeypatch.setattr(gateway,'positions',lambda account:[NS(account=account,contract=contract,position=10,avgCost=7)])
    monkeypatch.setattr(gateway,'reqAllOpenOrders',lambda:[])
    monkeypatch.setattr(gateway,'portfolio',lambda account:[NS(account=account,contract=contract,position=10,marketPrice=100)],raising=False)
    r=broker.get_safety_snapshot(include_marks=True)
    assert r['positions'][0]['market_price']==100 and r['positions'][0]['mark_status']=='reported'
    assert r['mark_observation']['broker_price_time'] is None
    assert r['mark_observation']['read_at']==r['checked_at']

def test_default_protection_read_does_not_request_or_depend_on_marks(gateway,monkeypatch):
    monkeypatch.setattr(gateway,'positions',lambda account:[])
    monkeypatch.setattr(gateway,'reqAllOpenOrders',lambda:[])
    monkeypatch.setattr(gateway,'portfolio',lambda account:pytest.fail('Protection must not depend on portfolio marks'),raising=False)
    assert broker.get_safety_snapshot()['ok']

def test_actual_library_decodes_market_price_separately_from_cost_basis(wire,monkeypatch):
    import struct
    from contextlib import nullcontext
    client,sent,_,_=wire
    fields=(7,8,123,'ABC','STK','',0,'','', 'NYSE','USD','ABC','ABC',10,100,1000,7,930,0,'DU-TEST')
    body=('\0'.join(map(str,fields))+'\0').encode();data=struct.pack('>I',len(body))+body
    client.client._onSocketHasData(data[:9]);assert client.portfolio('DU-TEST')==[]
    client.client._onSocketHasData(data[9:])
    mark=client.portfolio('DU-TEST')[0]
    client.wrapper.position('DU-TEST',mark.contract,10,7)
    monkeypatch.setattr(broker,'_session',lambda:nullcontext(client))
    monkeypatch.setattr(broker,'_identity',lambda ib:{'broker':'ibkr','account_id':'DU-TEST','paper_mode':True})
    monkeypatch.setattr(client,'isConnected',lambda:True)
    monkeypatch.setattr(client,'reqAllOpenOrders',lambda:[])
    result=broker.get_safety_snapshot.__wrapped__(include_marks=True)
    assert result['ok'] and result['positions'][0]['market_price']==100
    assert result['positions'][0]['market_price']!=mark.averageCost
    assert sent==[]

def test_account_route_uses_all_holdings_snapshot_and_returns_no_account_identifier(monkeypatch):
    import app as desk
    monkeypatch.setenv('BROKER_PROVIDER','ibkr')
    monkeypatch.setattr(broker,'public_status',lambda:{'broker':'ibkr','connected':True})
    data=snapshot([holding(),holding(con_id=2,asset_type='FUT')]);data['checked_at']=datetime.now(timezone.utc).isoformat()
    data['identity']={'account_id':'PRIVATE-ACCOUNT'}
    calls=[]
    def read(**kwargs):calls.append(kwargs);return data
    monkeypatch.setattr(broker,'get_safety_snapshot',read)
    monkeypatch.setattr(desk,'_broker_book_cached',lambda:pytest.fail('The stock subset must not replace all holdings'))
    reply=desk.app.test_client().get('/api/research/portfolio',base_url='http://127.0.0.1:5056')
    assert reply.status_code==200 and reply.json['coverage']['total_holdings']==2
    assert calls==[{'include_marks':True}] and 'PRIVATE-ACCOUNT' not in reply.text

def test_disconnected_account_route_never_requests_broker_or_retains_values(monkeypatch):
    import app as desk
    monkeypatch.setenv('BROKER_PROVIDER','ibkr')
    monkeypatch.setattr(broker,'public_status',lambda:{'broker':'ibkr','connected':False})
    monkeypatch.setattr(broker,'get_safety_snapshot',lambda **kwargs:pytest.fail('No disconnected broker read'))
    reply=desk.app.test_client().get('/api/research/portfolio',base_url='http://127.0.0.1:5056')
    assert reply.status_code==200 and reply.json['status']=='unavailable'
    assert reply.json['gross_usd'] is None and reply.json['holdings']==[]

@pytest.mark.parametrize('row',[{},True,{'qty':True,'con_id':1}])
def test_malformed_full_inventory_cannot_be_empty_or_zero(row):
    result=studio.portfolio_context({}, {},snapshot=snapshot([row]),now=NOW)
    assert result['status']=='unavailable' and result['gross_usd'] is None
