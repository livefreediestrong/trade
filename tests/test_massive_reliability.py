from collections import OrderedDict, deque
from types import SimpleNamespace
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
import polygon_client as client


def response(status=200, data=None, headers=None):
    return SimpleNamespace(status_code=status,headers=headers or {},json=lambda:data or {'status':'OK','results':[]})


def test_access_check_is_bounded_and_keeps_endpoint_failures_separate(monkeypatch):
    monkeypatch.setenv('TOMAHAWK_NO_BG','0')
    monkeypatch.setattr(client,'_probe_after',0.)
    monkeypatch.setattr(client,'_probe_worker',None)
    calls=[]
    monkeypatch.setattr(client.requests,'get',lambda url,**k:calls.append(url) or response(403 if 'snapshot' in url else 200))
    assert client.maybe_check_access()
    client._probe_worker.join(timeout=3)
    assert not client._probe_worker.is_alive() and not client.maybe_check_access()
    assert len(calls)==2
    status=client.public_status()
    assert status['endpoints']['aggregates']['state']=='available'
    assert status['endpoints']['snapshot']['state']=='access_denied'
    assert not status['plan_verified']


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv('MASSIVE_API_KEY','test-new-key')
    monkeypatch.setenv('POLYGON_API_KEY','test-old-key')
    monkeypatch.setenv('MASSIVE_STOCKS_PLAN','unknown')
    monkeypatch.setenv('MASSIVE_MAX_REQUESTS_PER_MINUTE','5')
    monkeypatch.setattr(client,'_key_fingerprint',None)
    monkeypatch.setattr(client,'_responses',OrderedDict())
    monkeypatch.setattr(client,'_requests_at',deque())
    monkeypatch.setattr(client,'_blocked',{})
    monkeypatch.setattr(client,'_endpoint_status',{})
    monkeypatch.setattr(client,'_status',{'state':'not_checked','error':None,'checked_at':None})
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:(_ for _ in ()).throw(AssertionError('Unmocked network')))


def test_both_key_names_and_no_key_disclosure(monkeypatch):
    import api_providers
    calls=[]
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:calls.append((a,k)) or response())
    assert client._get('v2/test') is not None
    assert calls[0][0][0].startswith('https://api.massive.com/')
    assert calls[0][1]['headers']['Authorization']=='Bearer test-new-key'
    assert 'apiKey' not in calls[0][1]['params'] and 'test-new-key' not in str(client.public_status())
    monkeypatch.setenv('MASSIVE_API_KEY',' ')
    assert client.api_key()=='test-old-key'
    monkeypatch.setenv('POLYGON_API_KEY','')
    assert client._get('v2/test') is None
    assert not api_providers.configured_map()['polygon']
    monkeypatch.setenv('MASSIVE_API_KEY','test-only-new')
    assert api_providers.configured_map()['polygon']


@pytest.mark.parametrize('code,state',[(401,'authentication_required'),(402,'access_denied'),(403,'access_denied'),(429,'rate_limited'),(500,'unavailable')])
def test_denied_batch_cannot_fan_out_or_immediately_retry(monkeypatch,code,state):
    calls=[]
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:calls.append(1) or response(code,headers={'Retry-After':'7200'}))
    assert client.fetch_radar_movers(['AAPL','MSFT','MU'])==[]
    assert client.fetch_radar_movers(['NVDA','AMD'])==[]
    assert len(calls)==1 and client.public_status()['state']==state
    if code==429: assert client._blocked['all']-client.time.monotonic()>7100


def test_denied_snapshots_do_not_block_historical_bars(monkeypatch):
    calls=[]
    monkeypatch.setattr(client.requests,'get',lambda url,**k:calls.append(url) or response(403 if 'snapshot' in url else 200))
    assert client.snapshot('AAPL') is None
    client.aggregates('AAPL')
    assert len(calls)==2
    status=client.public_status()
    assert status['endpoints']['snapshot']['state']=='access_denied'
    assert status['endpoints']['aggregates']['state']=='available'


def test_basic_skips_snapshot_and_plan_does_not_assert_live(monkeypatch):
    monkeypatch.setenv('MASSIVE_STOCKS_PLAN','basic')
    assert client.snapshot('AAPL') is None
    assert client.public_status()['state']=='plan_excluded'
    for plan,delay in [('starter','15_minutes'),('developer','15_minutes'),('advanced','real_time'),('invalid','unknown')]:
        monkeypatch.setenv('MASSIVE_STOCKS_PLAN',plan)
        status=client.public_status()
        assert status['declared_delay']==delay and not status['plan_verified']


def test_shared_budget_and_response_cache(monkeypatch):
    calls=[]; clock=[1000.]
    monkeypatch.setattr(client.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:calls.append(1) or response(data={'results':[{'c':100}]}))
    data=client._get('v2/one');data['results'][0]['c']=0
    assert client._get('v2/one')['results'][0]['c']==100
    for index in range(7):client._get(f'v2/test/{index}')
    assert len(calls)==5 and client.public_status()['state']=='rate_limited'
    clock[0]+=61
    assert client._get('v2/again') is not None and len(calls)==6


@pytest.mark.parametrize('data',[[],{'status':'ERROR','error':'secret key'}, {'status':'NOT_AUTHORIZED'}])
def test_http_200_provider_errors_are_not_available(monkeypatch,data):
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:SimpleNamespace(status_code=200,headers={},json=lambda:data))
    assert client._get('v2/bad') is None
    assert client.public_status()['state']=='unavailable' and 'secret key' not in str(client.public_status())


def test_slow_reader_does_not_block_status_or_spawn_requests(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    def get(*a,**k):
        entered.set();assert release.wait(2);return response()
    monkeypatch.setattr(client.requests,'get',get)
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker=pool.submit(client._get,'v2/one');assert entered.wait(2)
        try:
            assert client.public_status()['configured']
            assert client._get('v2/two') is None
        finally:release.set()
        assert worker.result() is not None


def test_timeout_is_sanitized_and_key_change_allows_recovery(monkeypatch):
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:(_ for _ in ()).throw(requests.Timeout('test-new-key')))
    assert client._get('v2/one') is None and 'test-new-key' not in str(client.public_status())
    monkeypatch.setenv('MASSIVE_API_KEY','replacement')
    monkeypatch.setattr(client.requests,'get',lambda *a,**k:response())
    assert client._get('v2/one') is not None
