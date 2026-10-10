"""WSB thread monitor: thread discovery, live comment ingestion, crowding (caution only)."""
from __future__ import annotations

import time
from collections import deque
from types import SimpleNamespace as NS

import pytest

import buzz_sources
import wsb_monitor as wm

NOW = 1_790_000_000.0


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(wm, "_state", {"threads": {}, "mentions": deque(), "last_poll": 0.0, "last_ok": 0.0,
                                       "last_discovery": 0.0, "coverage_since": None, "error": None,
                                       "comments_read": 0, "loaded": True})
    monkeypatch.setattr(wm, "_desk", NS(DATA_DIR=tmp_path))
    monkeypatch.setattr(wm, "_valid_symbol", lambda: None)
    monkeypatch.setattr(wm, "configured", lambda: True)


def post(pid, title, age_h, stickied=False):
    return {"id": pid, "title": title, "created_utc": NOW - age_h * 3600, "permalink": f"/r/wallstreetbets/comments/{pid}/x/",
            "stickied": stickied, "num_comments": 1000}


def test_picks_daily_nightly_weekend_and_stickies():
    posts = [post("d1", "Daily Discussion Thread for September 23, 2026", 26),
             post("d2", "Daily Discussion Thread for September 24, 2026", 3, True),
             post("w1", "What Are Your Moves Tomorrow, September 24, 2026", 1),
             post("e1", "Most Anticipated Earnings Releases for the week", 20, True),
             post("x1", "YOLO 100k on NVDA calls", 1),
             post("old", "Weekend Discussion Thread", 80)]
    rows = wm.pick_threads(posts, NOW)
    assert [r["id"] for r in rows] == ["d2", "w1", "e1"]
    assert rows[0]["label"] == "Daily Discussion" and rows[1]["label"] == "What Are Your Moves Tomorrow"


def test_tickers_skip_slang_and_words_but_keep_cashtags():
    assert wm.tickers_in("NVDA calls printing, YOLO into $AI and AI stuff, EOD LFG") == ["NVDA", "AI"]
    assert wm.tickers_in("TSLA to the moon", valid=lambda s: s != "TSLA") == []
    assert wm.sentiment("NVDA calls 🚀🚀") == "bull" and wm.sentiment("puts on SPY, drilling") == "bear"
    assert wm.sentiment("what is SPY doing") == "neutral"


def comments(n, sym="NVDA", start=0, age=60, text="{s} calls 🚀", authors=None):
    return [{"id": f"c{start + i}", "body": text.format(s=sym), "author": f"user{(authors or n) and i % (authors or n)}",
             "created_utc": NOW - age} for i in range(n)]


THREAD = {"id": "d2", "tag": "wsb_daily", "label": "Daily Discussion", "title": "Daily", "permalink": "/r/x/", "num_comments": 1}


def test_ingest_counts_each_comment_once_and_ignores_bots_and_old():
    rows = comments(5) + [{"id": "bot", "body": "NVDA", "author": "AutoModerator", "created_utc": NOW},
                          {"id": "old", "body": "NVDA", "author": "u", "created_utc": NOW - 4 * 3600}]
    assert wm.ingest_comments(THREAD, rows, NOW) == 5
    assert wm.ingest_comments(THREAD, rows, NOW) == 0  # already seen
    stats = wm.ticker_stats(NOW)
    assert stats[0]["ticker"] == "NVDA" and stats[0]["mentions_60m"] == 5 and stats[0]["bull_share"] == 1.0


def test_crowding_needs_many_people_and_a_spike_or_top_rank():
    wm._state["last_ok"] = NOW
    wm._state["coverage_since"] = NOW - 3 * 3600
    wm.ingest_comments(THREAD, comments(30, authors=30), NOW)
    wm.ingest_comments(THREAD, comments(5, start=100, age=5400), NOW)  # the hour before
    result = wm.crowding("NVDA", {"wsb_crowd_min_mentions": 25}, NOW)
    assert result["crowded"] and result["velocity"] == 6.0 and "30 mentions from 30 people" in result["reason"]
    # The same count from three accounts is not a crowd.
    wm._state["mentions"].clear()
    wm.ingest_comments(THREAD, comments(30, start=200, authors=3), NOW)
    assert not wm.crowding("NVDA", {"wsb_crowd_min_mentions": 25}, NOW)["crowded"]


def test_without_two_hours_of_history_only_a_top_ranked_heavy_ticker_counts():
    wm._state["last_ok"] = NOW
    wm._state["coverage_since"] = NOW - 1800
    wm.ingest_comments(THREAD, comments(30, authors=30), NOW)
    row = wm.crowding("NVDA", {"wsb_crowd_min_mentions": 25}, NOW)
    assert row["velocity"] is None and not row["crowded"]
    wm.ingest_comments(THREAD, comments(30, start=500, authors=30), NOW)
    assert wm.crowding("NVDA", {"wsb_crowd_min_mentions": 25}, NOW)["crowded"]


def test_stale_or_missing_data_is_never_crowded():
    wm.ingest_comments(THREAD, comments(80, authors=80), NOW)
    wm._state["last_ok"] = NOW - 3600
    assert wm.crowding("NVDA", {}, NOW) == {"available": False, "crowded": False, "ticker": "NVDA"}


def test_poll_reads_newest_comments_and_survives_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(buzz_sources, "fetch_subreddit_listing", lambda sub, sort, limit: (
        [post("d2", "Daily Discussion Thread for September 24, 2026", 3, True)], None))

    def fetch(permalink, **kw):
        calls.append(kw)
        return comments(3), None
    monkeypatch.setattr(buzz_sources, "fetch_post_comments", fetch)
    status = wm.poll(NOW)
    assert calls[0]["sort"] == "new" and calls[0]["limit"] == 500
    assert status["fresh"] and status["threads"][0]["comments_read"] == 3 and status["error"] is None
    monkeypatch.setattr(buzz_sources, "fetch_post_comments", lambda *a, **k: ([], "HTTP 503 for https://oauth.reddit.com/x"))
    status = wm.poll(NOW + 120)
    assert status["error"] == "Daily Discussion: HTTP 503" and "oauth.reddit.com" not in status["error"]
    assert wm._state["coverage_since"] is None  # a gap resets the trend history


def test_not_configured_is_explained(monkeypatch):
    monkeypatch.setattr(wm, "configured", lambda: False)
    assert "REDDIT_CLIENT_ID" in wm.poll(NOW)["error"]


def test_state_survives_a_restart(monkeypatch):
    now = time.time()
    wm._state["last_ok"] = now
    rows = [dict(c, created_utc=now - 30) for c in comments(4)]
    assert wm.ingest_comments(THREAD, rows, now) == 4
    wm._save()
    wm._state.update(mentions=deque(), threads={}, loaded=False)
    wm._load()
    assert len(wm._state["mentions"]) == 4 and "d2" in wm._state["threads"]


def test_pasted_live_chat_is_included_while_fresh(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(buzz_sources, "get_live_chat_paste", lambda: {
        "parsed_at": datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat(), "tickers": [{"ticker": "GME", "mentions": 9}]})
    assert wm.ticker_stats(NOW)[0]["live_chat_pasted"] == 9
    assert wm.ticker_stats(NOW + 3600) == []
