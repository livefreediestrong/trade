"""Scorecard is display only and never calls a small sample a winner."""
from datetime import datetime, timedelta, timezone

import strategy_scorecard as sc

NOW = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)
BASE = "http://127.0.0.1:5056"


def call(setup="PASS|fair", side="buy", net=10.0, days_ago=1, **extra):
    return {"setup": setup, "side": side, "outcome_status": "scored", "outcome_executable_move_bps": net,
            "outcome": "helped" if net > 0 else "hurt", "ts": (NOW - timedelta(days=days_ago)).isoformat(), **extra}


def test_small_samples_are_never_called_winners():
    out = sc.scorecard([call(net=50.0)] * 5, now=NOW)
    row = out["rows"][0]
    assert row["status"] == "too_few" and "5 of 30" in row["plain"] and row["win_rate"] == 1.0


def test_losing_and_positive_setups_after_enough_calls_with_dollar_example():
    rows = [call(net=-12.0)] * 30 + [call(setup="WATCH|early", side="sell", net=4.0)] * 40
    out = sc.scorecard(rows, now=NOW)
    losing, positive = out["rows"]
    assert losing["status"] == "losing" and "cost money" in losing["plain"]
    assert losing["example"] == "On a $1,000 trade, -12.0 bps is about -$1.20 per call after costs."
    assert positive["status"] == "positive" and "Keep testing" in positive["plain"]
    assert positive["label"] == "Watch-list idea, early entry · sell calls"
    assert "1 positive after costs, 1 losing" in out["summary"] and out["display_only"]


def test_mock_routed_old_and_unscored_rows_are_left_out_and_hold_calls_counted_separately():
    rows = [call(mock=True), call(routed=True), call(days_ago=40), dict(call(), outcome_status="pending"),
            call(side="flat", outcome="helped"), call(side="flat", outcome="hurt")]
    out = sc.scorecard(rows, now=NOW)
    assert out["rows"] == [] and out["stayed_out"] == {"calls": 2, "right": 1}
    assert "Staying out was right 1 of 2 times" in out["summary"]


def test_route_reads_lessons_and_validates_days(monkeypatch):
    import app as desk
    fresh = dict(call(net=-3.0), ts=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    monkeypatch.setattr(sc.lessons, "recent", lambda path, limit: [fresh])
    client = desk.app.test_client()
    assert client.get("/api/strategy-scorecard?days=x", base_url=BASE).status_code == 400
    data = client.get("/api/strategy-scorecard?days=7", base_url=BASE).get_json()
    assert data["ok"] and data["days"] == 7 and data["rows"][0]["avg_net_bps"] == -3.0


def test_future_invalid_naive_and_offset_out_of_period_calls_cannot_count_as_evidence():
    cutoff = NOW - timedelta(days=30)
    excluded = [None, call(ts="garbage"), call(ts="2026-09-25T10:00:00"),
                call(days_ago=-1), call(ts="2026-08-26T21:00:00+02:00")]
    included = [call(ts=cutoff.isoformat()), call(ts="2026-08-26T19:00:00-02:00"), call(ts=NOW.isoformat())]
    result = sc.scorecard(excluded + included, now=NOW)
    assert result["rows"][0]["scored"] == 3
    assert sc.scorecard(excluded, now=NOW)["rows"] == []


def test_boolean_nonfinite_results_are_not_numerical_evidence():
    rows = [call(outcome_executable_move_bps=x) for x in [True, False, float("nan"), float("inf")]]
    result = sc.scorecard(rows, now=NOW)["rows"][0]
    assert result["scored"] == 0 and result["avg_net_bps"] is None
