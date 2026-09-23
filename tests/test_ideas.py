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


# ------------------------------------------------------------- backtest

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import backtest  # noqa: E402
import screener_logic  # noqa: E402


def _hourly(days=90, spike_every=7, drift=0.002, start=100.0):
    """Synthetic 7-bar sessions; every Nth day has a 3x first-hour volume spike and
    rallies after 10:30 (so a correct rule should find profit)."""
    rows, idx = [], []
    px = start
    d0 = pd.Timestamp("2025-01-06", tz="America/New_York")
    day = 0
    k = 0
    while day < days:
        ts = d0 + pd.Timedelta(days=k)
        k += 1
        if ts.weekday() >= 5:
            continue
        spike = day % spike_every == 0 and day >= 55
        for h in range(7):
            t = ts + pd.Timedelta(hours=9, minutes=30) + pd.Timedelta(hours=h)
            o = px
            px = px * (1 + drift / 7 + (0.004 if (spike and h >= 1) else 0))
            vol = 3_000_000 if (spike and h == 0) else 1_000_000
            rows.append({"Open": o, "High": max(o, px) * 1.0005, "Low": min(o, px) * 0.9995, "Close": px, "Volume": vol})
            idx.append(t)
        day += 1
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def test_backtest_finds_planted_edge_and_uses_no_future():
    df = _hourly()
    trades = backtest.simulate_ticker(df, {})
    rule = [t for t in trades if t["rule"]]
    assert rule and all(t["ret"] > 0 for t in rule)  # spikes rally → rule trades win
    # No look-ahead: truncating the future must not change past decisions
    cut = trades[len(trades) // 2]["day"]
    df_cut = df[[ts.strftime("%Y-%m-%d") <= cut for ts in df.index]]
    t2 = backtest.simulate_ticker(df_cut, {})
    assert [(t["day"], t["rule"]) for t in t2] == [(t["day"], t["rule"]) for t in trades if t["day"] <= cut]


def test_backtest_verdict_not_enough_with_little_data():
    res = backtest.evaluate(backtest.simulate_ticker(_hourly(days=70), {}), {})
    assert res["ok"] and res["verdict"] == "not_enough"


def test_backtest_run_with_fake_fetch(tmp_path):
    res = backtest.run(["AAA", "BBB"], tmp_path / "bt.json", {}, fetch=lambda t: _hourly(days=120))
    assert res["ok"] and res["tickers"] == ["AAA", "BBB"]
    assert backtest.last_result(tmp_path / "bt.json")["period"]["start"]


# ------------------------------------------------------------- scanner signals

def test_intraday_signals_vwap_and_relvol():
    idx = pd.date_range("2026-09-18 09:30", periods=12, freq="5min", tz="America/New_York").append(
        pd.date_range("2026-09-21 09:30", periods=12, freq="5min", tz="America/New_York")).append(
        pd.date_range("2026-09-22 09:30", periods=12, freq="5min", tz="America/New_York"))
    vol = [1000] * 24 + [1000] * 10 + [5000, 100]
    close = list(np.linspace(100, 101, 12)) * 2 + list(np.linspace(101, 99, 12))
    df = pd.DataFrame({"Open": close, "High": [c + 0.1 for c in close], "Low": [c - 0.1 for c in close],
                       "Close": close, "Volume": vol}, index=idx)
    sig = screener_logic.intraday_signals(df, atr_usd=2.0, price=99.0)
    assert sig["rel_vol_5m"] == 5.0
    assert sig["vwap_slope_pct"] < 0 and sig["vwap_dist_atr"] < 0
    v, flags, notes = screener_logic.intraday_adjust("PASS", sig)
    assert v == "WATCH" and "below_falling_vwap" in flags and "volume_surge_5m" in flags


# ------------------------------------------------------------- Claude brain

class _FakeResp:
    def __init__(self, text, stop="end_turn"):
        self.content = [type("B", (), {"type": "text", "text": text})()]
        self.stop_reason = stop
        self.model = "claude-opus-5"
        self.usage = type("U", (), {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 0})()


def _fake_client(resp):
    msgs = type("M", (), {"create": lambda self, **kw: resp})()
    beta = type("Bt", (), {"messages": msgs})()
    return type("C", (), {"beta": beta})()


def test_claude_brain_parses_and_costs(monkeypatch):
    import claude_brain
    monkeypatch.setattr(claude_brain, "is_configured", lambda: True)
    monkeypatch.setattr(claude_brain, "_get_client", lambda: _fake_client(_FakeResp(
        '{"horizon":"higher","side":"buy","confidence":0.72,"thesis":"x","risks":[]}')))
    out = claude_brain.decide({"ticker": "AAPL"})
    assert out["side"] == "buy" and out["confidence"] == 0.72 and out["brain_mode"] == "claude"
    assert out["model_cost_usd"] == pytest.approx(1000 / 1e6 * 5 + 200 / 1e6 * 25)


def test_claude_refusal_and_unconfigured_hold(monkeypatch):
    import claude_brain
    monkeypatch.setattr(claude_brain, "is_configured", lambda: True)
    monkeypatch.setattr(claude_brain, "_get_client", lambda: _fake_client(_FakeResp("", stop="refusal")))
    assert claude_brain.decide({"ticker": "AAPL"})["error"] == "claude_refusal"
    monkeypatch.setattr(claude_brain, "is_configured", lambda: False)
    assert claude_brain.decide({"ticker": "AAPL"})["side"] == "flat"


def test_claude_shadow_needs_key(client, monkeypatch):
    import claude_brain
    monkeypatch.setattr(claude_brain, "is_configured", lambda: False)
    r = client.post("/api/config", base_url=BASE, json={"claude_shadow": True})
    assert r.status_code == 400


def test_brain_scoreboard_counts_same_decisions():
    for i, (m, c) in enumerate([("helped", "hurt"), ("hurt", "helped"), ("helped", "helped")]):
        ev = _ev(i, m)
        ev.update(brain_mode="gemini", shadow_claude_outcome=c)
        lessons.record_outcome(desk.LESSONS_PATH, ev)
    lessons.record_outcome(desk.LESSONS_PATH, _ev(9, "helped"))  # no claude → excluded
    s = desk.brain_scoreboard(days=100000)
    assert s["compared_calls"] == 3 and s["main"]["helped"] == 2 and s["claude"]["helped"] == 2
