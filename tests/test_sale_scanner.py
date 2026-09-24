"""Exercise the real scan worker against deterministic histories, never a broker."""
from datetime import datetime, timedelta, timezone
import threading

import pytest
import requests

from sale_scanner import SaleScanner
from research_studio import completed_day

NOW = datetime(2026, 9, 24, 15, tzinfo=timezone.utc)


def listing(symbol, etf=False):
    return {"symbol": symbol, "name": symbol+" company", "etf": etf}


def history(symbol, now):
    return {"symbol": symbol, "as_of": completed_day(now).isoformat(), "retrieved_at": now.isoformat(), "name": symbol,
            "below_typical_pct": 10, "avg_daily_dollar_volume": 20_000_000,
            "return_20_sessions_pct": 5, "completed_session_relative_volume": 1.2}


def scanner(tmp_path, rows=None, fetch=history, clock=lambda: NOW, directory=None, pace=0):
    return SaleScanner(tmp_path/"scan.sqlite3", directory or (lambda: {"symbols": rows or [listing("A")], "updated_at": NOW.isoformat()}),
                       fetch, completed_day, clock, pace)


def finish(scan):
    scan.worker.join(5)
    assert not scan.worker.is_alive(), "Worker did not finish"
    return scan.snapshot()


def test_entire_directory_beyond_manual_cap_and_class_share_mapping(tmp_path):
    calls = []
    def fetch(symbol, now):
        calls.append(symbol)
        return history(symbol, now)
    scan = scanner(tmp_path, [listing(f"S{i}", i%2 == 0) for i in range(105)]+[listing("BRK.B")], fetch)
    scan.start()
    result = finish(scan)
    assert result["total"] == result["checked"] == result["valid"] == 106
    assert len(set(calls)) == 106 and "BRK-B" in calls
    assert result["state"] == "complete" and result["coverage_pct"] == 100
    assert len(result["results"]) == 40
    assert len(scan.snapshot(page=3)["results"]) == 26
    assert scan.snapshot(kind="etf")["matched"] == 53
    assert scan.snapshot(kind="stock", query="BRK-B")["matched"] == 1
    assert scan.snapshot(query="%")["matched"] == 0
    assert scan.snapshot(minimum=11)["matched"] == 0


def test_data_exclusions_and_provider_gaps_not_discount_results(tmp_path):
    def fetch(symbol, now):
        if symbol == "BAD": raise ValueError("Adjusted close missing")
        if symbol == "GAP": raise requests.Timeout()
        row = history(symbol, now)
        if symbol == "ABOVE": row["below_typical_pct"] = -5
        if symbol == "THIN": row["avg_daily_dollar_volume"] = 500
        return row
    scan = scanner(tmp_path, [listing(s) for s in ["BAD", "GAP", "ABOVE", "THIN", "GOOD"]], fetch)
    scan.start(); d = finish(scan)
    assert (d["excluded"], d["failed"], d["valid"], d["matched"]) == (1, 1, 3, 2)
    assert d["state"] == "partial"
    assert scan.snapshot(liquid=True)["matched"] == 1
    assert {r["symbol"] for r in scan.snapshot(issues=True)["results"]} == {"BAD", "GAP"}


def test_pause_inflight_saves_one_result_resume_skips_completed(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(symbol, now):
        calls.append(symbol)
        entered.set(); assert release.wait(3)
        return history(symbol, now)
    scan = scanner(tmp_path, [listing("A"), listing("B")], fetch)
    scan.start(); assert entered.wait(3)
    assert scan.pause()["state"] == "pausing"
    release.set(); d = finish(scan)
    assert d["state"] == "paused" and d["checked"] == 1
    scan.start(resume=True); d = finish(scan)
    assert d["state"] == "complete" and calls == ["A", "B"]


def test_duplicate_start_cannot_launch_duplicate_workers(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def fetch(symbol, now):
        calls.append(symbol); entered.set(); assert release.wait(3)
        return history(symbol, now)
    scan = scanner(tmp_path, fetch=fetch)
    scan.start(); assert entered.wait(3)
    original = scan.worker
    scan.start(); assert scan.worker is original
    release.set(); finish(scan)
    assert calls == ["A"]


def test_rate_limit_pauses_with_cooldown_and_retries_failed_only(tmp_path):
    current, calls = [NOW], []
    def fetch(symbol, now):
        calls.append(symbol)
        if len(calls) == 2:
            response = requests.Response(); response.status_code = 429
            raise requests.HTTPError(response=response)
        return history(symbol, now)
    scan = scanner(tmp_path, [listing("A"), listing("B"), listing("C")], fetch, lambda: current[0])
    scan.start(); d = finish(scan)
    assert d["state"] == "paused" and d["failed"] == 1 and d["pending"] == 1
    with pytest.raises(ValueError, match="cooling down"): scan.start(resume=True)
    current[0] += timedelta(minutes=6)
    scan.start(resume=True); d = finish(scan)
    assert d["state"] == "complete" and calls == ["A", "B", "B", "C"]


def test_repeated_outage_stops_after_three_requests(tmp_path):
    def fetch(*_): raise requests.Timeout()
    scan = scanner(tmp_path, [listing(str(i)) for i in range(20)], fetch)
    scan.start(); d = finish(scan)
    assert d["state"] == "paused" and d["failed"] == 3 and d["pending"] == 17


def test_restarted_app_recovers_checkpoint_without_automatically_fetching(tmp_path):
    scan = scanner(tmp_path)
    scan.start(); finish(scan)
    scan._update(state="running")  # Emulate a process ending mid-pass.
    recovered = scanner(tmp_path, fetch=lambda *_: pytest.fail("Unexpected network work"))
    d = recovered.snapshot()
    assert d["state"] == "paused" and d["valid"] == 1 and recovered.worker is None


def test_new_close_stops_scan_and_prevents_stale_resume(tmp_path):
    current = [NOW]
    def fetch(symbol, now):
        current[0] = NOW.replace(hour=22)
        return history(symbol, now)
    scan = scanner(tmp_path, [listing("A"), listing("B")], fetch, lambda: current[0])
    scan.start(); d = finish(scan)
    assert d["state"] == "stale" and d["stale"] and d["checked"] == 1
    with pytest.raises(ValueError, match="older session"): scan.start(resume=True)


@pytest.mark.parametrize("patch", [{"symbol": "WRONG"}, {"as_of": "2026-09-22"}, {"below_typical_pct": float("nan")}])
def test_wrong_identity_stale_nonfinite_never_rank(tmp_path, patch):
    scan = scanner(tmp_path, fetch=lambda s, n: {**history(s, n), **patch})
    scan.start(); d = finish(scan)
    assert d["excluded"] == 1 and not d["results"]


def test_refresh_failure_preserves_last_pass(tmp_path):
    scan = scanner(tmp_path)
    scan.start(); finish(scan)
    def failed(): raise ValueError("Directory unavailable")
    scan.directory = failed
    scan.start(); d = finish(scan)
    assert d["valid"] == 1 and d["total"] == 1 and "Directory unavailable" in d["message"]


def test_lagging_history_remains_retryable_on_resume(tmp_path):
    from research_studio import RetryableHistoryError
    calls = []
    def fetch(symbol, now):
        calls.append(symbol)
        if len(calls) == 1:
            raise RetryableHistoryError('History is behind the latest completed session; no sale ranking calculated')
        return history(symbol, now)
    scan = scanner(tmp_path, fetch=fetch)
    scan.start(); first = finish(scan)
    assert first['state'] == 'partial' and first['failed'] == 1 and first['excluded'] == 0
    scan.start(resume=True); second = finish(scan)
    assert second['state'] == 'complete' and second['valid'] == 1 and calls == ['A', 'A']


@pytest.mark.parametrize("filters", [{"minimum": float("nan")}, {"minimum": -1}, {"kind": "otc"}, {"page": 0}])
def test_invalid_filters_rejected(tmp_path, filters):
    with pytest.raises(ValueError): scanner(tmp_path).snapshot(**filters)


def test_shared_views_and_context_reject_future_and_old_sessions(tmp_path):
    from types import SimpleNamespace
    import market_discovery
    def fetch(symbol, now):
        row = history(symbol, now)
        if symbol == "UP": row.update(below_typical_pct=-1, return_20_sessions_pct=15, completed_session_relative_volume=3)
        if symbol == "DOWN": row.update(return_20_sessions_pct=-20)
        return row
    scan = SaleScanner(tmp_path/"sale_scan.sqlite3", lambda: {"symbols": [listing("UP"), listing("DOWN")]}, fetch, completed_day, lambda: NOW, 0)
    scan.start(); finish(scan)
    assert [r["symbol"] for r in scan.snapshot()["results"]] == ["DOWN"]
    assert [r["symbol"] for r in scan.snapshot(lens="momentum")["results"]] == ["UP"]
    assert scan.snapshot(lens="active")["results"][0]["symbol"] == "UP"
    assert scan.snapshot(lens="all")["matched"] == 2
    desk = SimpleNamespace(DATA_DIR=tmp_path)
    assert market_discovery.symbols(desk, NOW) == ["UP", "DOWN"]
    assert market_discovery.symbols(desk, NOW-timedelta(minutes=1)) == []
    assert market_discovery.symbols(desk, NOW.replace(hour=22)) == []


def test_shared_scanner_routes_validate_and_never_call_broker(tmp_path, monkeypatch):
    import app as desk
    import research_studio as studio
    import market_universe
    import socket
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(socket.socket, "connect", lambda *_: pytest.fail("Network forbidden"))
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *_: pytest.fail("Broker writes forbidden"))
    monkeypatch.setattr(studio, "fetch_sale", history)
    monkeypatch.setattr(market_universe, "refresh", lambda _: None)
    monkeypatch.setattr(market_universe, "load", lambda _: {"symbols": [listing("A")], "updated_at": datetime.now(timezone.utc).isoformat()})
    client = desk.app.test_client()
    base = "http://127.0.0.1:5056"
    assert client.get("/api/markets/scanner", base_url=base).json["state"] == "idle"
    for query in ["page=no", "minimum=nan", "lens=unknown", "kind=futures"]:
        assert client.get("/api/markets/scanner?"+query, base_url=base).status_code == 400
    assert client.post("/api/markets/scanner", json={"action": "arm"}, base_url=base).status_code == 400
    started = client.post("/api/markets/scanner", json={"action": "start"}, base_url=base)
    assert started.status_code == 200
    # Stop the actual isolated worker before test teardown restores shared objects.
    client.post("/api/markets/scanner", json={"action": "pause"}, base_url=base)
    for worker in threading.enumerate():
        if worker.name == "sale-research": worker.join(3)
    result = client.get("/api/research/sale/market", base_url=base).json
    assert result["total"] == 1 and result["state"] in ("paused", "complete")
