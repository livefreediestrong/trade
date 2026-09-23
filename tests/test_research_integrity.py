from __future__ import annotations

import time

import news_intelligence
import news_stream
import social_intelligence
import x_social


def test_compact_news_timestamp_is_parsed_before_epoch_conversion():
    stamp = news_stream._ts_seconds("20260922120000")
    assert stamp is not None
    assert news_stream._ts_seconds("20990101120000") is None


def test_news_cluster_includes_time_window_and_identity():
    a = {"ticker": "AAPL", "event_tags": ["earnings"], "published_ts": 1000, "title": "AAPL earnings"}
    b = {"ticker": "AAPL", "event_tags": ["earnings"], "published_ts": 1000 + 21600, "title": "AAPL earnings"}
    assert news_intelligence._cluster_key(a) != news_intelligence._cluster_key(b)


def test_social_summary_separates_threads_from_authors():
    rows = [
        {"ticker_candidates": ["AAPL"], "title": "x", "excerpt": "", "sentiment": "bullish",
         "thread_id": "thread-1", "source_id": "post-1", "author_hash": "author-1", "score": 0, "comments": 0},
        {"ticker_candidates": ["AAPL"], "title": "y", "excerpt": "", "sentiment": "bullish",
         "thread_id": "thread-1", "source_id": "post-2", "author_hash": "author-2", "score": 0, "comments": 0},
    ]
    pulse = social_intelligence._summarize(rows, ["AAPL"])["pulse"][0]
    assert pulse["unique_threads"] == 1
    assert pulse["unique_authors"] == 2


def test_x_adapter_is_disabled_without_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    result = x_social.snapshot(["AAPL"])
    assert result["enabled"] is False
    assert result["research_only"] is True
