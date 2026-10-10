import copy
import math
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
import social_intelligence as social
import market_watch as market
import data_sources


def row(**kw):
    return dict(dict(source='reddit_stocks', source_id='1', created_at=time.time()-60,
        title='$AAPL bullish', excerpt='', sentiment='bullish', ticker_candidates=['AAPL'],
        author_hash='author', score=0, comments=0), **kw)


def test_sample_excludes_stale_missing_future_and_deduplicates_cross_symbol_messages():
    now=time.time()
    rows=[row(), row(), row(source_id='old', created_at=now-social.WINDOW_SEC-1),
          row(source_id='missing', created_at=None), row(source_id='future', created_at=now+3600),
          row(source='stocktwits', source_id='2', ticker_candidates=['AAPL']),
          row(source='stocktwits', source_id='2', ticker_candidates=['MSFT'])]
    saved=copy.deepcopy(rows)
    recent, excluded=social._recent_unique(rows,now)
    assert rows == saved
    assert len(recent)==2 and excluded == dict(stale=1, undated_or_future=2, duplicates=2)
    assert next(r for r in recent if r['source']=='stocktwits')['ticker_candidates']==['AAPL','MSFT']
    pulse=social._summarize(recent,['AAPL','MSFT'])['pulse']
    apple=next(p for p in pulse if p['ticker']=='AAPL')
    assert apple['mentions']==2 and apple['bullish']==1 and apple['uncertain']==1
    microsoft=next(p for p in pulse if p['ticker']=='MSFT')
    assert microsoft['mentions']==1 and microsoft['uncertain']==1


def test_headlines_unknown_accounts_and_repeated_author_are_not_extra_people_or_votes():
    rows=[row(),row(source='reddit_options',source_id='2'),
          row(source='stocktwits',source_id='3'),
          row(source_id='4',author_hash=None),
          row(source='public_rss',source_id='5',author_hash='feed',sentiment='bearish')]
    recent,_=social._recent_unique(rows,time.time())
    p=social._summarize(recent,['AAPL'])['pulse'][0]
    assert p['unique_authors']==2 and p['unique_threads']==4
    assert p['post_mentions']==4 and p['headline_mentions']==1
    assert p['bullish']==4 and p['bearish']==0 and p['uncertain']==0
    assert p['contributor_share']==0.5
    assert all(social._author_hash(v) is None for v in [None,'','[deleted]','[unknown]',0])


def test_snapshot_cache_cannot_be_mutated_and_rejects_clock_rollback(monkeypatch):
    calls=[]
    monkeypatch.setenv('SOCIAL_SUBREDDITS','stocks')
    monkeypatch.setattr(social,'_cache',{'at':0,'payload':None})
    monkeypatch.setattr(social,'_reddit_rows',lambda *a: calls.append(1) or [row()])
    for name in ('_stocktwits_rows','_rss_rows','_x_rows'):
        monkeypatch.setattr(social,name,lambda *a: [])
    first=social.snapshot(['AAPL'])
    first['pulse'][0]['bullish']=1234
    assert social.snapshot(['AAPL'])['pulse'][0]['bullish']==1 and len(calls)==1
    social._cache['at']=time.time()+300
    social.snapshot(['AAPL'])
    assert len(calls)==2


@pytest.fixture
def quote_client(monkeypatch):
    monkeypatch.setattr(market,'_quote_cache',{})
    monkeypatch.setattr(market,'_quote_next_at',0)
    app=Flask(__name__)
    market.register(app,SimpleNamespace(load_config=lambda:{}))
    monkeypatch.setattr(data_sources,'latest_quote',lambda *a: pytest.fail('Unexpected quote request'))
    return app.test_client()


def test_price_endpoint_reuses_provider_observation_and_handles_stale_time(quote_client,monkeypatch):
    calls=[]
    q=data_sources.quote_snapshot(123,time.time()-3600,'Test provider')
    monkeypatch.setattr(data_sources,'latest_quote',lambda symbol: calls.append(symbol) or q)
    first=quote_client.get('/api/market-watch/quote?symbol=aapl').get_json()
    assert first['ok'] and first['price']==123 and not first['fresh']
    assert first['display_only'] and first['currency'] is None
    first['price']=9
    second=quote_client.get('/api/market-watch/quote?symbol=AAPL').get_json()
    assert calls==['AAPL'] and second['price']==123 and second['from_cache']
    assert second['market_time']==q['market_time'] and second['age_sec']>=3600
    assert quote_client.get('/api/market-watch/quote?symbol=MSFT').status_code==429


@pytest.mark.parametrize('bad',['../config','https://evil.test','', 'AAPL%26x=1'])
def test_invalid_price_symbol_never_reaches_provider(quote_client,bad):
    assert quote_client.get('/api/market-watch/quote',query_string={'symbol':bad}).status_code==400


@pytest.mark.parametrize('value,stamp',[(float('nan'),None),(12,None),(12,time.time()+3600)])
def test_invalid_or_unverified_quotes_never_claim_fresh(quote_client,monkeypatch,value,stamp):
    q=data_sources.quote_snapshot(value,stamp,'Provider')
    monkeypatch.setattr(data_sources,'latest_quote',lambda *a:q)
    r=quote_client.get('/api/market-watch/quote?symbol=AAPL').get_json()
    assert not r['fresh']
    if not math.isfinite(value):assert r['price'] is None and not r['ok']


def test_price_failures_are_cached_and_busy_requests_do_not_start_more_reads(quote_client,monkeypatch):
    calls=[]
    def fail(*a):
        calls.append(1)
        raise RuntimeError('provider detail must not leak')
    monkeypatch.setattr(data_sources,'latest_quote',fail)
    r=quote_client.get('/api/market-watch/quote?symbol=AAPL').get_json()
    assert not r['ok'] and 'provider detail' not in str(r)
    quote_client.get('/api/market-watch/quote?symbol=AAPL')
    assert len(calls)==1
    with market._quote_lock:
        assert quote_client.get('/api/market-watch/quote?symbol=MSFT').status_code==429
    assert len(calls)==1
