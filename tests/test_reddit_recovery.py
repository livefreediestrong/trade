import copy
import json
import threading
import time

import pytest

import buzz_sources as buzz


class Reply:
    def __init__(self, code=200, body=None, headers=None):
        self.status_code, self.body, self.headers = code, body, headers or {}
        self.content = b'json'

    def json(self):
        return copy.deepcopy(self.body)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for key in ('REDDIT_CLIENT_ID', 'REDDIT_CLIENT_SECRET', 'REDDIT_USERNAME', 'REDDIT_PASSWORD', 'REDDIT_REFRESH_TOKEN', 'REDDIT_PUBLIC_JSON'):
        monkeypatch.delenv(key, raising=False)
    for key, value in {'_oauth_token': None, '_oauth_mode': None, '_oauth_expires_at': 0,
                       '_oauth_last_error': None, '_reddit_degraded_reason': None, '_reddit_retry_at': 0,
                       '_reddit_last_ok': 0, '_reddit_failures': 0, '_reddit_responses': {},
                       '_mem_cache': None, '_mem_cache_key': None, '_mem_cached_at': 0}.items():
        monkeypatch.setattr(buzz, key, value)
    monkeypatch.setattr(buzz, 'CACHE_PATH', tmp_path/'buzz.json')
    monkeypatch.setattr(buzz.requests, 'get', lambda *a, **k: pytest.fail('Unexpected network GET'))
    monkeypatch.setattr(buzz.requests, 'post', lambda *a, **k: pytest.fail('Unexpected token call'))


def credentials(monkeypatch):
    monkeypatch.setenv('REDDIT_CLIENT_ID', 'own-client')
    monkeypatch.setenv('REDDIT_CLIENT_SECRET', 'private-test-secret')


def token(monkeypatch):
    credentials(monkeypatch)
    monkeypatch.setattr(buzz.requests, 'post', lambda *a, **k: Reply(body={'access_token': 'token', 'expires_in': 3600}))


def test_missing_credentials_is_not_a_public_connection():
    state = buzz.reddit_auth_status()
    assert state['mode'] == 'disabled' and state['state'] == 'needs_credentials'
    assert buzz._reddit_get('/r/stocks/hot')[0] is None
    assert 'REDDIT_CLIENT_ID' in buzz._reddit_get('/r/stocks/hot')[1]


def test_401_renews_once_and_all_readers_share_success(monkeypatch):
    credentials(monkeypatch)
    tokens, reads = [], []
    def issue(*args, **kwargs):
        tokens.append(1)
        return Reply(body={'access_token': f'token{len(tokens)}', 'expires_in': 3600})
    def get(url, **kwargs):
        reads.append(kwargs['headers']['Authorization'])
        return Reply(401) if len(reads) == 1 else Reply(body={'data': {'children': []}})
    monkeypatch.setattr(buzz.requests, 'post', issue)
    monkeypatch.setattr(buzz.requests, 'get', get)
    first, error = buzz._reddit_get('/r/stocks/hot.json', params={'limit': 20})
    first['data']['children'].append('mutation')
    second, _ = buzz._reddit_get('/r/stocks/hot.json', params={'limit': 20})
    assert not error and second == {'data': {'children': []}}
    assert reads == ['bearer token1', 'bearer token2'] and len(tokens) == 2
    status = buzz.reddit_auth_status()
    assert status['state'] == 'connected'
    assert 'token1' not in json.dumps(status) and 'private-test-secret' not in json.dumps(status)


def test_429_holds_every_consumer_then_retries(monkeypatch):
    token(monkeypatch)
    clock = [1000.]
    monkeypatch.setattr(buzz.time, 'time', lambda: clock[0])
    calls = []
    def get(*args, **kwargs):
        calls.append(1)
        return Reply(429, headers={'Retry-After': '120'}) if len(calls) == 1 else Reply(body={'data': {}})
    monkeypatch.setattr(buzz.requests, 'get', get)
    assert buzz._reddit_get('/r/stocks/hot')[0] is None
    for sub in ('options', 'stocks', 'wallstreetbets'):
        assert buzz._reddit_get(f'/r/{sub}/new')[0] is None
    assert len(calls) == 1 and buzz.reddit_auth_status()['retry_in_sec'] == 120
    clock[0] += 121
    assert buzz._reddit_get('/r/stocks/hot')[0] == {'data': {}}
    assert buzz.reddit_auth_status()['state'] == 'connected'


def test_token_rejection_is_not_retried_for_each_feed(monkeypatch):
    credentials(monkeypatch)
    calls = []
    monkeypatch.setattr(buzz.requests, 'post', lambda *a, **k: calls.append(1) or Reply(403))
    monkeypatch.setenv('REDDIT_PUBLIC_JSON', '1')
    for _ in range(5):
        assert buzz._reddit_get('/r/stocks/hot')[0] is None
    assert len(calls) == 1 and buzz.reddit_auth_status()['state'] == 'cooldown'


def test_refresh_grant_and_secret_safe_errors(monkeypatch):
    credentials(monkeypatch)
    monkeypatch.setenv('REDDIT_REFRESH_TOKEN', 'private-refresh')
    calls = []
    def issue(*args, **kwargs):
        calls.append(kwargs['data'])
        raise RuntimeError('private-test-secret private-refresh')
    monkeypatch.setattr(buzz.requests, 'post', issue)
    buzz._reddit_get('/r/stocks/hot')
    assert calls[0]['grant_type'] == 'refresh_token'
    assert 'private-' not in json.dumps(buzz.reddit_auth_status())


def test_forbidden_host_never_receives_credentials(monkeypatch):
    token(monkeypatch)
    assert buzz._reddit_get('https://oauth.reddit.com.evil.test/r/stocks')[0] is None


def test_restart_restores_cache_key_without_provider_work(monkeypatch):
    now = buzz.time.time()
    key = buzz._buzz_cache_key(['AAPL'], False)
    buzz.CACHE_PATH.write_text(json.dumps({'cache_key': key, 'cached_at_epoch': now, 'focus_liquid': False, 'tickers': []}))
    monkeypatch.setattr(buzz, '_build_payload', lambda **kw: pytest.fail('Fresh disk cache must survive restart'))
    assert buzz.fetch_ticker_buzz(['AAPL'])['from_cache']


def test_buzz_route_never_waits_for_social_requests(monkeypatch):
    import app as desk
    monkeypatch.setattr(buzz, 'get_cached_buzz', lambda: None)
    monkeypatch.setattr(buzz, 'fetch_ticker_buzz', lambda *a, **kw: pytest.fail('Synchronous source fetch'))
    kicked = []
    monkeypatch.setattr(buzz, 'kick_background_refresh', lambda *a, **kw: kicked.append(1) or True)
    response = desk.app.test_client().get('/api/buzz?force=1', base_url='http://127.0.0.1:5056')
    assert response.status_code == 200 and response.get_json()['refreshing']
    assert kicked == [1]


def test_future_disk_cache_is_not_fresh(monkeypatch):
    buzz.CACHE_PATH.write_text(json.dumps({'cached_at_epoch': buzz.time.time()+3600, 'cache_key': 'all|'}))
    assert buzz.get_cached_buzz()['stale'] is True


def test_saved_auth_cannot_claim_a_connection_after_restart():
    buzz.CACHE_PATH.write_text(json.dumps({'cached_at_epoch': buzz.time.time(),
        'auth_mode': 'oauth_password', 'reddit_auth': {'mode': 'oauth_password', 'state': 'connected'}}))
    cached = buzz.get_cached_buzz()
    assert cached['auth_mode'] == 'disabled' and cached['reddit_auth']['state'] == 'needs_credentials'


def test_connection_status_does_not_wait_for_slow_token_request(monkeypatch):
    credentials(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    def slow(*a, **kw):
        entered.set(); assert release.wait(3)
        return Reply(body={'access_token': 'token', 'expires_in': 3600})
    monkeypatch.setattr(buzz.requests, 'post', slow)
    thread = threading.Thread(target=buzz._ensure_reddit_token)
    try:
        thread.start(); assert entered.wait(2)
        began = time.monotonic()
        assert buzz.reddit_auth_status()['state'] == 'ready'
        assert time.monotonic() - began < .5
    finally:
        release.set(); thread.join(3)


def test_concurrent_consumers_share_one_request(monkeypatch):
    token(monkeypatch)
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []
    def slow(*a, **kw):
        calls.append(1); entered.set()
        assert release.wait(3)
        return Reply(body={'data': {}})
    monkeypatch.setattr(buzz.requests, 'get', slow)
    threads = [threading.Thread(target=lambda: results.append(buzz._reddit_get('/r/stocks/hot'))) for _ in range(3)]
    try:
        threads[0].start(); assert entered.wait(2)
        for thread in threads[1:]: thread.start()
    finally:
        release.set()
        for thread in threads:
            if thread.ident: thread.join(3)
    assert len(calls) == 1 and len(results) == 3 and all(result[1] is None for result in results)
