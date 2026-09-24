"""Exercise reviewed limits and cancel/fill races with broker writes replaced."""
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
import app as desk
import broker_alpaca as alpaca
import broker_ibkr as ibkr
from broker_fixtures import IDENTITY, deadline
from test_execution_repairs import execution, gateway

BASE = "http://127.0.0.1:5056"


def review(client, **options):
    result = client.post("/api/signals/review/review", base_url=BASE, json={"order":options})
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def test_reviewed_limit_not_overridden_by_approval_fields(execution):
    client=desk.app.test_client()
    ticket=review(client,type="limit",limit_price=101)
    assert ticket["order"]["notional_bound"]==1010
    result=client.post("/api/signals/review/approve",base_url=BASE,json={
        "review_token":ticket["review_token"],"ack_ticker":"TEST", "type":"market","limit_price":9999,"shares":100000})
    assert result.status_code==200, result.get_json()
    order=execution[2][0]
    assert order["type"]=="limit" and order["limit"]==101 and order["shares"]==10


def test_quantity_reduction_requires_another_review(execution, monkeypatch):
    client=desk.app.test_client();ticket=review(client,type="limit",limit_price=100)
    monkeypatch.setattr(desk,"_cap_shares_for_broker",lambda *a,**k:(3,300,None))
    result=client.post("/api/signals/review/approve",base_url=BASE,json={"review_token":ticket["review_token"],"ack_ticker":"TEST"})
    assert result.status_code==400 and "quantity" in result.get_json()["error"]
    assert not execution[2]


def test_unfilled_limit_survives_short_wait_and_reconciles_later(execution,monkeypatch):
    sig=execution[1]
    sig["review_order"]={"type":"limit","limit":99,"shares":10,"time_in_force":"day"}
    monkeypatch.setattr(alpaca,"wait_for_fill",lambda *a,**k:{"state":"pending","terminal":False})
    monkeypatch.setattr(alpaca,"reconcile_after_timeout",lambda *a,**k:pytest.fail("Do not cancel a DAY limit after six seconds"))
    result=desk.execute_gated_broker_or_paper(sig,execution[0],source="test",via="test")
    assert result["pending"] and not result["ok"]
    assert len(desk.load_ledger()["pending_broker_orders"])==1
    monkeypatch.setattr(alpaca,"wait_for_fill",lambda *a,**k:{"state":"filled","terminal":True,"filled_qty":10,"filled_avg_price":98.5})
    desk._reconcile_pending_broker_orders()
    ledger=desk.load_ledger()
    assert not ledger["pending_broker_orders"] and ledger["broker_fills"][0]["price"]==98.5


def test_cancel_review_blocks_account_change_and_records_racing_fill(execution,monkeypatch):
    sig=execution[1];sig["review_order"]={"type":"limit","limit":99,"shares":10}
    monkeypatch.setattr(alpaca,"wait_for_fill",lambda *a,**k:{"state":"pending","terminal":False})
    desk.execute_gated_broker_or_paper(sig,execution[0],source="test",via="test")
    client=desk.app.test_client()
    bad=client.post("/api/signals/review/cancel",base_url=BASE,json={"review_token":"forged"})
    assert bad.status_code==409
    result=client.post("/api/signals/review/cancel-review",base_url=BASE,json={})
    assert result.status_code==200,result.get_json()
    token=result.get_json()["review_token"]
    calls=[]
    monkeypatch.setattr(alpaca,"cancel_reviewed_order",lambda order_id,identity,expires:calls.append((order_id,identity)) or {
        "state":"partially_filled","terminal":True,"alpaca_status":"canceled","filled_qty":3,"filled_avg_price":99})
    assert client.post("/api/signals/review/cancel",base_url=BASE,json={"review_token":token,"ack_ticker":"WRONG"}).status_code==400
    result=client.post("/api/signals/review/cancel",base_url=BASE,json={"review_token":token,"ack_ticker":"TEST"})
    assert result.status_code==200 and calls==[("order-1",IDENTITY)]
    ledger=desk.load_ledger()
    assert not ledger["pending_broker_orders"] and ledger["broker_fills"][0]["shares"]==3
    assert client.post("/api/signals/review/cancel",base_url=BASE,json={"review_token":token,"ack_ticker":"TEST"}).status_code==409


def test_alpaca_cancel_cannot_cross_account(monkeypatch):
    monkeypatch.setattr(alpaca,"verify_execution_context",lambda:{"ok":True,"identity":dict(IDENTITY,account_id="OTHER")})
    monkeypatch.setattr(alpaca,"cancel_order",lambda *a:pytest.fail("No cancellation on another account"))
    result=alpaca.cancel_reviewed_order("o",IDENTITY,__import__('time').time()+60)
    assert not result["terminal"] and "account" in result["error"]


def test_ibkr_sends_actual_day_limit_with_account_and_reference(monkeypatch, gateway):
    identity=ibkr.get_account()["identity"]
    placed=[]
    def place(contract,order):
        placed.append(order);order.orderId=42;order.clientId=37
        return NS(order=order)
    monkeypatch.setattr(gateway,"placeOrder",place)
    result=ibkr.place_from_desk_order({"ticker":"TEST","side":"buy","shares":2,"type":"limit","limit":4.25,
             "signal_id":"limit-ref","broker_identity":identity,"valid_until":deadline(),
             "risk_authorization":{"equity":100000.,"day_pnl":-250.,"reducing":False}})
    assert result["ok"],result
    native=placed[0]
    assert native.orderType=="LMT" and native.lmtPrice==4.25 and native.tif=="DAY"
    assert native.account=="DU123" and native.orderRef=="limit-ref" and native.outsideRth is False


@pytest.mark.parametrize("kind,limit",[("typo",5),("limit",float('nan')),("limit",-1)])
def test_alpaca_rejects_invalid_type_and_price_without_post(monkeypatch,kind,limit):
    monkeypatch.setattr(alpaca,"_request",lambda *a,**k:pytest.fail("No broker POST"))
    result=alpaca.place_from_desk_order({"ticker":"TEST","side":"buy","shares":1,"type":kind,"limit":limit})
    assert not result["ok"] and result["submission_attempted"] is False
