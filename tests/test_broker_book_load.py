"""Real UI-cache contention with simulated providers and no broker transport."""
from concurrent.futures import ThreadPoolExecutor,wait
import copy
import threading
import time
import pytest
import app as desk
import broker_alpaca as adapter


@pytest.fixture
def book(monkeypatch):
    cfg={'broker_identity':{'account_id':'PAPER_A'}}
    monkeypatch.setattr(desk,'load_config',lambda:copy.deepcopy(cfg))
    monkeypatch.setattr(desk,'_broker_is_configured',lambda:True)
    monkeypatch.setattr(desk,'_BROKER_BOOK_CACHE',{'at':0.,'val':None})
    monkeypatch.setattr(desk,'_note_day_pnl_peak',lambda *a:None)
    monkeypatch.setattr(adapter,'public_status',lambda:{'broker':'alpaca','endpoint':'paper.test','paper_mode':True,'connected':True})
    monkeypatch.setattr(adapter,'paper_mode',lambda:True)
    acct={'ok':True,'risk_ready':True,'account_id':'PAPER_A','account':{'equity':1000,'day_pnl':2}}
    positions={'ok':True,'positions':[{'symbol':'TEST','qty':1,'avg_entry_price':10,'current_price':11,'unrealized_pl':1}]}
    monkeypatch.setattr(adapter,'get_account',lambda:copy.deepcopy(acct))
    monkeypatch.setattr(adapter,'get_positions',lambda:copy.deepcopy(positions))
    return cfg,acct,positions


def test_cold_burst_has_one_refresh_and_other_callers_do_not_wait(book,monkeypatch):
    entered,release=threading.Event(),threading.Event();calls=[]
    def account():
        calls.append(threading.get_ident());entered.set();assert release.wait(3)
        return copy.deepcopy(book[1])
    monkeypatch.setattr(adapter,'get_account',account)
    with ThreadPoolExecutor(12) as pool:
        owner=pool.submit(desk._broker_book_cached);assert entered.wait(1)
        followers=[pool.submit(desk._broker_book_cached) for _ in range(11)]
        try:
            done,_=wait(followers,timeout=.5)
            assert len(done)==11,'A slow refresh must not occupy every HTTP caller'
            for future in done:
                result=future.result();assert result['ok'] is False and result['risk_ready'] is False
                assert result.get('day_pnl_usd') is None and result.get('account_id') is None
        finally:release.set()
        assert owner.result(2)['ok']
    assert len(calls)==1
    assert desk._broker_book_cached()['ok'] and len(calls)==1


def test_cached_result_cannot_be_mutated_by_a_caller(book):
    first=desk._broker_book_cached();first['positions'][0]['shares']=999;first['risk_ready']=False
    second=desk._broker_book_cached()
    assert second['positions'][0]['shares']==1 and second['risk_ready'] is True
    assert '_note_peak' not in first and '_note_peak' not in second


def test_expired_success_is_not_returned_as_ready_while_refresh_runs(book,monkeypatch):
    desk._broker_book_cached();desk._BROKER_BOOK_CACHE['at']=time.monotonic()-60
    entered,release=threading.Event(),threading.Event()
    def slow():
        entered.set();assert release.wait(2);return book[1]
    monkeypatch.setattr(adapter,'get_account',slow)
    with ThreadPoolExecutor(1) as pool:
        pending=pool.submit(desk._broker_book_cached);assert entered.wait(1)
        try:
            result=desk._broker_book_cached()
            assert not result['ok'] and not result['risk_ready'] and result['day_pnl_usd'] is None
        finally:release.set()
        assert pending.result(1)['ok']


def test_account_change_during_refresh_discards_old_values_and_peak(book,monkeypatch):
    peaks=[]
    monkeypatch.setattr(desk,'_note_day_pnl_peak',peaks.append)
    def account():
        book[0]['broker_identity']['account_id']='PAPER_B'
        return book[1]
    monkeypatch.setattr(adapter,'get_account',account)
    result=desk._broker_book_cached()
    assert not result['ok'] and result.get('equity') is None and result['risk_ready'] is False
    assert not peaks and desk._BROKER_BOOK_CACHE['val'] is None


def test_refresh_invalidation_discards_an_inflight_answer(book,monkeypatch):
    def account():
        desk._invalidate_broker_book_cache()
        return book[1]
    monkeypatch.setattr(adapter,'get_account',account)
    result=desk._broker_book_cached()
    assert not result['ok'] and result['risk_ready'] is False and desk._BROKER_BOOK_CACHE['val'] is None


def test_provider_error_releases_refresh_owner_and_next_request_can_recover(book,monkeypatch):
    monkeypatch.setattr(adapter,'get_account',lambda:(_ for _ in ()).throw(ConnectionError('offline')))
    first=desk._broker_book_cached();assert not first['ok']
    desk._invalidate_broker_book_cache()
    monkeypatch.setattr(adapter,'get_account',lambda:book[1])
    assert desk._broker_book_cached()['ok']


def test_failed_account_does_not_queue_a_redundant_positions_read(book,monkeypatch):
    monkeypatch.setattr(adapter,'get_account',lambda:{'ok':False,'error':'unavailable'})
    monkeypatch.setattr(adapter,'get_positions',lambda:pytest.fail('No account result to associate with positions'))
    result=desk._broker_book_cached()
    assert not result['ok'] and result.get('risk_ready') is False
