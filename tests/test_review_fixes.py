"""Regression tests for the 10-agent review fixes (2026-09-22).

Run:  .venv\\Scripts\\python -m pytest tests -q
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

os.environ.setdefault("TOMAHAWK_DATA_DIR", tempfile.mkdtemp(prefix="tomahawk_test_"))
os.environ["TOMAHAWK_NO_BG"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as desk  # noqa: E402


def test_atr_risk_sizing_caps_requested_shares():
    sig = {
        "ticker": "AAPL",
        "signal_price": 10.0,
        "suggested_shares": 100,
        "atr_usd": 2.0,
    }
    shares, notional, error = desk._cap_shares_for_broker(
        sig,
        {"risk_preset": "mid", "risk_per_trade_pct": 0.25, "atr_stop_multiple": 1.0},
        {"equity": 10_000},
        equity_override=10_000,
    )
    assert error is None
    assert shares == 12
    assert notional == 120.0
    assert sig["size_capped_for_atr_risk"] is True


def test_signal_uses_atr_stop_distance(monkeypatch):
    monkeypatch.setattr(desk, "load_ledger", lambda: {"equity": 10_000})
    cfg = desk.load_config()
    cfg.update(atr_stop_multiple=1.5)
    from app import _analysis_to_signal
    sig = _analysis_to_signal({
        "ticker": "AAPL",
        "price": 100.0,
        "verdict": "PASS",
        "verdict_text": "clean setup",
        "entry_quality": {"label": "early"},
        "intraday": {"atr_usd": 2.0, "spread_atr": 0.02},
        "volume": {"rel_vol": 2.0, "session_is_today": True},
        "checks": {},
        "sources": [],
    }, cfg, desk.get_preset("mid"), force=True)
    assert sig["atr_usd"] == 2.0
    assert sig["stop"] == 97.0
    assert sig["spread_atr"] == 0.02


def test_promotion_requires_net_expectancy_and_profit_factor():
    now = datetime.now().astimezone().isoformat()
    cfg = {
        "paper_equity": 10_000,
        "promotion_window_days": 30,
        "promotion_min_samples": 2,
        "promotion_min_win_rate": 0.5,
        "promotion_min_expectancy_usd": 0,
        "promotion_min_profit_factor": 1.05,
    }
    losing = {
        "fills": [
            {"ts": now, "position_id": "a", "realized_pnl": 2, "fee_usd": 0.5},
            {"ts": now, "position_id": "b", "realized_pnl": -1, "fee_usd": 0.5},
        ]
    }
    evidence = desk._promotion_gate(cfg, losing)
    assert evidence["checks"]["positive_expectancy"] is False
    assert evidence["checks"]["profit_factor"] is False
    assert evidence["eligible"] is False
import llm_trader  # noqa: E402
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
    ):
        monkeypatch.setattr(desk, name, tmp_path / fname)
    desk._CORRUPT_PATHS.clear()
    with desk._MARKS_LOCK:
        desk._MARKS.clear()
    yield tmp_path


@pytest.fixture
def client():
    return desk.app.test_client()


# ------------------------------------------------------------- Gemini key never in errors

def test_redact_secrets():
    txt = "Max retries exceeded with url: /v1beta/models/x:generateContent?key=AIzaSyA1234567890abcdefghijklmn"
    out = llm_trader.redact_secrets(txt, "AIzaSyA1234567890abcdefghijklmn")
    assert "AIza" not in out and "key=[redacted]" in out


def test_gemini_network_error_has_no_key(monkeypatch):
    import requests

    fake = "AIzaFAKEFAKEFAKEFAKEFAKEFAKE1234"
    seen = {}

    def boom(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers") or {}
        seen["params"] = kw.get("params")
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}?key={fake}")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(RuntimeError) as ei:
        llm_trader.gemini_generate("p", cfg={"api_key": fake, "model": "m"}, timeout_sec=1)
    assert fake not in str(ei.value)
    assert "key=" not in seen["url"] and not seen["params"]
    assert seen["headers"].get("x-goog-api-key") == fake


# ------------------------------------------------------------- model confidence

@pytest.mark.parametrize("raw,expect", [
    (0.7, 0.7), (85, 0.85), ("85%", 0.85), ("0.6", 0.6),
    (True, None), (float("nan"), None), (float("inf"), None), (1e9, None), (-1, None), (None, None),
])
def test_safe_confidence(raw, expect):
    got = llm_trader.safe_confidence(raw)
    assert (got is None and expect is None) or (got is not None and math.isclose(got, expect))


def test_side_horizon_conflict_holds():
    t = llm_trader._normalize_thesis({"horizon": "higher", "side": "sell", "confidence": 0.9}, "", "m")
    assert t["side"] == "flat" and t["confidence"] == 0 and t["error"] == "side_horizon_conflict"


def test_nan_confidence_holds():
    t = llm_trader._normalize_thesis({"side": "buy", "confidence": float("nan")}, "", "m")
    assert t["side"] == "flat" and t["error"] == "bad_confidence"


# ------------------------------------------------------------- market calendar

@pytest.mark.parametrize("stamp,open_", [
    ("2026-11-26 11:00", False),  # Thanksgiving
    ("2026-11-27 12:30", True),   # day after: open until 13:00
    ("2026-11-27 14:00", False),  # ...closed after 13:00
    ("2026-12-24 14:00", False),  # Christmas Eve early close
    ("2026-12-25 11:00", False),
    ("2026-07-03 11:00", False),  # Jul 4 on Saturday → observed Friday
    ("2026-09-07 11:00", False),  # Labor Day
    ("2026-04-03 11:00", False),  # Good Friday
    ("2027-12-31 11:00", True),   # Saturday New Year is NOT observed on Dec 31
    ("2026-09-22 11:00", True),
    ("2026-09-19 11:00", False),  # Saturday
])
def test_is_rth_calendar(stamp, open_):
    dt = datetime.fromisoformat(stamp).replace(tzinfo=paper_loop.NY_TZ)
    assert paper_loop.is_rth(dt) is open_


# ------------------------------------------------------------- numeric validation

@pytest.mark.parametrize("bank", ["NaN", "Infinity", "abc", -5, 0, True])
def test_session_start_rejects_bad_numbers(client, bank):
    r = client.post("/api/session/start", base_url=BASE,
                    json={"make_today_usd": 50, "beginning_bank_usd": bank})
    assert r.status_code == 400
    assert not desk.load_config().get("session_active")


def test_config_rejects_nan_and_bad_killswitch(client):
    assert client.post("/api/config", base_url=BASE, json={"paper_equity": "NaN"}).status_code == 400
    assert client.post("/api/config", base_url=BASE, json={"slip_bps": "abc"}).status_code == 400
    r = client.post("/api/config", base_url=BASE, json={"kill_switch": {"max_trades_per_day": "lots"}})
    assert r.status_code == 400


def test_nan_on_disk_is_sanitized(isolated_data):
    desk.CONFIG_PATH.write_text('{"paper_equity": NaN, "slip_bps": 5}', encoding="utf-8")
    cfg = desk.load_config()
    assert cfg["paper_equity"] == desk.DEFAULT_CONFIG.get("paper_equity")


def test_fee_zero_is_respected():
    assert desk._cfg_fee_bps({"fee_bps": 0}) == 0.0
    assert desk._cfg_fee_bps({}) == 1.0


# ------------------------------------------------------------- session start / stop

def test_start_keeps_ask_me_first(client):
    r = client.post("/api/session/start", base_url=BASE,
                    json={"make_today_usd": 50, "beginning_bank_usd": 1000, "mode": "manual"})
    assert r.status_code == 200, r.get_json()
    assert desk.load_config()["mode"] == "manual"
    client.post("/api/session/stop", base_url=BASE, json={})
    assert desk.load_config()["mode"] == "manual"  # stop no longer rewrites mode


def test_start_asks_before_wiping_positions(client):
    led = desk.load_ledger()
    led["positions"] = [{"ticker": "AAPL", "side": "long", "shares": 5, "avg_price": 100}]
    desk.save_ledger(led)
    r = client.post("/api/session/start", base_url=BASE,
                    json={"make_today_usd": 50, "beginning_bank_usd": 1000})
    assert r.status_code == 409 and r.get_json()["needs_confirm"]
    assert desk.load_ledger()["positions"]  # untouched
    r = client.post("/api/session/start", base_url=BASE,
                    json={"make_today_usd": 50, "beginning_bank_usd": 1000, "confirm_reset": True})
    assert r.status_code == 200
    led = desk.load_ledger()
    assert led["positions"] == [] and led["positions_archive"][0]["ticker"] == "AAPL"


def test_exits_allowed_after_stop(monkeypatch):
    monkeypatch.setattr(paper_loop, "is_rth", lambda now=None: True)
    cfg = dict(desk.load_config(), session_active=False)
    ok, _ = desk.can_take_trade(cfg, desk.load_ledger(), 100.0, reducing=True)
    assert ok
    ok, _ = desk.can_take_trade(cfg, desk.load_ledger(), 100.0, reducing=False)
    assert not ok


def test_stop_race_blocks_stale_snapshot(monkeypatch):
    """cfg snapshot says active, but STOP already landed on disk → no new risk."""
    monkeypatch.setattr(paper_loop, "is_rth", lambda now=None: True)
    cfg = desk.load_config()
    cfg["session_active"] = False
    desk.save_config(cfg)
    stale = dict(cfg, session_active=True)
    ok, reason = desk.can_take_trade(stale, desk.load_ledger(), 100.0)
    assert not ok and "Session" in reason


# ------------------------------------------------------------- expiry race

def test_expiry_never_resurrects_approving():
    past = "2000-01-01T00:00:00+00:00"
    snap = [
        {"id": "A", "status": "pending", "expires_at": past},
        {"id": "B", "status": "pending", "expires_at": "2999-01-01T00:00:00+00:00"},
    ]
    desk.save_signals(snap)
    stale = [dict(s) for s in snap]
    # Meanwhile: B is being approved and C arrives
    fresh = desk.load_signals()
    fresh[1]["status"] = "approving"
    fresh.insert(0, {"id": "C", "status": "pending"})
    desk.save_signals(fresh)
    desk.expire_stale_signals(stale)
    by_id = {s["id"]: s for s in desk.load_signals()}
    assert by_id["A"]["status"] == "expired"
    assert by_id["B"]["status"] == "approving"
    assert "C" in by_id


# ------------------------------------------------------------- MTM loss limits

def test_loss_limit_counts_open_positions(monkeypatch):
    monkeypatch.setattr(paper_loop, "is_rth", lambda now=None: True)
    led = desk.load_ledger()
    led["positions"] = [{"ticker": "ZZZ", "side": "long", "shares": 100, "avg_price": 100.0}]
    desk.save_ledger(led)
    with desk._MARKS_LOCK:
        import time
        desk._MARKS["ZZZ"] = (90.0, time.monotonic())  # down $1,000
    cfg = dict(desk.load_config(), session_active=True, max_session_loss_usd=500)
    desk.save_config(cfg)
    ok, reason = desk.can_take_trade(cfg, desk.load_ledger(), 10.0)
    assert not ok and "loss" in reason.lower()


# ------------------------------------------------------------- exit levels

def test_wrong_side_exits_rejected():
    ex = session_track.resolve_exit_prices(
        entry_px=100.0, side="buy", preset={"stop_r": 1, "target_r": 2},
        body={"stop": 105, "target": 95},
    )
    assert ex["stop_price"] is None and ex["take_profit_price"] is None and ex["rejected"]


def test_nan_stop_rejected():
    ex = session_track.resolve_exit_prices(
        entry_px=100.0, side="buy", preset={"stop_r": 1, "target_r": 2},
        body={"stop_loss_pct": "nan", "take_profit_pct": 2},
    )
    assert ex["stop_price"] is None and ex["take_profit_price"] == 102.0


# ------------------------------------------------------------- broker shorts

def test_broker_sell_without_position_refused(monkeypatch):
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (0.0, 100_000.0, None))
    monkeypatch.setattr(desk, "_broker_position_qty", lambda t: (0.0, None))
    sent = []
    monkeypatch.setattr(desk, "live_broker_place_order", lambda o: sent.append(o))
    sig = {"id": "s", "ticker": "AAPL", "side": "sell", "signal_price": 100.0, "suggested_shares": 10}
    res = desk.execute_gated_broker_or_paper(sig, dict(desk.load_config(), session_active=True),
                                             source="t", via="t")
    assert res["ok"] is False and sent == [] and "short" in res["error"].lower()


def test_broker_sell_clamped_to_held_and_treated_as_exit(monkeypatch):
    import broker_alpaca

    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (-99_999.0, 100_000.0, None))  # loss cap blown
    monkeypatch.setattr(desk, "_broker_position_qty", lambda t: (4.0, None))
    seen = {}
    monkeypatch.setattr(desk, "can_take_trade", lambda c, l, n, **k: (seen.update(k) or True, "ok"))
    sent = []
    monkeypatch.setattr(desk, "live_broker_place_order",
                        lambda o: sent.append(o) or {"ok": True, "status": "paper_submitted", "order_id": "o", "qty": str(o["shares"])})
    monkeypatch.setattr(broker_alpaca, "wait_for_fill",
                        lambda oid, timeout=0: {"state": "filled", "filled_qty": 4.0, "filled_avg_price": 99.0})
    sig = {"id": "s", "ticker": "AAPL", "side": "sell", "signal_price": 100.0, "suggested_shares": 10}
    res = desk.execute_gated_broker_or_paper(sig, dict(desk.load_config(), session_active=True),
                                             source="t", via="t")
    assert res["ok"] and sent[0]["shares"] == 4 and seen.get("reducing") is True


# ------------------------------------------------------------- New signal never trades

def test_generate_is_research_only(client, monkeypatch):
    cfg = desk.load_config()
    cfg.update(mode="auto_paper", session_active=True)
    desk.save_config(cfg)
    sig = {"id": "g1", "ticker": "AAPL", "side": "buy", "confidence": None, "signal_price": 10.0,
           "suggested_shares": 1, "verdict": "PASS"}
    monkeypatch.setattr(desk, "generate_scan_signal", lambda cfg, force=True, ticker=None: dict(sig))
    fills = []
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: fills.append(a) or {"ok": True})
    r = client.post("/api/signals/generate", base_url=BASE, json={})
    assert r.status_code == 200 and fills == []
    assert desk.load_signals()[0]["status"] == "pending"
    assert client.post("/api/signals/generate", base_url=BASE, json={"ticker": 5}).status_code == 400


# ------------------------------------------------------------- JSON never contains NaN

def test_json_responses_have_no_nan(client, monkeypatch):
    monkeypatch.setattr(desk, "load_config", lambda: {"paper_equity": float("nan"), "mode": "manual"})
    r = client.get("/api/config", base_url=BASE)
    assert b"NaN" not in r.data


# ------------------------------------------------------------- macro calendar

def test_fomc_day_flagged(monkeypatch):
    import macro_calendar

    monkeypatch.setattr(macro_calendar, "is_fred_configured", lambda: False)
    r = macro_calendar.fred_release_flags(date(2026, 9, 16))
    assert r["high_impact"] and r["flags"][0]["kind"] == "fed"
    r = macro_calendar.fred_release_flags(date(2026, 9, 22))
    assert not r["high_impact"]


def test_fred_outage_is_reported(monkeypatch):
    import macro_calendar

    monkeypatch.setattr(macro_calendar, "is_fred_configured", lambda: True)
    monkeypatch.setattr(macro_calendar, "_fred_get", lambda *a, **k: None)
    r = macro_calendar.fred_release_flags(date(2026, 9, 11))
    assert r["fred_error"] is True


# ------------------------------------------------------------- slow-bleed guard

def _losing_fills(n, loss=-2.0, fee=0.5, start_min=0):
    out = []
    for i in range(n):
        ts = f"2026-09-22T14:{start_min + i:02d}:00+00:00"
        out.append({"id": f"b{i}", "ts": ts, "side": "buy", "fee_usd": fee})
        out.append({"id": f"s{i}", "ts": ts.replace(":00+", ":30+"), "side": "sell",
                    "realized_pnl": loss, "fee_usd": fee})
    return out


def test_bleed_pauses_after_losing_streak(monkeypatch):
    monkeypatch.setattr(paper_loop, "is_rth", lambda now=None: True)
    led = desk.load_ledger()
    led["fills"] = _losing_fills(12)
    desk.save_ledger(led)
    cfg = dict(desk.load_config(), session_active=True)
    desk.save_config(cfg)
    bs = desk.bleed_status(desk.load_ledger(), cfg)
    assert bs["paused"] and bs["closed_trades"] == 12 and bs["net_usd"] < 0
    ok, reason = desk.can_take_trade(cfg, desk.load_ledger(), 10.0)
    assert not ok and "after costs" in reason
    ok, _ = desk.can_take_trade(cfg, desk.load_ledger(), 10.0, reducing=True)
    assert ok  # exits still allowed


def test_bleed_needs_enough_trades_and_counts_fees():
    cfg = desk.load_config()
    assert not desk.bleed_status({"fills": _losing_fills(5)}, cfg)["paused"]  # too few to judge
    # Each trade wins $0.80 but pays $1.00 in fees → net negative → pause
    fills = _losing_fills(12, loss=0.8, fee=0.5)
    bs = desk.bleed_status({"fills": fills}, cfg)
    assert bs["realized_usd"] > 0 and bs["net_usd"] < 0 and bs["paused"]


def test_bleed_resume_resets_window(client):
    led = desk.load_ledger()
    led["fills"] = _losing_fills(12)
    desk.save_ledger(led)
    assert desk.bleed_status(desk.load_ledger(), desk.load_config())["paused"]
    r = client.post("/api/bleed/resume", base_url=BASE, json={})
    assert r.status_code == 200 and not r.get_json()["bleed"]["paused"]


# ------------------------------------------------------------- benchmark vs SPY

def test_benchmark_vs_spy(monkeypatch):
    monkeypatch.setattr(desk, "_spy_now", lambda: 505.0)  # SPY +1%
    led = desk.load_ledger()
    led.update(cash=10_050.0, positions=[])
    cfg = dict(desk.load_config(), session_spy_start=500.0, session_bank_start=10_000.0)
    b = desk.benchmark_status(led, cfg)
    assert b["ok"] and b["desk_usd"] == 50.0 and b["spy_usd"] == 100.0 and b["ahead_usd"] == -50.0


def test_benchmark_absent_without_anchor():
    assert desk.benchmark_status(desk.load_ledger(), desk.load_config()) is None
