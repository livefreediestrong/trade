import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime

import pytest
import requests
import community_feeds as feeds


class Response:
    def __init__(self, payload=None, status=200, headers=None, body=None):
        self.status_code, self.headers = status, headers or {}
        self.body = body if body is not None else json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, n):
        for at in range(0, len(self.body), n): yield self.body[at:at+n]


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(feeds, '_cache', {})
    monkeypatch.setenv('COMMUNITY_FEEDS_ENABLED', '1')
    def denied(*args, **kwargs): raise AssertionError('Unexpected network read')
    monkeypatch.setattr(feeds.requests, 'get', denied)


def sample(name):
    if name == 'tradestie':
        return [{'ticker':'MU','sentiment':'Bearish','sentiment_score':-.2,'no_of_comments':18}]
    return {'results':[{'ticker':'MU','mentions':'93','rank':1,'rank_24h_ago':2,'mentions_24h_ago':0,'upvotes':308}]}


@pytest.mark.parametrize('name', list(feeds.PROVIDERS))
def test_valid_response_keeps_unknown_update_time_and_cache_is_private(monkeypatch, name):
    calls = []
    monkeypatch.setattr(feeds.requests, 'get', lambda *a, **k: calls.append((a,k)) or Response(sample(name)))
    first = feeds._read(name)
    assert first['ok'] and first['fetched_at'] and first['source_updated_at'] is None
    assert first['freshness'] == 'unverified'
    first['rows'][0]['symbol'] = 'CHANGED'
    second = feeds._read(name)
    assert second['cached'] and second['rows'][0]['symbol'] == 'MU' and len(calls) == 1
    assert calls[0][1]['allow_redirects'] is False
    assert 'params' not in calls[0][1] and 'Authorization' not in calls[0][1]['headers']


@pytest.mark.parametrize('count', [None, True, -1, 'nan', 'inf', 1.5, {}, 'bad'])
def test_bad_counts_are_not_zero_activity(count):
    with pytest.raises(ValueError):
        feeds._normalize('tradestie',[{'ticker':'MU','no_of_comments':count}])


def test_duplicates_invalid_symbols_and_unknown_direction_are_honest():
    rows, rejected = feeds._normalize('tradestie',[
        {'ticker':'MU','no_of_comments':1,'sentiment':'surprise','sentiment_score':'nan'},
        {'ticker':'MU','no_of_comments':10}, {'ticker':'<script>','no_of_comments':3}, None])
    assert rejected == 3 and rows == [{'symbol':'MU','count':1,'tone':'unclear','score':None}]


@pytest.mark.parametrize('status', [301, 401, 402, 403, 429, 500])
def test_errors_back_off_and_cannot_leave_old_rows_as_current(monkeypatch, status):
    clock = [1000000.0]
    monkeypatch.setattr(feeds.time, 'time', lambda: clock[0])
    calls=[]
    monkeypatch.setattr(feeds.requests, 'get', lambda *a, **k: calls.append(1) or Response(sample('tradestie')))
    assert feeds._read('tradestie')['ok']
    clock[0] += feeds.TTL + 1
    monkeypatch.setattr(feeds.requests, 'get', lambda *a, **k: calls.append(1) or Response(status=status,headers={'Retry-After':'7200'}))
    bad = feeds._read('tradestie')
    assert not bad['ok'] and bad['rows'] == [] and bad['fetched_at'] is None and bad['last_success_at']
    assert feeds._cache['tradestie']['next_ts'] >= clock[0]+7200
    assert feeds._read('tradestie')['cached'] and len(calls) == 2


@pytest.mark.parametrize('body', [b'<html>blocked</html>', b'{}', b'null', b'X'*(feeds.MAX_BYTES+1)], ids=['html','object','null','oversized'])
def test_invalid_or_oversized_response_is_failure(monkeypatch, body):
    monkeypatch.setattr(feeds.requests,'get',lambda *a,**k:Response(body=body))
    result=feeds._read('tradestie')
    assert not result['ok'] and result['rows']==[]


def test_independent_provider_failure_does_not_hide_working_provider(monkeypatch):
    def get(url,**kwargs):
        if 'tradestie' in url: raise requests.Timeout('private diagnostic URL')
        return Response(sample('apewisdom'))
    monkeypatch.setattr(feeds.requests,'get',get)
    result=feeds.snapshot()
    assert result['providers']['apewisdom']['ok']
    assert not result['providers']['tradestie']['ok']
    assert 'private diagnostic' not in str(result)
    assert result['display_only'] and 'pulse' not in result


def test_disabled_never_reads(monkeypatch):
    assert feeds.snapshot(enabled=False)['providers']=={}
    monkeypatch.setenv('COMMUNITY_FEEDS_ENABLED','0')
    assert not feeds.snapshot()['enabled']


def test_parallel_consumers_share_single_read(monkeypatch):
    entered, release = threading.Event(),threading.Event()
    calls=[]
    def get(*a,**k):
        calls.append(1);entered.set();assert release.wait(2)
        return Response(sample('tradestie'))
    monkeypatch.setattr(feeds.requests,'get',get)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first=pool.submit(feeds._read,'tradestie');assert entered.wait(2)
        other=pool.submit(feeds._read,'tradestie');release.set()
        assert first.result()['ok'] and other.result()['ok']
    assert len(calls)==1


def test_retry_after_http_date_and_nan():
    date=format_datetime(datetime.fromtimestamp(1000000+3600,timezone.utc))
    assert feeds._retry_after(Response(headers={'Retry-After':date}),1000000)==3600
    assert feeds._retry_after(Response(headers={'Retry-After':'nan'}),1000000)==0


def test_market_watch_respects_social_setting_and_does_not_merge_aggregates(monkeypatch):
    import market_watch as market
    import social_intelligence
    import x_watcher
    from types import SimpleNamespace
    monkeypatch.setattr(market,'google_headlines',lambda now:([],{}))
    monkeypatch.setattr(market,'custom_headlines',lambda now:([],{}))
    monkeypatch.setattr(market,'trend_scan',lambda *a:{})
    monkeypatch.setattr(market,'wsb_snapshot',lambda:{})
    monkeypatch.setattr(market,'listing_index',lambda d:SimpleNamespace(info=lambda s:{}))
    monkeypatch.setattr(x_watcher,'poll',lambda *a,**k:None)
    monkeypatch.setattr(x_watcher,'social_rows',lambda *a,**k:[])
    monkeypatch.setattr(x_watcher,'status',lambda:{})
    monkeypatch.setattr(social_intelligence,'snapshot',lambda *a:{'pulse':[]})
    monkeypatch.setattr(feeds.requests,'get',lambda url,**k:Response(sample('tradestie' if 'tradestie' in url else 'apewisdom')))
    enabled=market._build(None,{'social_enabled':True},[],1000000)
    assert len(enabled['community']['providers'])==2 and enabled['social']['pulse']==[]
    disabled=market._build(None,{'social_enabled':False},[],1000000)
    assert not disabled['community']['enabled'] and disabled['community']['providers']=={}
