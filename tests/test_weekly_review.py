import copy
import json
from datetime import datetime, timedelta, timezone
import pytest
import weekly_review as week
import after_close_review as night
from test_after_close_review import service, event, NOW, DAY


@pytest.fixture
def weekly(service):
    daily, cfg, journal = service
    daily.companion.after_close = daily
    def save(path, raw):
        assert path.name in ("weekly_reviews.json", "after_close_reviews.json")
        path.write_text(json.dumps(raw))
    daily.desk._save_json = save
    return week.WeeklyReview(daily.desk, daily.companion), cfg, journal


def test_last_session_week_holiday_early_close_dst_catchup():
    due, nxt = week.schedule(datetime(2026, 9, 25, 21, tzinfo=timezone.utc))
    assert due.date().isoformat() == "2026-09-18"
    assert nxt.isoformat() == "2026-09-25T17:30:00-04:00"
    assert week.schedule(datetime(2026, 9, 27, 23, tzinfo=timezone.utc))[0].date().isoformat() == DAY
    assert week.schedule(datetime(2026, 4, 3, 12, tzinfo=timezone.utc))[0].date().isoformat() == "2026-04-02"  # Good Friday closed
    assert week.week_time(datetime(2026, 11, 23).date()).isoformat() == "2026-11-27T14:30:00-05:00"


def test_whole_week_weights_records_corrections_future_and_missing_sessions(weekly, monkeypatch):
    s, cfg, _ = weekly
    old = event("2026-09-21", "a", 100)
    corrected = dict(old, error="withdrawn after review")
    rows = [corrected, event("2026-09-22", "b", 10), dict(event("2026-09-22", "c", 30), ticker="QQQ"),
            event(DAY, "d", -10), event("2026-09-28", "future", 999), event("2026-09-18", "prior", -20)]
    s.companion.events = lambda: rows
    monkeypatch.setattr(night.moss_paper, "history", lambda _: [old, rows[1]])
    s.companion.after_close.save({"reports": {"2026-09-22": {"evidence": {"headlines": [], "documents": []}}}})
    before = copy.deepcopy(cfg)
    e = s.collect(DAY, NOW + timedelta(hours=1))
    assert e["outcomes"]["today"]["outcomes"] == 3
    assert e["outcomes"]["today"]["mean_net_bps"] == 10  # not average of daily means (5)
    assert e["outcomes"]["prior_20_observed_sessions"]["mean_net_bps"] == -20
    assert "a" not in e["outcomes"]["qualified_event_ids"] and "future" not in e["outcomes"]["qualified_event_ids"]
    assert len(e["period"]["missing_daily_reports"]) == 4
    assert e["outcomes"]["verdict"]["state"] == "collecting"
    assert cfg == before and "SECRET-ACCOUNT" not in json.dumps(e)


def test_weekly_restart_receipts_and_disabled_scheduling(weekly, monkeypatch):
    s, cfg, _ = weekly
    e = s.collect(DAY, NOW + timedelta(hours=1))
    s.save({"reports": {DAY: {"day": DAY, "kind": "weekly", "status": "running", "evidence": e, "models": {"changing_woman": {"status": "attempted"}}}}})
    calls = []
    monkeypatch.setattr(s, "_review", lambda day, role, evidence, earlier: calls.append(role) or {"status": "complete", "result": {}})
    s._work(DAY, NOW + timedelta(hours=1))
    assert calls == ["fox"] and s.load()["reports"][DAY]["status"] == "partial"
    assert not s.tick(NOW + timedelta(hours=2))


def test_weekly_fill_dedup_account_isolation_and_retention(weekly):
    s, _, journal = weekly
    fill = {"execution_id": "fill-1", "account_id": "SECRET-ACCOUNT", "paper_mode": False, "verified": True,
            "ts": "2026-09-21T15:00:00+00:00", "order_id": 1, "commission": 1, "currency": "USD"}
    journal["executions"] = [fill, fill, dict(fill, execution_id="wrong", account_id="other")]
    e = s.collect(DAY, NOW + timedelta(hours=1))
    assert e["executions"]["live"]["executions"] == 1
    s.save({"reports": {str(i).zfill(3): {"day": str(i), "status": "partial"} for i in range(60)}})
    assert len(s.load()["reports"]) == 52
