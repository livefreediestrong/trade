"""Safety regression tests: local-only guard, corrupt-data fail-closed, broker honesty.

Run:  .venv\\Scripts\\python -m pytest tests -q
Never touches real data (TOMAHAWK_DATA_DIR → temp) or the network (TOMAHAWK_NO_BG + monkeypatch).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TOMAHAWK_DATA_DIR", tempfile.mkdtemp(prefix="tomahawk_test_"))
os.environ["TOMAHAWK_NO_BG"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as desk  # noqa: E402

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
    yield tmp_path
    desk._CORRUPT_PATHS.clear()


@pytest.fixture
def client():
    return desk.app.test_client()


# ---------------------------------------------------------------- #1 local-only guard

def test_foreign_host_blocked(client):
    r = client.get("/api/health", base_url="http://evil.example:5056")
    assert r.status_code == 403


def test_cross_origin_post_blocked(client):
    r = client.post(
        "/api/config",
        base_url=BASE,
        data='{"mode":"auto_live"}',
        headers={"Origin": "https://evil.example", "Content-Type": "text/plain"},
    )
    assert r.status_code == 403
    assert desk.load_config().get("mode") != "auto_live"


def test_cross_site_fetch_metadata_blocked(client):
    r = client.post("/api/config", base_url=BASE, json={"risk_preset": "low"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_and_script_posts_allowed(client):
    r = client.post("/api/config", base_url=BASE, json={"risk_preset": "low"},
                    headers={"Origin": BASE})
    assert r.status_code == 200
    r = client.post("/api/config", base_url=BASE, json={"risk_preset": "mid"})  # PowerShell-style
    assert r.status_code == 200


def test_stale_instance_lock_is_reclaimed(tmp_path, monkeypatch):
    lock_path = tmp_path / "tomahawk.pid"
    lock_path.write_text("2147483647", encoding="ascii")
    monkeypatch.setattr(desk, "_INSTANCE_LOCK_PATH", lock_path)
    monkeypatch.setattr(desk, "_INSTANCE_LOCK_FD", None)

    desk.acquire_instance_lock()
    try:
        assert lock_path.read_text(encoding="ascii") == str(os.getpid())
    finally:
        desk.release_instance_lock()
    assert not lock_path.exists()


def test_default_bind_is_localhost():
    assert desk.DESK_HOST == "127.0.0.1" or os.environ.get("TOMAHAWK_HOST")


def test_remote_client_requires_auth_token(client, monkeypatch):
    monkeypatch.setenv("TOMAHAWK_AUTH_TOKEN", "test-token")
    r = client.get(
        "/api/health",
        base_url=BASE,
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
    )
    assert r.status_code == 403
    r = client.get(
        "/api/health",
        base_url="https://127.0.0.1:5056",
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
        headers={"X-Tomahawk-Token": "test-token"},
    )
    assert r.status_code == 200


def test_remote_client_requires_https_even_with_token(client, monkeypatch):
    monkeypatch.setenv("TOMAHAWK_AUTH_TOKEN", "test-token")
    r = client.get(
        "/api/health",
        base_url=BASE,
        environ_base={"REMOTE_ADDR": "192.168.1.20"},
        headers={"X-Tomahawk-Token": "test-token"},
    )
    assert r.status_code == 403
    assert r.get_json()["error"] == "https_required"


def test_risk_policy_returns_defensive_preset_copy():
    import risk_policy

    preset = risk_policy.get_preset("low")
    preset["max_position_pct"] = 999
    assert risk_policy.get_preset("low")["max_position_pct"] == 1.0
    assert risk_policy.get_preset("unknown")["max_trades_per_day"] == 6


def test_llm_chat_rate_limit_is_fail_closed(client, monkeypatch):
    monkeypatch.setattr(desk, "_rate_limited", lambda *args, **kwargs: True)
    r = client.post("/api/llm/chat", base_url=BASE, json={"message": "hello"})
    assert r.status_code == 429
    assert "rate limit" in r.get_json()["error"]


def test_live_mode_requires_server_confirmation(client, monkeypatch):
    from broker_fixtures import IDENTITY
    import broker_alpaca
    monkeypatch.setattr(broker_alpaca, 'verify_execution_context', lambda: {'ok':True,'identity':dict(IDENTITY,paper_mode=False)})
    monkeypatch.setattr(broker_alpaca, 'public_status', lambda: {'broker':'alpaca','configured':True,'paper_mode':False})
    monkeypatch.setattr(
        desk,
        "_broker_public_status",
        lambda: {"configured": True, "paper_mode": False},
    )
    r = client.post("/api/config", base_url=BASE, json={"mode": "auto_live"})
    assert r.status_code == 400
    assert desk.load_config().get("mode") != "auto_live"
    r = client.post(
        "/api/config",
        base_url=BASE,
        json={"mode": "auto_live", "live_confirm": "REAL"},
    )
    assert r.status_code == 200
    assert desk.load_config().get("mode") == "auto_live"


def test_paper_auto_live_requires_explicit_confirmation(client):
    r = client.post("/api/config", base_url=BASE, json={"mode": "auto_live"})
    assert r.status_code == 400
    r = client.post(
        "/api/config",
        base_url=BASE,
        json={"mode": "auto_live", "live_confirm": "AUTO_LIVE"},
    )
    assert r.status_code == 200


# ---------------------------------------------------------------- #3 corrupt data

def test_bom_config_loads(isolated_data):
    desk.CONFIG_PATH.write_text(json.dumps({"watchlist": ["ZZZT"]}), encoding="utf-8-sig")
    assert desk.load_config()["watchlist"] == ["ZZZT"]
    assert not desk._CORRUPT_PATHS


def test_corrupt_config_never_overwritten(isolated_data):
    desk.CONFIG_PATH.write_text('{"watchlist": ["KEEP"', encoding="utf-8")
    before = desk.CONFIG_PATH.read_bytes()
    cfg = desk.load_config()
    desk.save_config(cfg)
    assert desk.CONFIG_PATH.read_bytes() == before
    assert list(isolated_data.glob("config.json.corrupt.*.bak"))


def test_corrupt_ledger_blocks_trading(isolated_data):
    desk.LEDGER_PATH.write_text("{not json", encoding="utf-8")
    ledger = desk.load_ledger()
    cfg = dict(desk.load_config(), session_active=True, rth_only=False)
    ok, reason = desk.can_take_trade(cfg, ledger, 100.0)
    assert not ok and "corrupt" in reason.lower()
    assert desk.LEDGER_PATH.read_text(encoding="utf-8") == "{not json"


def test_structurally_invalid_ledger_is_marked_corrupt(isolated_data):
    desk.LEDGER_PATH.write_text("[]", encoding="utf-8")
    ledger = desk.load_ledger()
    assert ledger["positions"] == []
    assert str(desk.LEDGER_PATH.resolve()) in desk._CORRUPT_PATHS
    assert list(isolated_data.glob("ledger.json.corrupt.*.bak"))


def test_nested_invalid_ledger_is_marked_corrupt(isolated_data):
    desk.LEDGER_PATH.write_text(json.dumps({"positions": {}, "fills": [], "daily": {}}), encoding="utf-8")
    ledger = desk.load_ledger()
    assert ledger["positions"] == []
    assert str(desk.LEDGER_PATH.resolve()) in desk._CORRUPT_PATHS


def test_missing_marks_block_new_risk(monkeypatch):
    ledger = desk.load_ledger()
    ledger["positions"] = [{"ticker": "AAPL", "side": "long", "shares": 10, "avg_price": 100}]
    cfg = dict(desk.load_config(), session_active=True, rth_only=False)
    desk.save_config(cfg)
    monkeypatch.setattr(desk, "fetch_last_price", lambda ticker: None)
    ok, reason = desk.can_take_trade(cfg, ledger, 1000, marks={})
    assert not ok and "valuation unavailable" in reason.lower()


# ---------------------------------------------------------------- #9 signal pruning

def test_signal_prune_keeps_pending():
    sigs = [{"id": str(i), "status": "rejected"} for i in range(desk.SIGNALS_KEEP + 50)]
    sigs.append({"id": "old-pending", "status": "pending"})
    desk.save_signals(sigs)
    saved = desk.load_signals()
    assert len(saved) == desk.SIGNALS_KEEP + 1
    assert saved[-1]["id"] == "old-pending"


# ---------------------------------------------------------------- #4 #5 #7 broker honesty

@pytest.fixture
def broker_on(monkeypatch):
    import broker_alpaca
    from broker_fixtures import configure
    # Transport/fill accounting is independent of Fox authorization (covered by test_live_agent).
    cfg = dict(desk.load_config(), session_active=True, mode="live_manual", rth_only=False)
    configure(monkeypatch, cfg)
    desk.save_config(cfg)
    monkeypatch.setattr(broker_alpaca, "get_open_orders", lambda: {"ok": True, "orders": []})
    monkeypatch.setattr(desk, "_broker_is_configured", lambda: True)
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (0.0, 100_000.0, None))
    monkeypatch.setattr(desk, "can_take_trade", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(desk, "_cap_shares_for_broker", lambda sig, cfg, led, **k: (10, 1000.0, None))
    monkeypatch.setattr(desk, "_broker_position_qty", lambda t: (0.0, None))
    paper_calls = []
    monkeypatch.setattr(desk, "paper_fill", lambda *a, **k: paper_calls.append(a) or {"ok": True})
    return paper_calls


def _sig():
    return {"id": "s1", "ticker": "AAPL", "side": "buy", "signal_price": 100.0, "suggested_shares": 10}


def _cfg():
    return dict(desk.load_config(), session_active=True, mode="live_manual")


def test_broker_reject_is_not_booked_as_paper(monkeypatch, broker_on):
    monkeypatch.setattr(desk, "live_broker_place_order",
                        lambda order: {"ok": False, "status": "error", "error": "insufficient buying power"})
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] is False and res["paper_fallback"] is False
    assert broker_on == []  # paper_fill never called


def test_broker_accepted_then_rejected_is_failure(monkeypatch, broker_on):
    import broker_alpaca
    monkeypatch.setattr(desk, "live_broker_place_order",
                        lambda order: {"ok": True, "status": "paper_submitted", "order_id": "o1", "qty": "10"})
    monkeypatch.setattr(broker_alpaca, "wait_for_fill",
                        lambda oid, timeout=0: {"state": "failed", "alpaca_status": "rejected", "filled_qty": 0})
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] is False
    assert desk._broker_trades_today(desk.load_ledger()) == 0


def test_broker_fill_uses_real_price_and_counts(monkeypatch, broker_on):
    import broker_alpaca
    monkeypatch.setattr(desk, "live_broker_place_order",
                        lambda order: {"ok": True, "status": "paper_submitted", "order_id": "o1", "qty": "10"})
    monkeypatch.setattr(broker_alpaca, "wait_for_fill",
                        lambda oid, timeout=0: {"state": "filled", "alpaca_status": "filled",
                                                "filled_qty": 10.0, "filled_avg_price": 101.37})
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] and res["fill"]["price"] == 101.37 and res["fill"]["confirmed"] is True
    assert res["fill"]["price_source"] == "broker_filled"
    assert desk._broker_trades_today(desk.load_ledger()) == 1


def test_unconfirmed_fill_is_flagged(monkeypatch, broker_on):
    import broker_alpaca
    monkeypatch.setattr(broker_alpaca, "reconcile_after_timeout", lambda *a, **k: {"state": "pending", "terminal": False})
    monkeypatch.setattr(desk, "live_broker_place_order",
                        lambda order: {"ok": True, "status": "paper_submitted", "order_id": "o1", "qty": "10"})
    monkeypatch.setattr(broker_alpaca, "wait_for_fill",
                        lambda oid, timeout=0: {"state": "pending", "alpaca_status": "new", "filled_qty": 0})
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] is False and "reconciled fill" in res["error"]


def test_broker_trade_cap_counts_broker_orders(monkeypatch, broker_on):
    cfg = _cfg()
    cap = desk.get_preset(cfg.get("risk_preset"))["max_trades_per_day"]
    ledger = desk.load_ledger()
    ledger["broker_daily"] = {desk._today_str(): {"trades": cap}}
    desk.save_ledger(ledger)
    called = []
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: called.append(order))
    res = desk.execute_gated_broker_or_paper(_sig(), cfg, source="t", via="t")
    assert res["ok"] is False and res.get("gated") and called == []


def test_broker_account_unavailable_fails_closed(monkeypatch, broker_on):
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (None, None, "Broker account unavailable"))
    called = []
    monkeypatch.setattr(desk, "live_broker_place_order", lambda order: called.append(order))
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] is False and called == []


def test_broker_partial_fill_is_reconciled_and_persisted(monkeypatch, broker_on):
    import broker_alpaca

    monkeypatch.setattr(
        desk,
        "live_broker_place_order",
        lambda order: {"ok": True, "status": "paper_submitted", "order_id": "o1", "qty": "10"},
    )
    monkeypatch.setattr(
        broker_alpaca,
        "wait_for_fill",
        lambda oid, timeout=0: {"state": "pending", "alpaca_status": "new", "filled_qty": 0},
    )
    monkeypatch.setattr(
        broker_alpaca,
        "reconcile_after_timeout",
        lambda oid, timeout=0: {
            "state": "partially_filled",
            "alpaca_status": "canceled",
            "filled_qty": 4,
            "filled_avg_price": 101.0,
        },
    )
    res = desk.execute_gated_broker_or_paper(_sig(), _cfg(), source="t", via="t")
    assert res["ok"] and res["fill"]["confirmed"] and res["fill"]["shares"] == 4
    assert desk.load_ledger()["broker_fills"][0]["broker_reconciled"] is True


# ---------------------------------------------------------------- #6 stuck approving

def test_approve_crash_does_not_leave_signal_stuck(monkeypatch, client):
    cfg = desk.load_config()
    cfg["mode"] = "manual"
    desk.save_config(cfg)
    desk.save_signals([{"id": "s1", "ticker": "AAPL", "side": "buy", "status": "pending", "signal_price": 10}])

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(desk, "paper_fill", boom)
    r = client.post("/api/signals/s1/approve", base_url=BASE, json={})
    assert r.status_code == 500
    assert desk.load_signals()[0]["status"] == "rejected"


def test_ledger_reset_preserves_broker_counters(client):
    led = desk.load_ledger()
    led["broker_daily"] = {desk._today_str(): {"trades": 2}}
    desk.save_ledger(led)
    r = client.post("/api/ledger/reset", base_url=BASE, json={})
    assert r.status_code == 200
    assert desk.load_ledger()["broker_daily"][desk._today_str()]["trades"] == 2


def test_equity_mark_reprices_all_open_positions(monkeypatch):
    led = desk.load_ledger()
    led["positions"] = [
        {"ticker": "AAA", "side": "long", "shares": 10, "avg_price": 100},
        {"ticker": "BBB", "side": "long", "shares": 10, "avg_price": 100},
    ]
    monkeypatch.setattr(desk, "fetch_last_price", lambda ticker: {"AAA": 90, "BBB": 80}[ticker])
    assert desk._session_pnl_with_mtm(led) == -300
