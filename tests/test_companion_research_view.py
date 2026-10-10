from datetime import datetime, timezone
from types import SimpleNamespace

import desk_day


def test_research_view_reports_cached_coverage_without_fetching():
    now = datetime.now(timezone.utc)
    stamp = now.timestamp()
    payload = {"sources": [{"status": "connected", "checked_at": stamp - 5},
                           {"status": "connected", "checked_at": stamp - 901},
                           {"status": "connected", "checked_at": stamp + 1}],
               "items": [{"fresh": True, "checked_at": stamp - 5, "published_ts": stamp - 60},
                         {"fresh": True, "checked_at": stamp - 5, "published_ts": stamp + 1}],
               "busy": False, "refresh_seconds": 300}
    class News:
        def snapshot(self, at):
            assert at == stamp
            return payload

        def refresh(self):
            raise AssertionError("Presentation must never launch extra network work")

    desk = SimpleNamespace(_research_companion=SimpleNamespace(news=News()))
    result = desk_day.research_view(desk, now)
    assert result["fresh_sources"] == 1 and result["total_sources"] == 3
    assert result["fresh_headlines"] == 1
    assert "1 of 3" in result["summary"] and result["refresh_seconds"] == 300
    assert "not model conclusions" in result["evaluation"]


def test_research_view_unavailable_and_malformed_cache_are_explicit():
    now = datetime.now(timezone.utc)
    for payload in (None, {"sources": None, "items": "bad"}, {}):
        news = SimpleNamespace(snapshot=lambda _now: payload)
        result = desk_day.research_view(SimpleNamespace(_research_companion=SimpleNamespace(news=news)), now)
        assert result["fresh_sources"] == 0
        assert "No current headline feeds verified" in result["summary"]


def test_research_view_busy_does_not_claim_fresh_results():
    news = SimpleNamespace(snapshot=lambda _: {"busy": True, "sources": [], "items": [], "refresh_seconds": float("nan")})
    result = desk_day.research_view(SimpleNamespace(_research_companion=SimpleNamespace(news=news)), datetime.now(timezone.utc))
    assert result["busy"] and "last verified context separate" in result["summary"]
    assert result["fresh_sources"] == 0 and result["refresh_seconds"] == 300
