"""Failure and recovery paths with isolated storage and no external orders."""
import copy
import threading
from datetime import datetime, timedelta

import pytest

import app as desk
import live_agent as agent
import moss_paper
import research_companion as companion
import screener_logic
import companion_news
import backtest
import buzz_sources
import paper_loop
from test_execution_repairs import execution
from test_live_agent import live
from test_moss_workday import isolated, NOW, freeze_fill_clock, moss_signal
from test_sale_scanner import scanner


def fail(*args, **kwargs):
    raise RuntimeError("Injected failure")


def test_notebook_worker_launch_failure_can_retry(isolated, monkeypatch):
    service = companion.Companion(desk)
    clock = [1000.]
    monkeypatch.setattr(companion.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(companion.threading.Thread, "start", fail)
    assert service.run() is False
    assert not service.busy and "start" in service.last_error.lower()
    assert service.run() is False  # keep the retry cooldown
    started = []
    monkeypatch.setattr(companion.threading.Thread, "start", lambda self: started.append(self.name))
    clock[0] += 301
    assert service.run() is True and started == ["moss-research"]


def test_optional_scheduled_failure_does_not_skip_daily_notebook(isolated, monkeypatch):
    service = companion.Companion(desk)
    jobs, calls = {}, []
    class Thread:
        def __init__(self, target, name, **kwargs): jobs[name] = target
        def start(self): pass
    class Stop:
        def __init__(self): self.ticks = iter([False, True])
        def wait(self, _): return next(self.ticks)
        def is_set(self): return False
    monkeypatch.setattr(companion.threading, "Thread", Thread)
    monkeypatch.setattr(service.news, "refresh", fail)
    monkeypatch.setattr(service.review, "tick", lambda: calls.append("review"))
    monkeypatch.setattr(service, "run", lambda **kw: calls.append("notebook"))
    service.start()
    service.stop = Stop()
    jobs["moss-daily-schedule"]()
    assert calls == ["review", "notebook"]
    assert "news" in service.status()["scheduler_errors"]
    monkeypatch.setattr(service.news, "refresh", lambda: None)
    service.stop = Stop()
    jobs["moss-daily-schedule"]()
    assert service.status()["scheduler_errors"] == {}


def paper_engine(monkeypatch):
    engine = moss_paper.PaperWorkday(desk)
    monkeypatch.setattr(desk, "check_decision_outcomes", lambda: None)
    monkeypatch.setattr(engine, "close_due", lambda *a: None)
    monkeypatch.setattr(engine, "close_report", lambda *a: None)
    return engine


def test_paper_worker_launch_failure_recovers_after_reserved_interval(isolated, monkeypatch):
    engine = paper_engine(monkeypatch)
    clock = [1000.]
    monkeypatch.setattr(moss_paper.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(moss_paper.threading.Thread, "start", fail)
    engine.tick()
    assert not engine.busy and "start" in engine.last_error.lower()
    reserved = engine.load()["last_cycle_at"]
    started = []
    monkeypatch.setattr(moss_paper.threading.Thread, "start", lambda self: started.append(self.name))
    engine.tick()
    assert not started and engine.load()["last_cycle_at"] == reserved
    interval = engine.status()["settings"]["interval_sec"]
    monkeypatch.setattr(moss_paper, "now_utc", lambda: NOW + timedelta(seconds=interval + 1))
    clock[0] += interval + 1
    engine.tick()
    assert started == ["moss-paper-research"] and engine.busy


@pytest.mark.parametrize("broken", ["outcomes", "exits", "report"])
def test_paper_housekeeping_failure_keeps_other_jobs_running_and_blocks_new_cycles(isolated, monkeypatch, broken):
    engine = moss_paper.PaperWorkday(desk)
    calls = []
    def job(name):
        calls.append(name)
        if name == broken: fail()
    monkeypatch.setattr(desk, "check_decision_outcomes", lambda: job("outcomes"))
    monkeypatch.setattr(engine, "close_due", lambda *a: job("exits"))
    monkeypatch.setattr(engine, "close_report", lambda *a: job("report"))
    monkeypatch.setattr(moss_paper.threading.Thread, "start", lambda self: pytest.fail("No new entries on maintenance failure"))
    engine.tick()
    assert calls == ["outcomes", "exits", "report"]
    assert engine.status()["error"] and engine.status()["phase"] == "error"
    assert not engine.busy
    # A later healthy maintenance pass clears the error and can schedule again.
    broken = None
    engine.last_housekeeping = 0
    started = []
    monkeypatch.setattr(moss_paper.threading.Thread, "start", lambda self: started.append(self.name))
    engine.tick()
    assert engine.housekeeping_errors == {} and started == ["moss-paper-research"]


def test_paper_status_distinguishes_waiting_from_working(isolated, monkeypatch):
    engine = paper_engine(monkeypatch)
    engine.save({"days": {}, "cursor": 0, "last_cycle_at": NOW.isoformat()})
    status = engine.status()
    assert status["phase"] == "waiting_for_next_cycle"
    assert datetime.fromisoformat(status["next_at"]) > NOW
    engine.busy = True
    assert engine.status()["phase"] == "researching"


@pytest.mark.parametrize("result", [{"error": "Provider unavailable"}, None])
def test_real_scanner_failure_consumes_full_agent_interval(live, monkeypatch, result):
    # Restore the actual generator; the agent fixture normally supplies a signal.
    monkeypatch.setattr(desk, "generate_scan_signal", REAL_GENERATOR)
    def analyze(symbol):
        if result is None: raise TimeoutError("Provider unavailable")
        return copy.deepcopy(result)
    monkeypatch.setattr(screener_logic, "analyze_ticker", analyze)
    live.service.tick()
    raw = live.service.load()
    assert raw["days"][live.service.key(desk.load_config())]["research"] == 1
    assert (datetime.fromisoformat(raw["next_at"]) - agent.now_utc()).total_seconds() > agent.SCREEN_GAP_SEC
    assert live.service.phase == "blocked" and "data" in live.service.message.lower()
    assert not live.sent


REAL_GENERATOR = desk.generate_scan_signal


def test_live_worker_launch_failure_releases_cycle_lock(live, monkeypatch):
    monkeypatch.setattr(agent.threading.Thread, "start", fail)
    assert live.service.schedule_tick() is False
    assert not live.service.cycle_lock.locked() and live.service.phase == "error"
    assert not live.calls and not live.sent


def test_reconciliation_continues_during_one_slow_live_research_worker(live, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, checks = [], []
    def scan(*args, **kw):
        calls.append(kw["ticker"])
        entered.set()
        assert release.wait(5), "Test must release the worker"
        return None
    class Stop:
        count = 0
        def wait(self, **kwargs):
            self.count += 1
            if self.count > 1: assert entered.wait(2)
            return self.count > 3
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    monkeypatch.setattr(desk, "_bg_stop", Stop())
    monkeypatch.setattr(desk, "_reconcile_pending_broker_orders", lambda: checks.append(len(calls)))
    monkeypatch.setattr(desk.market_radar, "maybe_refresh", lambda *a: None)
    try:
        desk._bg_loop()
        assert checks == [0, 1, 1]
        assert calls == ["TEST"] and live.service.cycle_lock.locked()
        # A pause while research is in flight must still discard its result.
        cfg = desk.load_config(); cfg["live_agent"]["enabled"] = False; desk.save_config(cfg)
    finally:
        release.set()
        worker = getattr(live.service, "_worker", None)
        if worker: worker.join(3)
    assert not live.service.cycle_lock.locked() and not live.sent
    assert live.service.phase == "discarded"


def test_error_journal_failure_does_not_strand_agent_worker(live, monkeypatch):
    monkeypatch.setattr(live.service, "_tick", fail)
    monkeypatch.setattr(desk, "append_journal", fail)
    live.service.tick()
    assert not live.service.cycle_lock.locked() and live.service.phase == "error"


def test_headline_worker_launch_failure_can_retry(isolated, monkeypatch):
    service = companion_news.HeadlineDesk(desk)
    clock = [1000.]
    monkeypatch.setattr(companion_news.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(companion_news.threading.Thread, "start", fail)
    assert service.refresh() is False
    assert not service.busy and service.snapshot()["error"]
    started = []
    monkeypatch.setattr(companion_news.threading.Thread, "start", lambda self: started.append(self.name))
    assert service.refresh() is False
    clock[0] += companion_news.TTL + 1
    assert service.refresh() is True and started == ["companion-headlines"]


def test_backtest_worker_launch_failure_does_not_claim_running(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest, "_state", {"running": False})
    monkeypatch.setattr(backtest.threading.Thread, "start", fail)
    ok, message = backtest.start(["TEST"], tmp_path / "result.json")
    assert not ok and "start" in message.lower() and backtest.status()["running"] is False
    monkeypatch.setattr(backtest.threading.Thread, "start", lambda self: None)
    assert backtest.start(["TEST"], tmp_path / "result.json")[0] is True


def test_buzz_worker_launch_failure_does_not_claim_refreshing(monkeypatch):
    monkeypatch.setattr(buzz_sources, "_refresh_inflight", False)
    monkeypatch.setattr(buzz_sources.threading.Thread, "start", fail)
    assert buzz_sources.kick_background_refresh(["TEST"]) is False
    assert not buzz_sources._refresh_inflight
    monkeypatch.setattr(buzz_sources.threading.Thread, "start", lambda self: None)
    assert buzz_sources.kick_background_refresh(["TEST"]) is True


def test_optional_state_worker_failure_keeps_snapshot_available(monkeypatch):
    monkeypatch.setattr(desk, "_STATE_AUX", {"refreshing": False})
    monkeypatch.setattr(desk.threading.Thread, "start", fail)
    assert desk._state_aux_snapshot({}, ["TEST"], None) == {}
    assert desk._STATE_AUX["refreshing"] is False
    monkeypatch.setattr(desk.threading.Thread, "start", lambda self: None)
    desk._state_aux_snapshot({}, ["TEST"], None)
    assert desk._STATE_AUX["refreshing"] is True


@pytest.mark.parametrize("verdict", ["WATCH", "AVOID"])
def test_actual_completed_nonpass_screen_retains_fast_rotation(live, monkeypatch, verdict):
    monkeypatch.setattr(desk, "generate_scan_signal", REAL_GENERATOR)
    monkeypatch.setattr(desk, "market_regime_status", lambda cfg: {})
    monkeypatch.setattr(screener_logic, "analyze_ticker", lambda symbol: {
        "ticker": symbol, "verdict": verdict, "verdict_text": "Waiting for evidence"})
    monkeypatch.setattr(desk, "_enrich_signal_with_llm", lambda *a, **kw: pytest.fail("No paid work for WATCH/AVOID"))
    live.service.tick()
    raw = live.service.load()
    assert raw["days"][live.service.key(desk.load_config())]["research"] == 0
    assert 0 < (datetime.fromisoformat(raw["next_at"]) - agent.now_utc()).total_seconds() <= agent.SCREEN_GAP_SEC
    assert live.service.phase == "no_setup" and not live.sent


def test_background_survives_failure_of_its_error_journal(monkeypatch):
    ticks, calls = iter([False, False, True]), []
    class Stop:
        def wait(self, **kwargs): return next(ticks)
    def reconcile():
        calls.append("reconcile")
        fail()
    monkeypatch.setattr(desk, "_bg_stop", Stop())
    monkeypatch.setattr(desk, "_reconcile_pending_broker_orders", reconcile)
    monkeypatch.setattr(desk, "append_journal", fail)
    desk._bg_loop()
    assert calls == ["reconcile", "reconcile"]


def test_new_pending_order_during_async_research_still_blocks_submission(live, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def scan(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return live.generate(*args, **kwargs)
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    try:
        assert live.service.schedule_tick() is True
        assert entered.wait(2)
        # A manual order became unresolved while the provider was busy.
        with desk._lock:
            ledger = desk.load_ledger()
            ledger["pending_broker_orders"] = [{"order_id": "unresolved-fixture"}]
            desk.save_ledger(ledger)
        assert live.service.schedule_tick() is False
    finally:
        release.set()
        if live.service._worker: live.service._worker.join(3)
    assert not live.service.cycle_lock.locked() and not live.sent
    assert desk.load_ledger()["pending_broker_orders"]


def test_directory_scanner_launch_failure_is_visible_and_retryable(tmp_path, monkeypatch):
    service = scanner(tmp_path)
    original = threading.Thread.start
    monkeypatch.setattr(threading.Thread, "start", fail)
    state = service.start()
    assert state["state"] == "error" and "start" in state["message"].lower()
    monkeypatch.setattr(threading.Thread, "start", original)
    service.start()
    service.worker.join(3)
    assert service.snapshot()["state"] == "complete"


def test_paper_loop_launch_failure_does_not_report_running(tmp_path, monkeypatch):
    service = paper_loop.PaperLoop(decisions=paper_loop.DecisionRing(tmp_path / "decisions.json"), get_deps=lambda: {})
    monkeypatch.setattr(threading.Thread, "start", fail)
    with pytest.raises(RuntimeError): service.start()
    assert not service._running_flag and service._last_error


def test_due_paper_holding_closes_despite_outcome_checker_failure(isolated, monkeypatch):
    freeze_fill_clock(monkeypatch)
    cfg = desk.load_config()
    opened = desk.paper_fill(moss_signal(cfg), desk.paper_research_config(cfg), "auto_paper")
    assert opened["ok"]
    ledger = desk.load_ledger()
    ledger["positions"][0]["moss_exit_at"] = (NOW - timedelta(seconds=1)).isoformat()
    desk.save_ledger(ledger)
    desk.save_config(dict(cfg, paper_research_enabled=False, paper_auto_approve=False))
    monkeypatch.setattr(desk, "check_decision_outcomes", fail)
    engine = moss_paper.PaperWorkday(desk)
    engine.tick()
    ledger = desk.load_ledger()
    assert not ledger["positions"] and len(ledger["fills"]) == 2
    assert not ledger.get("broker_fills") and not ledger.get("pending_broker_orders")
    assert engine.status()["error"] and not engine.busy
