from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
import broker_ibkr as broker
from test_broker_lifecycle import gateway


def ticks(kind=1,age=0):
    t=datetime.now(timezone.utc)-timedelta(seconds=age)
    return NS(marketDataType=kind,bidSize=10,askSize=10,close=999,last=888,
              ticks=[NS(tickType=1,price=100,time=t),NS(tickType=2,price=100.02,time=t)])


@pytest.mark.parametrize('kind,fresh,word',[(1,True,'live'),(2,False,'frozen'),(3,True,'delayed'),(4,False,'frozen'),(None,False,'unknown')])
def test_types_and_receipt_provenance(kind,fresh,word):
    t=ticks(kind);seen={};broker._capture_stock_ticks(t,seen);q=broker._stock_tick_quote(t,seen)
    assert q['fresh'] is fresh and word in q['source']
    assert q['price']==pytest.approx(100.01) and q['time_kind']=='local_receipt' and q['exchange_time'] is None


def test_close_and_unrelated_ticks_never_establish_freshness():
    t=ticks();t.ticks=[NS(tickType=9,price=999,time=datetime.now(timezone.utc))]
    seen={};broker._capture_stock_ticks(t,seen);q=broker._stock_tick_quote(t,seen)
    assert q['price'] is None and q['fresh'] is False


@pytest.mark.parametrize('problem',['frozen','old','crossed','spread','size','identity'])
def test_final_guard_blocks_before_real_adapter_dispatch(gateway,monkeypatch,problem):
    account=broker.get_account();identity=account['identity'];q=ticks(2 if problem=='frozen' else 1,20 if problem=='old' else 0)
    if problem=='spread':q.ticks[1].price=110
    if problem=='crossed':q.ticks[1].price=99
    if problem=='size':q.askSize=0
    seen={};broker._capture_stock_ticks(q,seen);quote=dict(broker._stock_tick_quote(q,seen),ok=True,identity=identity,con_id=42)
    if problem=='identity':quote['identity']={}
    monkeypatch.setattr(broker,'stock_quote',lambda *_:quote)
    from broker_fixtures import deadline
    result=broker.place_from_desk_order({'ticker':'TEST','side':'buy','shares':1,'signal_id':'guard-'+problem,
        'broker_identity':identity,'valid_until':deadline(),
        'risk_authorization':{'equity':100000.,'day_pnl':-250.,'reducing':False}})
    assert not result['ok'] and result['submission_attempted'] is False and not gateway.trades,result


def test_clock_drift_and_queue_priority(monkeypatch):
    monkeypatch.setattr(broker,'_API_PULSE',{});monkeypatch.setattr(broker,'_SERVER_UNAVAILABLE',False);monkeypatch.setattr(broker,'_RESYNC_REQUIRED',False)
    ib=NS(isConnected=lambda:True,reqCurrentTime=lambda:datetime.now(timezone.utc)+timedelta(seconds=30))
    assert not broker._api_responsive(ib)
    assert broker._API_PULSE['clock_error']
    assert not broker._api_responsive(ib),'cached pulse must retain the clock blocker'
    assert broker._task_priority('_poll',(),{'cancel':True})<broker._task_priority('place_from_desk_order',(),{})<broker._task_priority('stock_quote',(),{})
    for n in [1,2,4,10]:
        base=min(600,60*2**min(n-1,4))
        assert base<=broker._soft_reconnect_backoff_sec(n)<=min(600,base*1.2)


def test_stream_callbacks_survive_packet_tick_list_clearing(gateway, monkeypatch):
    from eventkit import Event
    ticker=ticks();ticker.ticks=[];ticker.updateEvent=Event('stock')
    monkeypatch.setattr(gateway,'reqMktData',lambda *a:ticker)
    def sleep(_):
        ticker.ticks=ticks().ticks
        ticker.updateEvent.emit(ticker)
        ticker.ticks=[]  # ib_insync clears per-packet ticks, not the accumulated receipt.
    monkeypatch.setattr(gateway,'sleep',sleep)
    q=broker.stock_quote('TEST')
    assert q['ok'] and q['fresh'] and q['bid']==100 and q['ask']==100.02,q
    assert len(ticker.updateEvent)==0,'temporary subscription callback must be removed'


def test_account_change_while_final_quote_waits_blocks_dispatch(gateway,monkeypatch):
    identity=broker.get_account()['identity'];t=ticks();seen={};broker._capture_stock_ticks(t,seen)
    def changed(_):
        broker._ACCOUNT_EQUITY[(id(gateway),gateway.account)]=50000
        return dict(broker._stock_tick_quote(t,seen),ok=True,identity=identity,con_id=42)
    monkeypatch.setattr(broker,'stock_quote',changed)
    from broker_fixtures import deadline
    r=broker.place_from_desk_order({'ticker':'TEST','side':'buy','shares':1,'signal_id':'race',
        'broker_identity':identity,'valid_until':deadline(),'risk_authorization':{'equity':100000.,'day_pnl':-250.,'reducing':False}})
    assert not r['ok'] and not r['submission_attempted'] and not gateway.trades
    assert 'equity changed' in r['error']
