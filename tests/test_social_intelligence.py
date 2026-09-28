from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_intelligence
import pytest


@pytest.fixture(autouse=True)
def reset_provider_backoff(monkeypatch):
    monkeypatch.setattr(social_intelligence, '_cache', {'at': 0, 'payload': None})
    monkeypatch.setattr(social_intelligence, '_source_status', {})
    monkeypatch.setattr(social_intelligence, '_stocktwits_retry', {'until': 0.0, 'error': None})


def test_stocktwits_handles_nested_likes_and_missing_optional_objects(monkeypatch):
    class Response:
        status_code = 200
        def json(self):
            return {'messages': [{'id': 123, 'body': '$AAPL bullish', 'likes': {'total': 4},
                                  'conversation': None, 'user': None, 'entities': None},
                                 {'id': 124, 'body': 'more $AAPL', 'likes': 'invalid'}]}
    monkeypatch.setattr(social_intelligence.requests, 'get', lambda *a, **k: Response())
    result = social_intelligence._stocktwits_symbol('AAPL', 12)
    assert len(result) == 2 and [r['score'] for r in result] == [4, 0]
    assert result[0]['thread_id'] == ''


def test_social_errors_identify_the_provider_and_do_not_hide_other_sources(monkeypatch):
    monkeypatch.setattr(social_intelligence, '_stocktwits_rows', lambda *a: (_ for _ in ()).throw(TypeError('bad format')))
    monkeypatch.setattr(social_intelligence, '_reddit_rows', lambda *a: [])
    monkeypatch.setattr(social_intelligence, '_rss_rows', lambda *a: [])
    monkeypatch.setattr(social_intelligence, '_x_rows', lambda *a: [])
    result = social_intelligence.snapshot(['AAPL'], force=True)
    assert 'TypeError' in result['source_status']['stocktwits']['error']
    assert 'unexpected' not in result['source_status']


def test_stocktwits_denial_backs_off_without_changing_trading(monkeypatch):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(social_intelligence.requests, 'get', lambda *a, **k: calls.append(a) or SimpleNamespace(status_code=403))
    monkeypatch.setattr(social_intelligence.time, 'time', lambda: 1000)
    assert social_intelligence._stocktwits_symbol('AAPL', 12) is None
    assert social_intelligence._stocktwits_symbol('MSFT', 12) is None
    assert len(calls) == 1 and social_intelligence._stocktwits_retry['until'] == 4600


def test_social_snapshot_normalizes_and_summarizes_rows(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"data": {"children": [
                {"data": {
                    "id": "abc",
                    "author": "trader",
                    "title": "$AAPL breakout looks bullish",
                    "selftext": "calls and upside",
                    "created_utc": time.time() - 60,
                    "score": 100,
                    "num_comments": 20,
                    "permalink": "/r/wallstreetbets/comments/abc/x/",
                }},
                {"data": {
                    "id": "def",
                    "author": "other",
                    "title": "$AAPL puts bearish",
                    "selftext": "downside risk",
                    "created_utc": time.time() - 50,
                    "score": 20,
                    "num_comments": 4,
                    "permalink": "/r/wallstreetbets/comments/def/y/",
                }},
            ]}}

    monkeypatch.setattr(social_intelligence.requests, "get", lambda *a, **k: Response())
    monkeypatch.setenv("SOCIAL_SUBREDDITS", "wallstreetbets")
    # Reddit goes through buzz_sources: OAuth when configured, public JSON only by opt-in.
    for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "X_BEARER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REDDIT_PUBLIC_JSON", "1")
    import buzz_sources
    monkeypatch.setattr(buzz_sources, '_reddit_responses', {})
    monkeypatch.setattr(buzz_sources, '_reddit_retry_at', 0)
    monkeypatch.setattr(social_intelligence, '_rss_rows', lambda *a: [])
    monkeypatch.setattr(social_intelligence, '_stocktwits_rows', lambda *a: [])
    monkeypatch.setattr(social_intelligence, '_x_rows', lambda *a: [])
    result = social_intelligence.snapshot(["AAPL"], force=True)
    assert result["ok"] is True
    assert result["pulse"][0]["ticker"] == "AAPL"
    assert result["pulse"][0]["mentions"] == 2
    assert result["pulse"][0]["unique_authors"] == 2
    assert result["pulse"][0]["bullish"] == 1
    assert result["pulse"][0]["bearish"] == 1
    assert result["display_only"] is True


def test_ticker_extraction_avoids_common_words():
    assert social_intelligence._tickers("THE CEO says $MSFT is strong") == ["MSFT"]


def test_social_sources_are_combined(monkeypatch):
    monkeypatch.setattr(social_intelligence, "_x_rows", lambda *a: [])
    monkeypatch.setenv("SOCIAL_SUBREDDITS", "wallstreetbets")
    monkeypatch.setattr(social_intelligence, "_reddit_rows", lambda subreddit: [{
        "source": f"reddit_{subreddit}", "ticker_candidates": ["AAPL"],
        "created_at": time.time() - 10, "title": subreddit, "excerpt": "bullish", "sentiment": "bullish",
        "author_hash": subreddit, "score": 1, "comments": 0,
    }])
    monkeypatch.setattr(social_intelligence, "_stocktwits_rows", lambda symbols: [{
        "source": "stocktwits", "ticker_candidates": ["AAPL"],
        "created_at": time.time() - 20, "title": "message", "excerpt": "bearish", "sentiment": "bearish",
        "author_hash": "tw", "score": 2, "comments": 0,
    }])
    monkeypatch.setattr(social_intelligence, "_rss_rows", lambda feeds: [{
        "source": "public_rss", "ticker_candidates": ["AAPL"],
        "created_at": time.time() - 30, "title": "news", "excerpt": "uncertain", "sentiment": "uncertain",
        "author_hash": "rss", "score": 0, "comments": 0,
    }])
    result = social_intelligence.snapshot(["AAPL"], force=True)
    assert result["source"] == "reddit_stocktwits_rss"
    assert result["source_counts"]["stocktwits"] == 1
    assert result["source_counts"]["public_rss"] == 1
    assert len(result["pulse"]) == 1
