from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import social_intelligence


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
                    "created_utc": 1_798_000_000,
                    "score": 100,
                    "num_comments": 20,
                    "permalink": "/r/wallstreetbets/comments/abc/x/",
                }},
                {"data": {
                    "id": "def",
                    "author": "other",
                    "title": "$AAPL puts bearish",
                    "selftext": "downside risk",
                    "created_utc": 1_798_000_010,
                    "score": 20,
                    "num_comments": 4,
                    "permalink": "/r/wallstreetbets/comments/def/y/",
                }},
            ]}}

    monkeypatch.setattr(social_intelligence.requests, "get", lambda *a, **k: Response())
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
