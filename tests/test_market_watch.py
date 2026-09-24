"""Google News, social feed edge cases, the X watcher and the market-watch trend scan."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import pytest

import market_watch
import news_stream
import social_intelligence
import x_watcher


class Resp:
    def __init__(self, status=200, content=b"", payload=None, headers=None):
        self.status_code, self.content, self._payload, self.headers = status, content, payload, headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc).timestamp()
T0 = time.time()


# --- news_stream ------------------------------------------------------------

def test_rss_and_gdelt_timestamps_parse_to_the_right_moment():
    assert news_stream._ts_seconds("Tue, 22 Sep 2026 15:00:00 GMT") == datetime(2026, 9, 22, 15, tzinfo=timezone.utc).timestamp()
    # GDELT "YYYYMMDDHHMMSS" used to be read as epoch millis (a year-2612 date).
    assert news_stream._ts_seconds("20260922150100") == datetime(2026, 9, 22, 15, 1, tzinfo=timezone.utc).timestamp()
    assert news_stream._ts_seconds("Tue, garbage") is None


def test_future_dated_headline_is_not_ranked_as_fresh():
    row = news_stream.enrich_headline({"title": "AAPL earnings", "ts": NOW + 86400}, now=NOW)
    assert row["published_ts"] is None and row["timestamp_rejected"] == "future"


GOOGLE_RSS = b"""<rss><channel>
<item><title>Nvidia shares jump after earnings - Reuters</title><link>https://news.google.com/rss/articles/a</link>
<pubDate>Thu, 24 Sep 2026 14:00:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
<item><title>No link item</title><pubDate>Thu, 24 Sep 2026 14:00:00 GMT</pubDate></item>
<item><title>Old story - CNBC</title><link>https://news.google.com/rss/articles/b</link>
<pubDate>Mon, 14 Sep 2026 14:00:00 GMT</pubDate><source url="https://www.cnbc.com">CNBC</source></item>
</channel></rss>"""


def test_google_news_rows_name_the_real_publisher_and_drop_the_suffix():
    rows = news_stream.parse_google_news_rss(GOOGLE_RSS)
    assert [r["title"] for r in rows] == ["Nvidia shares jump after earnings", "Old story"]
    assert rows[0]["publisher"] == "Reuters" and rows[0]["publisher_url"] == "https://www.reuters.com"
    assert news_stream.parse_google_news_rss(b"x" * (news_stream.MAX_FEED_BYTES + 1)) == []


def test_google_news_query_disambiguates_word_tickers():
    assert news_stream.google_news_query("AAPL") == '"AAPL" stock'
    assert news_stream.google_news_query("F") == '"NYSE: F" OR "NASDAQ: F" OR "NYSEARCA: F"'
    assert "NASDAQ: AI" in news_stream.google_news_query("AI")


def test_google_story_dedupes_with_the_same_yahoo_story(monkeypatch):
    import data_sources
    monkeypatch.setattr(news_stream.requests, "get", lambda *a, **k: Resp(content=GOOGLE_RSS))
    monkeypatch.setattr(news_stream, "_gdelt_news", lambda s, limit=5: [])
    monkeypatch.setattr(news_stream, "_benzinga_news", lambda s, limit=5: [])
    monkeypatch.setattr(data_sources, "finnhub_news", lambda s, days=5, limit=6: [])
    monkeypatch.setattr(data_sources, "yahoo_news", lambda s, count=6: [
        {"title": "Nvidia shares jump after earnings", "ts": NOW, "source": "yahoo"}])
    rows = news_stream.news_for_symbol("NVDA", limit=6)
    assert [r["title"] for r in rows].count("Nvidia shares jump after earnings") == 1


# --- social_intelligence ----------------------------------------------------

def test_social_rows_sort_by_parsed_time_not_string(monkeypatch):
    monkeypatch.setenv("SOCIAL_SUBREDDITS", "stocks")
    monkeypatch.setattr(social_intelligence, "_reddit_rows", lambda sub: [])
    monkeypatch.setattr(social_intelligence, "_x_rows", lambda symbols: [])
    monkeypatch.setattr(social_intelligence, "_stocktwits_rows", lambda symbols: [
        {"source": "stocktwits", "created_at": "2026-09-24T14:59:00Z", "title": "new", "excerpt": "$AAPL",
         "ticker_candidates": ["AAPL"], "author_hash": "a"}])
    monkeypatch.setattr(social_intelligence, "_rss_rows", lambda feeds: [
        {"source": "public_rss", "created_at": "Tue, 01 Sep 2026 10:00:00 GMT", "title": "old", "excerpt": "",
         "ticker_candidates": [], "author_hash": "b"}])
    result = social_intelligence.snapshot(["AAPL"], force=True)
    assert [r["title"] for r in result["items"]] == ["new", "old"]


def test_word_tickers_count_only_as_cashtags():
    assert social_intelligence._tickers("AI stocks rally ON news, NOW what") == []
    assert social_intelligence._tickers("Loading $AI and $ON here") == ["AI", "ON"]


def test_stocktwits_permalink_is_a_url(monkeypatch):
    payload = {"messages": [{"id": 123, "body": "$AAPL looks strong", "user": {"id": 9, "username": "trader1"},
                             "created_at": "2026-09-24T14:00:00Z"}]}
    monkeypatch.setattr(social_intelligence.requests, "get", lambda *a, **k: Resp(payload=payload))
    rows = social_intelligence._stocktwits_rows(["AAPL", "not valid!"])
    assert rows[0]["permalink"] == "https://stocktwits.com/trader1/message/123"


def test_invalid_subreddit_is_refused_without_a_request(monkeypatch):
    monkeypatch.setattr(social_intelligence.requests, "get", lambda *a, **k: pytest.fail("no request"))
    assert social_intelligence._reddit_rows("../../api/v1/me") == []


def test_reddit_without_credentials_explains_how_to_turn_it_on(monkeypatch):
    for name in ("REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_PUBLIC_JSON"):
        monkeypatch.delenv(name, raising=False)
    assert social_intelligence._reddit_rows("stocks") == []
    assert "REDDIT_CLIENT_ID" in social_intelligence._source_status["reddit r/stocks"]["error"]


def test_atom_feeds_are_read():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>$TSLA deliveries beat</title>
    <link rel="alternate" href="https://example.com/tsla"/><updated>2026-09-24T12:00:00Z</updated>
    <summary>&lt;p&gt;Record quarter&lt;/p&gt;</summary></entry></feed>"""
    import xml.etree.ElementTree as ET
    entries = social_intelligence._feed_entries(ET.fromstring(atom))
    assert entries == [{"title": "$TSLA deliveries beat", "link": "https://example.com/tsla",
                        "summary": "<p>Record quarter</p>", "published": "2026-09-24T12:00:00Z"}]


# --- x_watcher ----------------------------------------------------------------

@pytest.fixture
def xw(monkeypatch, tmp_path):
    monkeypatch.setenv("X_BEARER_TOKEN", "token")
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("X_MONTHLY_POST_CAP", "3000")
    monkeypatch.delenv("X_DAILY_POST_CAP", raising=False)
    monkeypatch.delenv("X_WATCH_ACCOUNTS", raising=False)
    monkeypatch.setattr(x_watcher, "_state", {"rows": {}, "since": {}, "last_poll_at": 0.0, "backoff_until": 0.0,
                                              "error": None, "cashtag_operator": True, "spam_dropped": 0, "cursor": 0})
    calls = []
    responses = []

    def fake_get(url, params=None, **kwargs):
        calls.append(dict(params or {}))
        return responses.pop(0) if responses else Resp(payload={"meta": {"result_count": 0}})

    monkeypatch.setattr(x_watcher.requests, "get", fake_get)
    return calls, responses


def _page(posts, newest="200"):
    return Resp(payload={
        "data": posts,
        "includes": {"users": [{"id": "1", "username": "alice"}]},
        "meta": {"newest_id": newest, "result_count": len(posts)},
    })


def _post(pid, text="$AAPL breakout bullish", tags=("AAPL",)):
    return {"id": str(pid), "text": text, "author_id": "1", "created_at": datetime.now(timezone.utc).isoformat(),
            "entities": {"cashtags": [{"tag": t} for t in tags]}, "public_metrics": {"like_count": 3, "reply_count": 1}}


def test_x_is_silent_without_a_token(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.setattr(x_watcher.requests, "get", lambda *a, **k: pytest.fail("no request without a token"))
    assert x_watcher.social_rows(["AAPL"]) == []
    assert "X_BEARER_TOKEN" in x_watcher.status()["error"]


def test_x_poll_reads_posts_drops_spam_and_uses_since_id(xw):
    calls, responses = xw
    spam = _post(150, "$A $B $C $D $E $F moon", tags=("A", "B", "C", "D", "E", "F"))
    responses.append(_page([_post(101), spam], newest="150"))
    x_watcher.poll(["AAPL"], now=T0)
    rows = x_watcher.cached_rows()
    assert [r["source_id"] for r in rows] == ["101"]
    assert rows[0]["permalink"] == "https://x.com/alice/status/101" and rows[0]["ticker_candidates"] == ["AAPL"]
    assert x_watcher.status()["spam_dropped"] == 1
    assert x_watcher.usage()["posts"] == 2  # both posts were read and billed
    x_watcher.poll(["AAPL"], now=T0 + 16 * 60)
    assert calls[-1]["since_id"] == "150"


def test_x_poll_respects_interval_and_daily_budget(xw, monkeypatch):
    calls, responses = xw
    x_watcher.poll(["AAPL"], now=T0)
    x_watcher.poll(["AAPL"], now=T0 + 60)  # inside the poll interval
    assert len(calls) == 1
    monkeypatch.setenv("X_DAILY_POST_CAP", "5")
    x_watcher.poll(["AAPL"], now=T0 + 3600, force=True)
    assert len(calls) == 1 and "budget" in x_watcher.status()["error"]


def test_x_rate_limit_backs_off_until_reset(xw):
    calls, responses = xw
    responses.append(Resp(status=429, headers={"x-rate-limit-reset": str(T0 + 4000)}))
    x_watcher.poll(["AAPL"], now=T0)
    assert "rate limit" in x_watcher.status()["error"]
    x_watcher.poll(["AAPL"], now=T0 + 3000)  # past the interval, before the reset
    assert len(calls) == 1
    x_watcher.poll(["AAPL"], now=T0 + 4001)
    assert len(calls) == 2


def test_x_access_denied_is_explained(xw):
    calls, responses = xw
    responses.append(Resp(status=403, payload={"detail": "client-not-enrolled"}))
    x_watcher.poll(["AAPL"], now=T0)
    assert "403" in x_watcher.status()["error"] and "client-not-enrolled" in x_watcher.status()["error"]


def test_x_falls_back_to_keywords_when_cashtags_are_not_allowed(xw):
    calls, responses = xw
    responses.append(Resp(status=400, payload={"errors": [{"message": "Reference to invalid operator 'cashtag'"}]}))
    x_watcher.poll(["AAPL"], now=T0)
    assert x_watcher.status()["query_mode"] == "keyword"
    x_watcher.poll(["AAPL"], now=T0 + 16 * 60)
    assert "$AAPL" not in calls[-1]["query"] and "AAPL" in calls[-1]["query"]


def test_x_long_watchlists_rotate_through_every_chunk(xw, monkeypatch):
    calls, _ = xw
    monkeypatch.setenv("X_QUERY_MAX_CHARS", "64")
    symbols = [f"T{c}{d}" for c in "ABCDEFGH" for d in "ABCDE"]
    queried = set()
    for n in range(6):
        x_watcher.poll(symbols, now=T0 + n * 3600)
    for params in calls:
        queried.update(s.strip("($)") for s in params["query"].split(" -is")[0].split(" OR "))
    assert set(symbols) <= queried


# --- market_watch -------------------------------------------------------------

LISTINGS = [
    {"symbol": "NVDA", "name": "NVIDIA Corporation - Common Stock", "etf": False, "exchange": "Q"},
    {"symbol": "HD", "name": "The Home Depot, Inc. Common Stock", "etf": False, "exchange": "N"},
    {"symbol": "TGT", "name": "Target Corporation Common Stock", "etf": False, "exchange": "N"},
    {"symbol": "GOOG", "name": "Alphabet Inc. - Class C Capital Stock", "etf": False, "exchange": "Q"},
    {"symbol": "GOOGL", "name": "Alphabet Inc. - Class A Common Stock", "etf": False, "exchange": "Q"},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ", "etf": True, "exchange": "Q"},
] + [{"symbol": f"Z{i:04d}", "name": f"Filler {i} Holdings", "etf": False, "exchange": "Q"} for i in range(1000)]


def test_listing_index_matches_names_cashtags_and_exchange_mentions():
    index = market_watch.ListingIndex(LISTINGS)
    assert index.loaded
    assert index.match_text("Nvidia rallies; Home Depot (NYSE: HD) and $GOOGL rise; Target slips") == ["GOOGL", "HD", "NVDA"]
    assert index.match_search_term("nvidia stock") == "NVDA"
    assert index.match_search_term("alphabet") == "GOOG"
    assert index.match_search_term("target") is None  # everyday word
    assert index.info("HD")["google_finance_url"] == "https://www.google.com/finance/quote/HD:NYSE"
    assert market_watch.google_finance_url("XYZ") == "https://www.google.com/finance?q=XYZ"


def test_google_trends_rss_is_parsed():
    feed = b"""<rss xmlns:ht="https://trends.google.com/trending/rss"><channel>
    <item><title>nvidia stock</title><ht:approx_traffic>200K+</ht:approx_traffic>
    <ht:news_item><ht:news_item_title>Nvidia hits record</ht:news_item_title></ht:news_item></item>
    <item><title>football scores</title><ht:approx_traffic>2,000+</ht:approx_traffic></item>
    </channel></rss>"""
    rows = market_watch.parse_google_trends(feed)
    assert rows[0] == {"term": "nvidia stock", "traffic": 200000, "news": ["Nvidia hits record"]}
    assert rows[1]["traffic"] == 2000


def test_rank_trends_prefers_breadth_and_drops_non_listings():
    index = market_watch.ListingIndex(LISTINGS)
    ranked = market_watch.rank_trends({
        "stocktwits": [{"ticker": "BTC.X"}, {"ticker": "TQQQ"}, {"ticker": "NVDA"}],
        "yahoo": [{"ticker": "^GSPC"}, {"ticker": "NVDA"}, {"ticker": "HD"}],
        "google_news": [{"ticker": "NVDA"}],
    }, index, watchlist=["HD"])
    by_symbol = {r["symbol"]: r for r in ranked}
    assert ranked[0]["symbol"] == "NVDA" and set(by_symbol) == {"NVDA", "HD", "TQQQ"}
    assert ranked[0]["breadth"] == 3 and ranked[0]["label"] == "broad"
    assert by_symbol["HD"]["in_watchlist"] is True
    assert by_symbol["TQQQ"]["leveraged_or_inverse"] is True


def test_custom_feeds_accept_only_plain_https(monkeypatch):
    monkeypatch.setenv("MARKET_WATCH_RSS_FEEDS",
                       "https://a.example/rss, http://b.example/rss, https://user:pw@c.example/rss, https://a.example/rss")
    assert market_watch.configured_feeds() == ["https://a.example/rss"]


def test_snapshot_combines_sources_and_isolates_failures(monkeypatch, tmp_path):
    import buzz_sources
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("MARKET_WATCH_RSS_FEEDS", raising=False)
    fresh = datetime.fromtimestamp(time.time() - 600, timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    news = GOOGLE_RSS.replace(b"Thu, 24 Sep 2026 14:00:00 GMT", fresh.encode())

    def fake_get(url, params=None, **kwargs):
        if "news.google.com" in url:
            return Resp(content=news)
        if "trends.google.com" in url:
            return Resp(status=503)
        if "yahoo" in url:
            return Resp(payload={"finance": {"result": [{"quotes": [{"symbol": "NVDA"}, {"symbol": "HD"}]}]}})
        return Resp(status=404)

    monkeypatch.setattr(market_watch.requests, "get", fake_get)
    monkeypatch.setattr(market_watch, "listing_index", lambda desk: market_watch.ListingIndex(LISTINGS))
    monkeypatch.setattr(buzz_sources, "get_cached_buzz", lambda: {"tickers": [
        {"ticker": "NVDA", "mentions": 40, "subreddits": ["wallstreetbets"]}], "stale": False})
    monkeypatch.setattr(buzz_sources, "fetch_stocktwits_trending", lambda: ([{"ticker": "NVDA"}], None))
    monkeypatch.setattr(market_watch, "_cache", {"at": 0.0, "key": None, "payload": None})
    payload = market_watch.snapshot(None, {"watchlist": ["HD"], "social_enabled": False}, force=True)
    assert payload["ok"] and payload["display_only"]
    assert payload["headlines"][0]["publisher"] == "Reuters"
    top = payload["trends"]["tickers"][0]
    assert top["symbol"] == "NVDA" and set(top["sources"]) == {"reddit", "stocktwits", "yahoo", "google_news"}
    assert payload["trends"]["sources"]["google_trends"] == {**payload["trends"]["sources"]["google_trends"],
                                                           "ok": False, "error": "HTTP 503"}
    assert payload["trends"]["sources"]["google_news"]["ok"] is True
    assert payload["watchlist"][0]["google_finance_url"].endswith("HD:NYSE")
    assert "Social research is off" in payload["social"]["error"]


def test_market_watch_endpoint(monkeypatch):
    import app as desk
    monkeypatch.setattr(market_watch, "snapshot", lambda d, cfg, force=False: {"ok": True, "force": force})
    client = desk.app.test_client()
    assert client.get("/api/market-watch?force=1", base_url="http://127.0.0.1:5056").get_json() == {"ok": True, "force": True}


def test_source_errors_are_short_and_url_free():
    assert news_stream.short_error("HTTP 403 for https://api.stocktwits.com/api/2/trending/symbols.json") == "HTTP 403"
    assert news_stream.short_error("ProxyError: HTTPSConnectionPool(host='news.google.com', port=443): Max retries") == "could not connect"
    assert news_stream.short_error("timeout for https://x.example") == "timed out"
    assert news_stream.short_error(None) is None


def test_headline_trend_is_unavailable_without_headlines(monkeypatch):
    monkeypatch.setattr(market_watch, "listing_index", lambda desk: market_watch.ListingIndex(LISTINGS))
    for name in ("_reddit_trend", "_stocktwits_trend", "_yahoo_trend"):
        monkeypatch.setattr(market_watch, name, (lambda *a: ([], market_watch._status(True))))
    monkeypatch.setattr(market_watch, "_google_trends", lambda index: ([], [], market_watch._status(True)))
    result = market_watch.trend_scan(None, [], [])
    assert result["sources"]["google_news"]["ok"] is False
