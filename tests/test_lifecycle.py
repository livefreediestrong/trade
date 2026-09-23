"""Tests for the durable automation/session lifecycle foundation (lifecycle.py)
and its integration into paper_loop.PaperLoop.

Run:  .venv\\Scripts\\python -m pytest tests\\test_lifecycle.py -q
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import lifecycle as lc  # noqa: E402
import paper_loop  # noqa: E402


# --------------------------------------------------------------------------- helpers
def _manager(tmp_path: Path, **kwargs) -> lc.LifecycleManager:
    store = lc.DurableLifecycleStore(tmp_path / "lifecycle.json", **kwargs)
    return lc.LifecycleManager(store)


def _stub_paper_loop(tmp_path: Path, *, session_active: bool = False) -> paper_loop.PaperLoop:
    ring = paper_loop.DecisionRing(tmp_path / "decisions.json")

    def get_deps():
        return {
            "load_config": lambda: {"session_active": session_active, "loop_enabled": False},
            "append_journal": lambda *a, **k: None,
            "load_ledger": lambda: {},
            "daily_target_progress": lambda cfg, ledger: {},
            "get_preset": lambda name: {},
            "execute_loop_decision": lambda *a, **k: None,
            "analyze_ticker": lambda t: {},
            "trade_thesis": lambda *a, **k: {},
        }

    return paper_loop.PaperLoop(decisions=ring, get_deps=get_deps)


# --------------------------------------------------------------------------- state machine
def test_basic_start_run_pause_cycle(tmp_path):
    mgr = _manager(tmp_path)
    assert mgr.state == lc.WorkerState.IDLE
    run_id = mgr.begin_start("start_requested")
    assert mgr.state == lc.WorkerState.STARTING
    assert run_id
    mgr.mark_running("thread_alive")
    assert mgr.state == lc.WorkerState.RUNNING
    mgr.pause("stop_requested")
    assert mgr.state == lc.WorkerState.PAUSED
    # Resuming re-enters STARTING and opens a *new* run record.
    run_id2 = mgr.begin_start("resume")
    assert run_id2 != run_id
    assert mgr.state == lc.WorkerState.STARTING


def test_invalid_transition_raises(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(lc.LifecycleTransitionError):
        # idle -> running directly is not a legal transition
        mgr._transition(lc.WorkerState.RUNNING)


def test_recovering_flow_marks_recovery_needed_then_clears(tmp_path):
    mgr = _manager(tmp_path)
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.begin_recovery("transient_error")
    assert mgr.state == lc.WorkerState.RECOVERING
    assert mgr.health()["recovery_needed"] is True
    mgr.recover_ok("cycle_succeeded")
    assert mgr.state == lc.WorkerState.RUNNING
    assert mgr.health()["recovery_needed"] is False


def test_failed_flow_and_recover_ok_from_failed(tmp_path):
    mgr = _manager(tmp_path)
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.fail("boom")
    assert mgr.state == lc.WorkerState.FAILED
    assert mgr.health()["recovery_needed"] is True
    mgr.recover_ok("cycle_succeeded")
    assert mgr.state == lc.WorkerState.RUNNING


# --------------------------------------------------------------------------- kill switch
def test_kill_switch_is_sticky_and_requires_explicit_reset(tmp_path):
    mgr = _manager(tmp_path)
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.kill("operator_kill")
    assert mgr.state == lc.WorkerState.KILLED
    # killed does not accept any transition except the explicit reset
    with pytest.raises(lc.LifecycleTransitionError):
        mgr.begin_start("attempted_restart")
    mgr.reset_after_kill("operator_reset")
    assert mgr.state == lc.WorkerState.IDLE
    # Now a normal start is legal again.
    mgr.begin_start("start_again")
    assert mgr.state == lc.WorkerState.STARTING


def test_kill_switch_survives_reload_from_disk(tmp_path):
    path = tmp_path / "lifecycle.json"
    mgr = lc.LifecycleManager(lc.DurableLifecycleStore(path))
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.kill("loss_limit_breached")

    # A brand-new process/manager pointed at the same durable file must see the
    # kill as still in effect (this is the whole point of a *hard* kill switch).
    reloaded = lc.build_default_lifecycle_manager(path)
    assert reloaded.state == lc.WorkerState.KILLED


def test_reset_after_kill_requires_killed_state(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(lc.LifecycleTransitionError):
        mgr.reset_after_kill()


# --------------------------------------------------------------------------- idempotent commands
def test_apply_command_runs_exactly_once_for_repeated_key(tmp_path):
    mgr = _manager(tmp_path)
    calls = {"n": 0}

    def do_work():
        calls["n"] += 1
        return {"applied": True}

    r1 = mgr.apply_command("cmd-123", "start", do_work)
    r2 = mgr.apply_command("cmd-123", "start", do_work)
    assert calls["n"] == 1
    assert r1["result"] == r2["result"] == {"applied": True}
    assert r2["status"] == "applied"


def test_apply_command_retries_after_a_failed_attempt(tmp_path):
    mgr = _manager(tmp_path)
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("transient")
        return "ok"

    with pytest.raises(RuntimeError):
        mgr.apply_command("cmd-456", "kill", flaky)
    record = mgr.apply_command("cmd-456", "kill", flaky)
    assert attempts["n"] == 2
    assert record["status"] == "applied"
    assert record["result"] == "ok"


def test_apply_command_requires_a_key(tmp_path):
    mgr = _manager(tmp_path)
    with pytest.raises(ValueError):
        mgr.apply_command("", "start", lambda: True)


# --------------------------------------------------------------------------- heartbeats / retries / health
def test_heartbeat_and_retry_are_durably_recorded(tmp_path):
    mgr = _manager(tmp_path)
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.heartbeat(cycle_count=1, error_streak=0)
    mgr.record_retry(attempt=1, delay_sec=5.0, error="boom")
    status = mgr.status()
    assert status["heartbeats"][0]["cycle_count"] == 1
    assert status["retries"][0]["error"] == "boom"


def test_error_streak_autotransitions_to_recovering(tmp_path):
    mgr = _manager(tmp_path, )
    mgr.begin_start("start")
    mgr.mark_running("alive")
    for i in range(1, mgr.error_streak_recovery_threshold + 1):
        mgr.heartbeat(cycle_count=i, error_streak=i, last_error="e")
    assert mgr.state == lc.WorkerState.RECOVERING


def test_health_flags_recovery_needed_on_stale_heartbeat(tmp_path):
    mgr = _manager(tmp_path)
    mgr.heartbeat_stale_sec = 0.01
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.heartbeat(cycle_count=1)
    time.sleep(0.05)
    health = mgr.health()
    assert health["stale_heartbeat"] is True
    assert health["recovery_needed"] is True


def test_health_flags_recovery_needed_when_thread_reported_dead(tmp_path):
    mgr = _manager(tmp_path)
    mgr.begin_start("start")
    mgr.mark_running("alive")
    health = mgr.health(thread_alive=False)
    assert health["recovery_needed"] is True
    assert health["reason"] == "thread_not_alive"


# --------------------------------------------------------------------------- durability
def test_corrupt_store_file_is_quarantined_not_dropped_silently(tmp_path):
    path = tmp_path / "lifecycle.json"
    path.write_text("{not valid json", encoding="utf-8")
    store = lc.DurableLifecycleStore(path)
    assert store.get_state() == lc.WorkerState.IDLE
    backup = path.with_suffix(path.suffix + ".corrupt.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "{not valid json"


def test_restart_reconciliation_flags_recovery_needed_for_stale_running_state(tmp_path):
    path = tmp_path / "lifecycle.json"
    mgr = lc.LifecycleManager(lc.DurableLifecycleStore(path))
    mgr.begin_start("start")
    mgr.mark_running("alive")
    # Simulate the process dying without a clean stop: a fresh manager loads the
    # same file and must not assume the old "running" claim is still true.
    reloaded = lc.build_default_lifecycle_manager(path)
    assert reloaded.state == lc.WorkerState.RECOVERING
    assert reloaded.health()["recovery_needed"] is True


def test_restart_reconciliation_preserves_paused_and_killed(tmp_path):
    paused_path = tmp_path / "paused.json"
    mgr = lc.LifecycleManager(lc.DurableLifecycleStore(paused_path))
    mgr.begin_start("start")
    mgr.mark_running("alive")
    mgr.pause("stop_requested")
    reloaded = lc.build_default_lifecycle_manager(paused_path)
    assert reloaded.state == lc.WorkerState.PAUSED

    killed_path = tmp_path / "killed.json"
    mgr2 = lc.LifecycleManager(lc.DurableLifecycleStore(killed_path))
    mgr2.begin_start("start")
    mgr2.mark_running("alive")
    mgr2.kill("bad_loss")
    reloaded2 = lc.build_default_lifecycle_manager(killed_path)
    assert reloaded2.state == lc.WorkerState.KILLED


# --------------------------------------------------------------------------- PaperLoop integration
def test_paper_loop_hard_kill_cancels_pending_intents_and_blocks_restart(tmp_path):
    loop = _stub_paper_loop(tmp_path)
    loop._set_pending_intent("AAPL", {"side": "buy"})
    loop._set_pending_intent("MSFT", {"side": "sell"})

    result = loop.hard_kill(reason="max_loss_breached")

    assert set(result["cancelled_intents"]) == {"AAPL", "MSFT"}
    assert loop._pending_intents == {}
    assert loop.lifecycle.state == lc.WorkerState.KILLED

    # Refuses to (re)start while killed.
    assert loop.start() is False
    assert loop.lifecycle.state == lc.WorkerState.KILLED

    loop.reset_kill()
    assert loop.lifecycle.state == lc.WorkerState.IDLE
    assert loop.start() is True
    # Give the background thread a moment to reach RUNNING, then clean up.
    for _ in range(50):
        if loop.lifecycle.state == lc.WorkerState.RUNNING:
            break
        time.sleep(0.02)
    assert loop.lifecycle.state == lc.WorkerState.RUNNING
    loop.stop()
    for _ in range(50):
        if loop.lifecycle.state != lc.WorkerState.RUNNING:
            break
        time.sleep(0.02)


def test_paper_loop_hard_kill_is_idempotent_via_command_key(tmp_path):
    loop = _stub_paper_loop(tmp_path)
    loop._set_pending_intent("AAPL", {"side": "buy"})

    r1 = loop.hard_kill(reason="loss", command_key="kill-abc")
    # A second call with the same command_key must not re-cancel / re-run the
    # kill body; the (already empty) result is replayed verbatim.
    r2 = loop.hard_kill(reason="loss", command_key="kill-abc")
    assert r1 == r2


def test_paper_loop_status_exposes_lifecycle(tmp_path):
    loop = _stub_paper_loop(tmp_path)
    status = loop.status({"session_active": False, "loop_enabled": False})
    assert "lifecycle" in status
    assert status["lifecycle"]["state"] == lc.WorkerState.IDLE.value
    assert "recovery_needed" in status["lifecycle"]["health"]
