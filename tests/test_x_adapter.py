from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import x_adapter


def _reset_adapter_state(monkeypatch):
    monkeypatch.setattr(x_adapter, "_request_times", [], raising=False)
    x_adapter._cache.clear()


def test_disabled_without_bearer_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    assert x_adapter.is_configured() is False
    assert x_adapter.is_enabled({"social_enabled": True, "x_enabled": True}) is False


def test_disabled_by_default_config_even_when_configured(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    assert x_adapter.is_configured() is True
    # Off by default: neither flag set.
    assert x_adapter.is_enabled(None) is False
    assert x_adapter.is_enabled({}) is False
    # Only one of the two required flags set — still off.
    assert x_adapter.is_enabled({"social_enabled": True}) is False
    assert x_adapter.is_enabled({"x_enabled": True}) is False
    # Both flags + token configured -> enabled.
    assert x_adapter.is_enabled({"social_enabled": True, "x_enabled": True}) is True


def test_search_recent_noop_when_not_configured(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    assert x_adapter.search_recent("AAPL") == []


def test_search_recent_uses_authorized_endpoint_and_normalizes_rows(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _reset_adapter_state(monkeypatch)
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "data": [
                    {
                        "id": "1",
                        "text": "$AAPL breaking out",
                        "author_id": "u1",
                        "created_at": "2026-01-01T00:00:00Z",
                        "public_metrics": {"like_count": 5, "reply_count": 1},
                    }
                ]
            }

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["params"] = params
        return Response()

    monkeypatch.setattr(x_adapter.requests, "get", fake_get)
    rows = x_adapter.search_recent("aapl")
    assert captured["url"] == x_adapter._SEARCH_URL
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    assert rows[0]["source"] == "x_public"
    assert rows[0]["ticker_candidates"] == ["AAPL"]
    assert rows[0]["author_hash"] != "u1"  # hashed, never the raw author id
    assert len(rows[0]["author_hash"]) == 16


def test_rate_limit_blocks_requests_over_window(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("X_RATE_LIMIT_PER_15MIN", "2")
    _reset_adapter_state(monkeypatch)
    calls = {"n": 0}

    class Response:
        status_code = 200

        def json(self):
            calls["n"] += 1
            return {"data": []}

    monkeypatch.setattr(x_adapter.requests, "get", lambda *a, **k: Response())
    # Distinct symbols to avoid the per-symbol result cache masking the limiter.
    assert x_adapter.search_recent("AAA") == []
    assert x_adapter.search_recent("BBB") == []
    # Third distinct symbol within the same 15-min window should be rate limited
    # (no network call made) rather than raising.
    assert x_adapter.search_recent("CCC") == []
    assert calls["n"] == 2


def test_rows_for_watchlist_respects_enable_gate(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "test-token")
    _reset_adapter_state(monkeypatch)
    monkeypatch.setattr(x_adapter, "search_recent", lambda symbol, limit=None: [{"source": "x_public"}])
    assert x_adapter.rows_for_watchlist(["AAPL"], {"social_enabled": False, "x_enabled": True}) == []
    assert x_adapter.rows_for_watchlist(["AAPL"], {"social_enabled": True, "x_enabled": False}) == []
    rows = x_adapter.rows_for_watchlist(["AAPL"], {"social_enabled": True, "x_enabled": True})
    assert rows == [{"source": "x_public"}]


def test_public_status_never_leaks_secret(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "super-secret-token")
    status = x_adapter.public_status()
    assert "super-secret-token" not in str(status)
    assert status["display_only"] is True
    assert status["attention_only"] is True
