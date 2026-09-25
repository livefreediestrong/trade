"""Scheduled market events: parsing real feed formats, impact, and the no-new-entry window."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import market_events as me

ET = me.ET

FED = {"events": [
    {"title": "Speech - Governor Christopher J. Waller", "time": "8:30 a.m.", "month": "2026-09", "days": "3",
     "type": "Speeches", "description": "Economic Outlook", "live": "https://events.example/waller"},
    {"title": "Testimony - Chairman Kevin Warsh", "time": "10:00 a.m.", "month": "2026-09", "days": "24",
     "type": "Testimony", "location": "Before the U.S. Senate Committee on Banking"},
    {"title": "Speech - Vice Chair Philip N. Jefferson", "time": "7:00 p.m.", "month": "2026-09", "days": "24",
     "type": "Speeches"},
    {"title": "FOMC Press Conference", "time": "2:30 p.m.", "month": "2026-09", "days": "16", "type": "FOMC",
     "link": "https://www.federalreserve.gov/live-broadcast.htm"},
    {"title": "FOMC Meeting", "time": "", "month": "2026-10", "days": "27-28", "type": "FOMC"},
    {"title": "FOMC Minutes", "time": "2:00 p.m.", "month": "2026-10", "days": "7", "type": "FOMC",
     "description": "<p>Meeting of September 15-16</p>"},
    {"title": "H.4.1 - Factors Affecting Reserve Balances", "time": "4:30 p.m.", "month": "2026-09",
     "days": "3, 10, 17, 24", "type": "Stat"},
    {"title": "Holiday - Independence Day", "time": "", "month": "2026-07", "days": "3", "type": "Other"},
]}

BLS = """BEGIN:VCALENDAR
X-WR-TIMEZONE:US-Eastern
BEGIN:VEVENT
UID:a
DTSTART;TZID=US-Eastern:20260911T083000
DURATION:PT0M
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
UID:b
DTSTART;TZID=US-Eastern:20260924T100000
SUMMARY:Job Openings and Labor Turnover Survey
END:VEVENT
BEGIN:VEVENT
UID:c
DTSTART;TZID=US-Eastern:20260918T100000
SUMMARY:State Employment and Unemploy
 ment (Monthly)
END:VEVENT
END:VCALENDAR
"""

PRESIDENT = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20260924T180000Z
SUMMARY:The President delivers an address to the nation on trade
DESCRIPTION:Oval Office\\, Open Press
END:VEVENT
BEGIN:VEVENT
DTSTART:20260924T150000Z
SUMMARY:The President receives the Intelligence Briefing
DESCRIPTION:Closed Press
END:VEVENT
BEGIN:VEVENT
DTSTART:20260924T160000Z
SUMMARY:The President signs an Executive Order on tariffs
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_state", {"events": [], "sources": {}, "at": 0.0, "loaded": True, "by_source": {}})
    me.configure(tmp_path)
    monkeypatch.setenv("TOMAHAWK_NO_BG", "1")
    yield
    me.configure(None)


def by_title(rows, text):
    return [r for r in rows if text in r["title"]]


def test_fed_calendar_times_and_impact():
    rows = me.parse_fed_calendar(FED)
    chair = by_title(rows, "Chairman Kevin Warsh")[0]
    assert chair["impact"] == "high" and chair["kind"] == "fed_chair"
    assert datetime.fromisoformat(chair["start"]).astimezone(ET).strftime("%Y-%m-%d %H:%M") == "2026-09-24 10:00"
    assert by_title(rows, "Vice Chair")[0]["impact"] == "medium"
    assert by_title(rows, "Waller")[0]["impact"] == "medium"
    press = by_title(rows, "Press Conference")[0]
    assert press["impact"] == "high" and datetime.fromisoformat(press["start"]).astimezone(ET).hour == 14
    statement = by_title(rows, "FOMC statement")
    assert len(statement) == 1 and datetime.fromisoformat(statement[0]["start"]).astimezone(ET).strftime("%m-%d %H:%M") == "10-28 14:00"
    assert by_title(rows, "Minutes")[0]["impact"] == "medium" and "<p>" not in by_title(rows, "Minutes")[0]["detail"]
    assert len(by_title(rows, "H.4.1")) == 4 and not by_title(rows, "Holiday")


def test_bls_ics_eastern_times_and_folded_lines():
    rows = me.parse_ics(BLS)
    assert [r["summary"] for r in rows][2] == "State Employment and Unemployment (Monthly)"
    cpi = rows[0]
    assert cpi["start"].astimezone(timezone.utc).strftime("%H:%M") == "12:30"  # 8:30 EDT
    assert me.classify_bls("Consumer Price Index") == ("high", "cpi")
    assert me.classify_bls("Employment Situation") == ("high", "jobs_report")
    assert me.classify_bls("Job Openings and Labor Turnover Survey")[0] == "medium"
    assert me.classify_bls("State Employment and Unemployment (Monthly)")[0] == "low"


def test_president_schedule_impact_and_cancelled_items():
    rows = me.parse_ics(PRESIDENT)
    assert len(rows) == 2  # cancelled item dropped
    assert me.classify_president(rows[0]["summary"], rows[0]["description"]) == "high"
    assert me.classify_president(rows[1]["summary"], rows[1]["description"]) == "low"
    assert me.classify_president("The President delivers remarks on the economy") == "medium"


def test_custom_events_validate_and_parse():
    rows = me.validate_custom_events([{"title": "President speech <b>", "start": "2026-09-24 14:00", "impact": "high"}])
    assert rows == [{"title": "President speech", "start": "2026-09-24 14:00", "impact": "high"}]
    event = me.parse_custom(rows)[0]
    assert datetime.fromisoformat(event["start"]).astimezone(ET).hour == 14
    for bad in ([{"title": "", "start": "2026-09-24 14:00"}], [{"title": "x", "start": "soon"}],
                [{"title": "x", "start": "2026-09-24 14:00", "impact": "huge"}], "x", [{}] * 51):
        with pytest.raises(ValueError):
            me.validate_custom_events(bad)


def _set_rows(rows):
    me._state["by_source"] = {"test": rows}
    me._state["at"] = __import__("time").time()


def test_guard_window_blocks_around_a_high_impact_event():
    _set_rows(me.parse_fed_calendar(FED))
    cfg = {"event_guard_enabled": True, "event_guard_before_min": 15, "event_guard_after_min": 15}
    at = lambda h, m: datetime(2026, 9, 24, h, m, tzinfo=ET)  # noqa: E731
    assert me.active_window(cfg, at(9, 44)) is None
    window = me.active_window(cfg, at(9, 50))
    assert window and "Warsh" in window["title"] and "10:15 AM ET" in me.window_message(window, at(9, 50))
    assert me.active_window(cfg, at(10, 16)) is None
    assert me.active_window(dict(cfg, event_guard_enabled=False), at(9, 50)) is None
    # A medium-impact speech never opens a window.
    assert me.active_window(cfg, at(19, 0)) is None


def test_pre_market_release_moves_the_window_to_the_open():
    bls = [me._event("bls", "Consumer Price Index", datetime(2026, 9, 11, 8, 30, tzinfo=ET), impact="high", kind="cpi")]
    _set_rows(bls)
    cfg = {"event_guard_after_min": 15}
    assert me.active_window(cfg, datetime(2026, 9, 11, 8, 30, tzinfo=ET)) is None  # moved to the open
    window = me.active_window(cfg, datetime(2026, 9, 11, 9, 40, tzinfo=ET))
    assert window and window["window_start"].startswith("2026-09-11T09:30")
    assert me.active_window(cfg, datetime(2026, 9, 11, 9, 46, tzinfo=ET)) is None


def test_static_fomc_dates_work_offline_and_dedupe_with_the_feed():
    _set_rows(me.parse_fed_calendar(FED))
    rows = me.events({}, datetime(2026, 9, 15, 12, 0, tzinfo=ET))
    pressers = [r for r in rows if r["kind"] == "fomc_press_conference" and r["start"].startswith("2026-09-16")]
    assert len(pressers) == 1  # feed row and static fallback are one event
    _set_rows([])
    offline = me.events({}, datetime(2026, 9, 15, 12, 0, tzinfo=ET))
    assert any(r["kind"] == "fomc_statement" and r["start"].startswith("2026-09-16") for r in offline)


def test_upcoming_lists_medium_and_high_with_minutes_away():
    _set_rows(me.parse_fed_calendar(FED))
    rows = me.upcoming({}, datetime(2026, 9, 24, 9, 0, tzinfo=ET), hours=12)
    titles = [r["title"] for r in rows]
    assert any("Warsh" in t for t in titles) and any("Jefferson" in t for t in titles)
    assert not any("H.4.1" in t for t in titles)  # low impact hidden by default
    assert [r for r in rows if "Warsh" in r["title"]][0]["minutes_away"] == 60


def test_refresh_keeps_last_good_rows_when_a_source_fails(monkeypatch):
    calls = {"n": 0}

    def fed():
        calls["n"] += 1
        if calls["n"] > 1:
            raise ValueError("HTTP 503")
        return me.parse_fed_calendar(FED)
    monkeypatch.setattr(me, "_source_fed", fed)
    monkeypatch.setattr(me, "_source_bls", lambda: me.parse_ics(BLS) and [])
    monkeypatch.setenv("PRESIDENT_SCHEDULE_ICS", "off")
    me.refresh()
    first = len(me._state["by_source"]["fed"])
    status = me.refresh()
    assert len(me._state["by_source"]["fed"]) == first > 0
    assert status["sources"]["fed"]["ok"] is False and "503" in status["sources"]["fed"]["error"]
    assert status["president_feed"] is False


def test_cache_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(me, "_source_fed", lambda: me.parse_fed_calendar(FED))
    monkeypatch.setattr(me, "_source_bls", lambda: [])
    monkeypatch.setenv("PRESIDENT_SCHEDULE_ICS", "off")
    me.refresh()
    me._state.update(by_source={}, loaded=False, at=0.0)
    rows = me.events({}, datetime(2026, 9, 24, 9, 0, tzinfo=ET))
    assert any("Warsh" in r["title"] for r in rows)


def test_describe_is_portable():
    row = me._event("fed", "FOMC Press Conference", datetime(2026, 9, 16, 14, 30, tzinfo=ET), impact="high",
                    kind="fomc_press_conference")
    assert me.describe(row, datetime(2026, 9, 16, 9, 0, tzinfo=ET)) == "FOMC Press Conference (2:30 PM ET)"
    assert me.describe(row, datetime(2026, 9, 15, 9, 0, tzinfo=ET)) == "FOMC Press Conference (Wed Sep 16, 2:30 PM ET)"


def test_bls_uses_owner_contact_and_explains_403(monkeypatch):
    calls = []

    def fake_fetch(url, user_agent=me.UA):
        calls.append(user_agent)
        if "@" not in user_agent:
            raise ValueError("HTTP 403")
        return BLS

    monkeypatch.setattr(me, "_fetch", fake_fetch)
    monkeypatch.delenv("BLS_USER_AGENT", raising=False)
    try:
        me._source_bls()
    except ValueError as exc:
        assert "BLS_USER_AGENT" in str(exc)
    else:
        raise AssertionError("403 without a contact must fail")
    monkeypatch.setenv("BLS_USER_AGENT", "no contact here")
    assert me.bls_user_agent() == ""
    monkeypatch.setenv("BLS_USER_AGENT", "nadzeel-desk/1.0 (owner@example.com)")
    rows = me._source_bls()
    assert rows and calls[-1] == "nadzeel-desk/1.0 (owner@example.com)"
