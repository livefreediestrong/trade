from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as desk


def test_daily_recap_is_descriptive_and_after_hours(monkeypatch):
    day = desk._today_str()
    monkeypatch.setattr(
        desk.paper_loop_mod,
        "session_close_time",
        lambda _date: desk.datetime.now(desk.paper_loop_mod.NY_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).time(),
    )
    recap = desk.daily_recap(
        {"daily_profit_target_usd": 20},
        {"daily": {day: {"pnl": 8.5}}, "fills": [
            {"ts": f"{day}T15:00:00+00:00", "ticker": "AAPL", "realized_pnl": 10, "fee_usd": 1.5}
        ], "positions": []},
    )
    assert recap["market_closed"] is True
    assert recap["pnl_usd"] == 8.5
    assert recap["fees_usd"] == 1.5
    assert recap["fills"] == 1
    assert "does not authorize" in recap["note"]


def test_daily_recap_accepts_real_calendar_time_type():
    recap = desk.daily_recap({}, {'daily': {}, 'fills': []})
    assert isinstance(recap['market_closed'], bool)


def test_daily_recap_groups_utc_records_by_eastern_trading_day(monkeypatch):
    rows=[{'ts':'2026-09-24T00:30:00Z','ticker':'SPY'},
          {'ts':'2026-09-24T04:30:00Z','ticker':'QQQ'},
          {'ts':'2026-09-23T20:30:00','ticker':'UNKNOWN'}]
    monkeypatch.setattr(desk._decision_ring,'latest',lambda *a:rows)
    recap=desk.daily_recap({}, {'fills':rows}, day='2026-09-23')
    assert recap['fills']==1 and recap['decisions']==1 and recap['top_tickers']==['SPY']
