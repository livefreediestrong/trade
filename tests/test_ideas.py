"""Tests for the YouTube-inspired features: trailing stops, lessons memory,
midday risk check, weekly report card, scanner signals, backtest."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("TOMAHAWK_DATA_DIR", tempfile.mkdtemp(prefix="tomahawk_test_"))
os.environ["TOMAHAWK_NO_BG"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as desk  # noqa: E402
import lessons  # noqa: E402
import paper_loop  # noqa: E402
import session_track  # noqa: E402

BASE = "http://127.0.0.1:5056"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    for name, fname in (
        ("CONFIG_PATH", "config.json"),
        ("SIGNALS_PATH", "signals.json"),
        ("LEDGER_PATH", "ledger.json"),
        ("JOURNAL_PATH", "journal.json"),
        ("LESSONS_PATH", "lessons.json"),
    ):
        monkeypatch.setattr(desk, name, tmp_path / fname)
    desk._CORRUPT_PATHS.clear()
    yield tmp_path


@pytest.fixture
def client():
    return desk.app.test_client()


# ------------------------------------------------------------- trailing stops

def test_trailing_stop_resolves_and_ratchets_up_only():
    ex = session_track.resolve_exit_prices(
        entry_px=100.0, side="buy", preset={"stop_r": 1, "target_r": 2}, body={"trail_pct": "3%"}
    )
    assert ex["trail_pct"] == 3.0 and ex["stop_price"] == 97.0
    pos = {"side": "long", "avg_price": 100.0, "stop_price": 97.0, "trail_pct": 3.0, "trail_high": 100.0}
    assert session_track.ratchet_trailing_stop(pos, 110.0) == pytest.approx(106.7)
    assert session_track.ratchet_trailing_stop(pos, 104.0) is None  # price fell: stop stays
    assert pos["stop_price"] == pytest.approx(106.7)


def test_bad_trailing_value_is_rejected():
    ex = session_track.resolve_exit_prices(
        entry_px=100.0, side="buy", preset={"stop_r": 1, "target_r": 2}, body={"trail_pct": "90"}
    )
    assert ex["trail_pct"] is None and any("trailing" in r for r in ex["rejected"])


def test_exit_check_ratchets_and_fires(monkeypatch):
    led = desk.load_ledger()
    led["positions"] = [{"ticker": "ZZZ", "side": "long", "shares": 10, "avg_price": 100.0,
                         "stop_price": 97.0, "trail_pct": 3.0, "trail_high": 100.0}]
    desk.save_ledger(led)
    prices = iter([110.0, 106.0])
    monkeypatch.setattr(desk, "fetch_last_price", lambda t: next(prices))
    fills = []
    monkeypatch.setattr(desk, "paper_fill", lambda sig, cfg, source=None, **k: fills.append((sig, source)) or {"ok": True, "fill": {}})
    desk.check_paper_exit_intents(desk.load_config())
    assert desk.load_ledger()["positions"][0]["stop_price"] == pytest.approx(106.7)
    assert fills == []
    desk.check_paper_exit_intents(desk.load_config())  # 106 < 106.7 → stop
    assert fills and fills[0][1] == "paper_stop"


# ------------------------------------------------------------- lessons

def _ev(i, outcome, side="buy", verdict="WATCH", late="fair", ticker="AAPL", bps=-80):
    return {"id": f"d{i}", "ticker": ticker, "intended_side": side, "verdict": verdict,
            "lateness_label": late, "confidence": 0.7, "outcome": outcome,
            "outcome_move_bps": bps, "outcome_ts": f"2099-01-01T00:00:{i:02d}+00:00"}


def test_lessons_record_and_prompt_note(isolated_data):
    p = desk.LESSONS_PATH
    for i in range(4):
        lessons.record_outcome(p, _ev(i, "hurt"))
    lessons.record_outcome(p, _ev(9, "helped", bps=60))
    assert lessons.record_outcome(p, _ev(0, "hurt")) is None  # idempotent
    rec = lessons.track_record(p, ticker="AAPL", verdict="WATCH", lateness="fair")
    assert rec["setup_results"]["buy"] == {"helped": 1, "hurt": 4, "flat": 0}
    note = lessons.prompt_note(rec)
    assert "1 helped, 4 hurt" in note and "AAPL" in note


def test_lessons_reach_the_prompt(isolated_data):
    for i in range(3):
        lessons.record_outcome(desk.LESSONS_PATH, _ev(i, "hurt"))
    a = desk._with_lessons({"ticker": "AAPL", "verdict": "WATCH", "entry_quality": {"label": "fair"}})
    import llm_trader
    assert "past_results_for_similar_setups" in llm_trader._analysis_context_blob(a)


def test_no_history_means_no_note():
    a = desk._with_lessons({"ticker": "AAPL", "verdict": "PASS"})
    assert "past_results_for_similar_setups" not in a


# ------------------------------------------------------------- midday check

def test_midday_cuts_losers_and_protects_winners(monkeypatch):
    led = desk.load_ledger()
    led["positions"] = [
        {"ticker": "LOSE", "side": "long", "shares": 10, "avg_price": 100.0, "stop_price": 90.0},
        {"ticker": "WIN", "side": "long", "shares": 10, "avg_price": 100.0, "stop_price": 97.0},
        {"ticker": "MEH", "side": "long", "shares": 10, "avg_price": 100.0, "stop_price": 97.0},
    ]
    desk.save_ledger(led)
    px = {"LOSE": 92.0, "WIN": 104.0, "MEH": 100.5}
    monkeypatch.setattr(desk, "fetch_last_price", lambda t: px[t])
    sold = []
    monkeypatch.setattr(desk, "paper_fill", lambda sig, cfg, source=None, **k: sold.append(sig["ticker"]) or {"ok": True})
    now = datetime(2026, 9, 22, 12, 5, tzinfo=paper_loop.NY_TZ)
    out = desk.midday_risk_check(desk.load_config(), now=now)
    assert sold == ["LOSE"] and out["protected"][0]["ticker"] == "WIN"
    stops = {p["ticker"]: p["stop_price"] for p in desk.load_ledger()["positions"]}
    assert stops["WIN"] == 100.0 and stops["MEH"] == 97.0
    assert desk.midday_risk_check(desk.load_config(), now=now) is None  # once per day


def test_midday_waits_until_noon():
    now = datetime(2026, 9, 22, 11, 0, tzinfo=paper_loop.NY_TZ)
    assert desk.midday_risk_check(desk.load_config(), now=now) is None


# ------------------------------------------------------------- report card

def test_weekly_report_grades(client):
    from datetime import timezone
    ts = datetime.now(timezone.utc).isoformat()
    led = desk.load_ledger()
    led["fills"] = [
        {"id": f"s{i}", "ts": ts, "ticker": "AAPL", "side": "sell", "realized_pnl": 10.0, "fee_usd": 0.5}
        for i in range(6)
    ]
    desk.save_ledger(led)
    for i in range(8):
        lessons.record_outcome(desk.LESSONS_PATH, dict(_ev(i, "helped", bps=50), outcome_ts=ts))
    r = client.get("/api/report/weekly", base_url=BASE).get_json()
    assert r["grade"] == "A" and r["net_usd"] == 57.0 and r["closed_trades"] == 6


def test_weekly_report_needs_data(client):
    r = client.get("/api/report/weekly", base_url=BASE).get_json()
    assert r["grade"] == "—"
