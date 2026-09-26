"""Watchlist alerts notify once, explain in plain words and never touch trading."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

import desk_alerts
import watch_alerts as wa

BASE = "http://127.0.0.1:5056"
STAMP = datetime(2026, 9, 25, 15, tzinfo=timezone.utc).timestamp()


def quote(price, prev=100.0):
    return {"regularMarketPrice": price, "regularMarketPreviousClose": prev, "regularMarketTime": STAMP}


def test_moves_alert_by_band_with_a_dollar_example():
    alerts = wa.move_alerts({"AAA": quote(103.5), "BBB": quote(101.0), "CCC": quote(93.0)}, 3.0)
    by = {a["ticker"]: a for a in alerts}
    assert set(by) == {"AAA", "CCC"}
    assert "AAA is up 3.5% today" in by["AAA"]["message"] and "$1,000 of AAA would now be worth about $1,035" in by["AAA"]["message"]
    assert by["AAA"]["key"] == "move|AAA|2026-09-25|up|1"
    assert by["CCC"]["key"].endswith("|down|2") and by["CCC"]["level"] == "warning"
    assert wa.move_alerts({"BAD": {"regularMarketPrice": None}}, 3.0) == []


def test_earnings_and_news_alerts():
    rows = {"AAA": {"days_away": 1, "date": "2026-09-26"}, "BBB": {"days_away": 5, "date": "2026-09-30"},
            "CCC": {"days_away": -1, "date": "2026-09-24"}}
    alerts = wa.earnings_alerts(rows, 2)
    assert [a["ticker"] for a in alerts] == ["AAA"] and "tomorrow (2026-09-26)" in alerts[0]["message"]
    assert wa.earnings_alerts(rows, 0) == []
    news = wa.news_alerts([{"ticker": "AAA", "title": "AAA raises guidance", "headline_key": "k", "source": "google_news"}])
    assert news[0]["key"] == "news|AAA|k" and "AAA headline: AAA raises guidance" in news[0]["message"]


def test_settings_are_validated():
    assert wa.validate({"move_pct": 5, "earnings_days": 0})["move_pct"] == 5.0
    for bad in ({"move_pct": 0.5}, {"earnings_days": 1.5}, {"enabled": "yes"}, {"extra": 1}, None):
        with pytest.raises(ValueError):
            wa.validate(bad)


@pytest.fixture
def fake_desk(tmp_path, monkeypatch):
    desk = NS(DATA_DIR=tmp_path, load_config=lambda: {"watchlist": ["AAA", "BBB"]},
              _live_agent=NS(load=lambda: {"managed": {"ZZZ": {"shares": 1}}}))
    emitted = []
    monkeypatch.setattr(desk_alerts, "emit", lambda kind, message, **kw: emitted.append((kind, message, kw)))
    import data_sources
    import news_stream
    monkeypatch.setattr(data_sources, "yahoo_quote_batch", lambda symbols, timeout=10: {"AAA": quote(104.0), "ZZZ": quote(100.0)})
    monkeypatch.setattr(wa, "_earnings_rows", lambda symbols: {"BBB": {"days_away": 0, "date": "2026-09-25"}})
    monkeypatch.setattr(news_stream, "material_headlines", lambda symbols, max_age_sec: [])
    return NS(desk=desk, emitted=emitted)


def test_run_follows_held_stocks_first_and_never_repeats_an_alert(fake_desk):
    assert wa.followed(fake_desk.desk) == ["ZZZ", "AAA", "BBB"]
    out = wa.run(fake_desk.desk, force_earnings=True)
    kinds = sorted(k for k, _, _ in fake_desk.emitted)
    assert kinds == ["watch_earnings", "watch_move"] and out["notification_only"] and len(out["history"]) == 2
    wa.run(fake_desk.desk, force_earnings=True)
    assert len(fake_desk.emitted) == 2  # remembered on disk, not re-sent


def test_disabled_alerts_do_nothing_and_routes_save_settings(fake_desk, monkeypatch):
    raw = wa.load(fake_desk.desk)
    raw["settings"]["enabled"] = False
    wa.save(fake_desk.desk, raw)
    wa.run(fake_desk.desk, force_earnings=True)
    assert fake_desk.emitted == []
    import app as desk
    monkeypatch.setattr(desk, "DATA_DIR", fake_desk.desk.DATA_DIR)
    client = desk.app.test_client()
    bad = client.post("/api/watch-alerts", json={"move_pct": 99}, base_url=BASE)
    assert bad.status_code == 400
    good = client.post("/api/watch-alerts", json={"enabled": True, "move_pct": 5, "earnings_days": 1, "news": False}, base_url=BASE)
    assert good.get_json()["settings"] == {"enabled": True, "move_pct": 5.0, "earnings_days": 1, "news": False}


def test_a_volatile_day_is_grouped_not_flooded_and_cheap_stocks_show_real_prices():
    quotes = {f"S{i}": quote(103 + i * 0.5) for i in range(10)}
    quotes["PENNY"] = quote(0.30, prev=0.34)
    moves = wa.move_alerts(quotes, 3.0)
    assert moves[0]["ticker"] == "PENNY" and "$0.3400 → $0.3000" in moves[0]["message"] and "11.8%" in moves[0]["message"]
    limited = wa.limit_moves(moves, 3.0, {})
    assert len(limited) == wa.MAX_MOVE_ALERTS + 1
    summary = limited[-1]
    assert summary["message"].startswith("5 more followed stocks moved at least 3% today") and len(summary["covers"]) == 5
    assert wa.limit_moves(moves, 3.0, {m["key"]: "x" for m in moves}) == []
