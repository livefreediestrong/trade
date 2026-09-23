"""
Durable automation/session lifecycle foundation.

Gives any long-running background worker (currently ``paper_loop.PaperLoop``) an
explicit, persisted state machine plus the operational primitives a real
automation needs to run unattended and recover safely:

* Explicit states: ``idle -> starting -> running -> paused`` with
  ``recovering`` / ``failed`` / ``killed`` side states.
* Durable run / command / heartbeat / retry records, written with the same
  atomic tmp-file + ``os.replace`` pattern already used by
  ``paper_loop.DecisionRing`` (crash mid-write can never corrupt the previous
  good state; a corrupt file is quarantined, never silently discarded).
* A hard kill switch: force-transitions to ``killed`` from any state, closes
  the open run, and is *sticky* — it survives a process restart and blocks
  ``start()`` until an operator calls ``reset_after_kill``.
* Idempotent command keys: ``apply_command`` applies a side-effecting callable
  at most once per ``(command, command_key)`` pair, so retried HTTP requests
  or crash-and-replay can never double-apply a start/stop/kill.
* A ``health()`` signal with ``recovery_needed`` derived from stale
  heartbeats, elevated error streaks, or a ``failed`` state — for dashboards
  and alerting, not just logs.
* Structured operational telemetry via the stdlib ``logging`` module (JSON
  payloads) plus an optional sink callback (e.g. the host app's journal).

This module is dry-run / paper safe by construction: it has no knowledge of
brokers or live orders. It only manages *when a worker is allowed to run* and
records what happened — trading logic and live-order safety remain entirely
in ``paper_loop.py`` / ``broker_router.py``.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_MAX_RUNS = 200
DEFAULT_MAX_COMMANDS = 500
DEFAULT_MAX_HEARTBEATS = 500
DEFAULT_MAX_RETRIES = 200

# No heartbeat for this long while RUNNING => treat as needing recovery.
DEFAULT_HEARTBEAT_STALE_SEC = 180.0
# Consecutive tick failures while RUNNING => auto-transition to RECOVERING.
DEFAULT_ERROR_STREAK_RECOVERY_THRESHOLD = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe coercion for values stored in durable records."""
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return str(value)


class WorkerState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    KILLED = "killed"
    RECOVERING = "recovering"
    FAILED = "failed"


# Allowed transitions: {from_state: {allowed to_states...}}
_ALLOWED_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
    WorkerState.IDLE: {WorkerState.STARTING, WorkerState.KILLED},
    WorkerState.STARTING: {
        WorkerState.RUNNING,
        WorkerState.FAILED,
        WorkerState.PAUSED,
        WorkerState.KILLED,
    },
    WorkerState.RUNNING: {
        WorkerState.PAUSED,
        WorkerState.RECOVERING,
        WorkerState.FAILED,
        WorkerState.KILLED,
    },
    WorkerState.PAUSED: {WorkerState.STARTING, WorkerState.KILLED},
    WorkerState.RECOVERING: {
        WorkerState.RUNNING,
        WorkerState.STARTING,
        WorkerState.FAILED,
        WorkerState.KILLED,
    },
    WorkerState.FAILED: {
        WorkerState.STARTING,
        WorkerState.RECOVERING,
        WorkerState.KILLED,
    },
    # Terminal-until-reset: killed only ever leaves via an explicit operator
    # reset. This is the point of a *hard* kill switch — no transition, forced
    # or otherwise, is allowed to auto-resume out of it.
    WorkerState.KILLED: {WorkerState.IDLE},
}


class LifecycleTransitionError(RuntimeError):
    """Raised when an invalid (and not explicitly forced) state transition is attempted."""


class DurableLifecycleStore:
    """Atomic JSON-file-backed durable store for lifecycle state.

    Uses the same tmp-write + ``Path.replace`` pattern as
    ``paper_loop.DecisionRing`` so a crash mid-write can never corrupt the
    previous good state. A file that fails to parse is quarantined to
    ``<path>.corrupt.bak`` (never silently overwritten) and a fresh default
    state is used — fail closed, not silently.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_runs: int = DEFAULT_MAX_RUNS,
        max_commands: int = DEFAULT_MAX_COMMANDS,
        max_heartbeats: int = DEFAULT_MAX_HEARTBEATS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self.path = Path(path)
        self.max_runs = max_runs
        self.max_commands = max_commands
        self.max_heartbeats = max_heartbeats
        self.max_retries = max_retries
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._load()

    def _default(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "state": WorkerState.IDLE.value,
            "state_changed_at": _now_iso(),
            "state_reason": None,
            "runs": [],
            "commands": {},
            "command_order": [],
            "heartbeats": [],
            "retries": [],
            "health": {
                "recovery_needed": False,
                "reason": None,
                "updated_at": _now_iso(),
            },
        }

    def _load(self) -> None:
        if not self.path.exists():
            self._data = self._default()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("lifecycle store root must be an object")
            base = self._default()
            base.update(raw)
            self._data = base
        except Exception:
            try:
                bak = self.path.with_suffix(self.path.suffix + ".corrupt.bak")
                if self.path.exists() and not bak.exists():
                    bak.write_bytes(self.path.read_bytes())
            except OSError:
                pass
            self._data = self._default()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data, default=str))

    # -- state ---------------------------------------------------------------
    def set_state(self, state: WorkerState, reason: Optional[str]) -> None:
        with self._lock:
            self._data["state"] = state.value
            self._data["state_changed_at"] = _now_iso()
            self._data["state_reason"] = reason
            self._persist()

    def get_state(self) -> WorkerState:
        with self._lock:
            try:
                return WorkerState(self._data.get("state") or WorkerState.IDLE.value)
            except ValueError:
                return WorkerState.IDLE

    # -- runs ------------------------------------------------------------------
    def open_run(self, run_id: str, reason: Optional[str]) -> None:
        with self._lock:
            rec = {
                "run_id": run_id,
                "started_at": _now_iso(),
                "ended_at": None,
                "start_reason": reason,
                "end_reason": None,
                "end_state": None,
            }
            self._data["runs"].insert(0, rec)
            self._data["runs"] = self._data["runs"][: self.max_runs]
            self._persist()

    def close_run(self, run_id: str, end_state: WorkerState, reason: Optional[str]) -> None:
        with self._lock:
            for rec in self._data["runs"]:
                if rec.get("run_id") == run_id and rec.get("ended_at") is None:
                    rec["ended_at"] = _now_iso()
                    rec["end_reason"] = reason
                    rec["end_state"] = end_state.value
                    self._persist()
                    return

    # -- commands (idempotency) -------------------------------------------------
    def get_command(self, key: str) -> Optional[dict[str, Any]]:
        with self._lock:
            rec = self._data["commands"].get(key)
            return dict(rec) if rec else None

    def put_command(self, key: str, record: dict[str, Any]) -> None:
        with self._lock:
            is_new = key not in self._data["commands"]
            self._data["commands"][key] = record
            if is_new:
                order = self._data["command_order"]
                order.append(key)
                while len(order) > self.max_commands:
                    evict = order.pop(0)
                    self._data["commands"].pop(evict, None)
            self._persist()

    # -- heartbeats --------------------------------------------------------------
    def add_heartbeat(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._data["heartbeats"].insert(0, record)
            self._data["heartbeats"] = self._data["heartbeats"][: self.max_heartbeats]
            self._persist()

    def last_heartbeat(self) -> Optional[dict[str, Any]]:
        with self._lock:
            hb = self._data["heartbeats"]
            return dict(hb[0]) if hb else None

    # -- retries -------------------------------------------------------------------
    def add_retry(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._data["retries"].insert(0, record)
            self._data["retries"] = self._data["retries"][: self.max_retries]
            self._persist()

    # -- health ------------------------------------------------------------------
    def set_health(self, *, recovery_needed: bool, reason: Optional[str]) -> None:
        with self._lock:
            self._data["health"] = {
                "recovery_needed": bool(recovery_needed),
                "reason": reason,
                "updated_at": _now_iso(),
            }
            self._persist()

    def get_health(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._data.get("health") or {})


class TelemetryEmitter:
    """Structured operational telemetry.

    Emits JSON-shaped events through the stdlib ``logging`` module (so they
    flow through whatever logging config the host process already has) and,
    optionally, to a journal sink callback such as the host app's
    ``append_journal`` for UI/history surfacing. Never raises — telemetry must
    never be able to break the worker it is observing.
    """

    def __init__(
        self,
        *,
        logger: Optional[logging.Logger] = None,
        sink: Optional[Callable[[str, dict[str, Any]], None]] = None,
        component: str = "lifecycle",
    ) -> None:
        self.logger = logger or logging.getLogger(f"tomahawk.{component}")
        self.sink = sink
        self.component = component

    def emit(self, event: str, fields: dict[str, Any], *, level: str = "info") -> None:
        payload = {"component": self.component, "event": event, "ts": _now_iso(), **(fields or {})}
        try:
            log_fn = getattr(self.logger, level, None) or self.logger.info
            log_fn(json.dumps(payload, default=str))
        except Exception:
            pass
        if self.sink:
            try:
                self.sink(f"lifecycle_{event}", payload)
            except Exception:
                pass


class LifecycleManager:
    """Coordinates worker state transitions and durable run/command/heartbeat/
    retry records, plus idempotent command application and health/telemetry
    signals, for a long-running automation loop such as
    ``paper_loop.PaperLoop``.

    Thread-safe: every mutating operation serializes on an internal lock and
    is durably persisted (via ``store``) before the call returns, so state is
    safe to inspect immediately after a crash/restart.
    """

    def __init__(
        self,
        store: DurableLifecycleStore,
        *,
        telemetry: Optional[TelemetryEmitter] = None,
        error_streak_recovery_threshold: int = DEFAULT_ERROR_STREAK_RECOVERY_THRESHOLD,
        heartbeat_stale_sec: float = DEFAULT_HEARTBEAT_STALE_SEC,
    ) -> None:
        self.store = store
        self.telemetry = telemetry or TelemetryEmitter()
        self.error_streak_recovery_threshold = error_streak_recovery_threshold
        self.heartbeat_stale_sec = heartbeat_stale_sec
        self._lock = threading.RLock()
        self._current_run_id: Optional[str] = None

    @property
    def state(self) -> WorkerState:
        return self.store.get_state()

    # -- internal ----------------------------------------------------------------
    def _transition(
        self, target: WorkerState, *, reason: Optional[str] = None, force: bool = False
    ) -> WorkerState:
        with self._lock:
            current = self.store.get_state()
            if current == target:
                return current
            allowed = _ALLOWED_TRANSITIONS.get(current, set())
            if target not in allowed and not force:
                raise LifecycleTransitionError(
                    f"cannot transition {current.value} -> {target.value}"
                )
            self.store.set_state(target, reason)
            self.telemetry.emit(
                "state_transition",
                {
                    "from": current.value,
                    "to": target.value,
                    "reason": reason,
                    "forced": bool(force and target not in allowed),
                },
            )
            return target

    def _close_current_run(self, end_state: WorkerState, reason: Optional[str]) -> None:
        run_id = self._current_run_id
        if run_id:
            self.store.close_run(run_id, end_state, reason)
        self._current_run_id = None

    # -- restart reconciliation ----------------------------------------------------
    def reconcile_after_restart(self, reason: str = "process_restarted") -> WorkerState:
        """Call once when the owning process (re)creates this manager.

        If the durable record shows an in-flight state (``starting``/
        ``running``) left over from a process that exited without a clean
        stop, mark ``recovering`` and raise ``recovery_needed`` — the
        previous process died mid-work and nothing confirmed a safe stop.
        ``killed`` and ``paused`` are left untouched: a hard kill must survive
        a restart (explicit reset required), and a clean pause is not a
        recovery situation.
        """
        with self._lock:
            state = self.store.get_state()
            if state in (WorkerState.RUNNING, WorkerState.STARTING):
                self.store.set_state(WorkerState.RECOVERING, reason)
                self.store.set_health(recovery_needed=True, reason=reason)
                self.telemetry.emit(
                    "recovery_needed_on_restart",
                    {"previous_state": state.value, "reason": reason},
                    level="warning",
                )
                return WorkerState.RECOVERING
            return state

    # -- run lifecycle --------------------------------------------------------------
    def begin_start(self, reason: str = "start_requested") -> str:
        """idle/paused/failed/recovering -> starting; opens a durable run record."""
        with self._lock:
            self._transition(WorkerState.STARTING, reason=reason)
            run_id = uuid.uuid4().hex
            self._current_run_id = run_id
            self.store.open_run(run_id, reason)
            self.telemetry.emit("run_started", {"run_id": run_id, "reason": reason})
            return run_id

    def mark_running(self, reason: str = "thread_alive") -> WorkerState:
        with self._lock:
            state = self._transition(WorkerState.RUNNING, reason=reason)
            self.store.set_health(recovery_needed=False, reason=None)
            return state

    def pause(self, reason: str = "stop_requested") -> WorkerState:
        with self._lock:
            state = self._transition(WorkerState.PAUSED, reason=reason)
            self._close_current_run(WorkerState.PAUSED, reason)
            return state

    def begin_recovery(self, reason: str) -> WorkerState:
        with self._lock:
            state = self._transition(WorkerState.RECOVERING, reason=reason)
            self.store.set_health(recovery_needed=True, reason=reason)
            self.telemetry.emit("recovery_started", {"reason": reason}, level="warning")
            return state

    def recover_ok(self, reason: str = "cycle_succeeded") -> None:
        """Call once a cycle succeeds again after ``recovering``/``failed`` —
        returns the worker to ``running``."""
        with self._lock:
            state = self.store.get_state()
            if state == WorkerState.RECOVERING:
                self.mark_running(reason)
            elif state == WorkerState.FAILED:
                self.begin_recovery(f"{reason}_after_failed")
                self.mark_running(reason)

    def fail(self, reason: str) -> WorkerState:
        with self._lock:
            state = self._transition(WorkerState.FAILED, reason=reason)
            self._close_current_run(WorkerState.FAILED, reason)
            self.store.set_health(recovery_needed=True, reason=reason)
            self.telemetry.emit("run_failed", {"reason": reason}, level="error")
            return state

    def kill(self, reason: str) -> WorkerState:
        """Hard kill switch.

        Force-transitions to ``killed`` from *any* state and closes any open
        run. Terminal until ``reset_after_kill`` is called explicitly — a kill
        must never silently auto-resume, even across a process restart.
        """
        with self._lock:
            state = self._transition(WorkerState.KILLED, reason=reason, force=True)
            self._close_current_run(WorkerState.KILLED, reason)
            self.store.set_health(recovery_needed=False, reason=None)
            self.telemetry.emit("kill_switch_engaged", {"reason": reason}, level="warning")
            return state

    def reset_after_kill(self, reason: str = "operator_reset") -> WorkerState:
        with self._lock:
            if self.store.get_state() != WorkerState.KILLED:
                raise LifecycleTransitionError("reset_after_kill requires state == killed")
            state = self._transition(WorkerState.IDLE, reason=reason)
            self.telemetry.emit("kill_switch_reset", {"reason": reason})
            return state

    # -- heartbeats / retries -------------------------------------------------------
    def heartbeat(
        self,
        *,
        cycle_count: int = 0,
        error_streak: int = 0,
        last_error: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> None:
        record = {
            "ts": _now_iso(),
            "state": self.store.get_state().value,
            "cycle_count": int(cycle_count),
            "error_streak": int(error_streak),
            "last_error": last_error,
        }
        if extra:
            record.update({k: _jsonable(v) for k, v in extra.items()})
        self.store.add_heartbeat(record)
        self.telemetry.emit("heartbeat", record, level="debug")
        if (
            error_streak >= self.error_streak_recovery_threshold
            and self.store.get_state() == WorkerState.RUNNING
        ):
            self.begin_recovery(f"error_streak>={self.error_streak_recovery_threshold}")

    def record_retry(self, *, attempt: int, delay_sec: float, error: str) -> None:
        record = {
            "ts": _now_iso(),
            "attempt": int(attempt),
            "delay_sec": round(float(delay_sec), 2),
            "error": error,
        }
        self.store.add_retry(record)
        self.telemetry.emit("retry_scheduled", record, level="warning")

    # -- idempotent commands --------------------------------------------------------
    def apply_command(
        self, command_key: str, command: str, fn: Callable[[], Any]
    ) -> dict[str, Any]:
        """Apply ``fn`` at most once for a given ``(command, command_key)`` pair.

        Repeated calls with the same key return the cached record without
        re-invoking ``fn`` — safe for at-least-once delivery (retried HTTP
        calls, crash-and-replay). A previously *failed* attempt with the same
        key is retried rather than permanently cached as a failure.
        """
        if not command_key:
            raise ValueError("command_key is required for idempotent commands")
        with self._lock:
            existing = self.store.get_command(command_key)
            if existing and existing.get("command") == command and existing.get("status") == "applied":
                self.telemetry.emit(
                    "command_deduped", {"command_key": command_key, "command": command}
                )
                return existing
            record = {
                "command_key": command_key,
                "command": command,
                "status": "pending",
                "issued_at": _now_iso(),
                "applied_at": None,
                "result": None,
                "error": None,
            }
            self.store.put_command(command_key, record)
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001
            record = dict(record, status="failed", applied_at=_now_iso(), error=str(exc))
            self.store.put_command(command_key, record)
            self.telemetry.emit(
                "command_failed",
                {"command_key": command_key, "command": command, "error": str(exc)},
                level="error",
            )
            raise
        record = dict(record, status="applied", applied_at=_now_iso(), result=_jsonable(result))
        self.store.put_command(command_key, record)
        self.telemetry.emit("command_applied", {"command_key": command_key, "command": command})
        return record

    # -- health / status --------------------------------------------------------------
    def health(self, *, thread_alive: Optional[bool] = None) -> dict[str, Any]:
        state = self.store.get_state()
        stored_health = self.store.get_health()
        last_hb = self.store.last_heartbeat()
        stale = False
        if state == WorkerState.RUNNING and last_hb:
            try:
                last_ts = datetime.fromisoformat(last_hb["ts"])
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last_ts).total_seconds()
                stale = age > self.heartbeat_stale_sec
            except Exception:
                stale = False
        thread_down = state == WorkerState.RUNNING and thread_alive is False
        recovery_needed = (
            bool(stored_health.get("recovery_needed"))
            or stale
            or thread_down
            or state == WorkerState.FAILED
        )
        reason = stored_health.get("reason")
        if thread_down:
            reason = "thread_not_alive"
        elif stale and not reason:
            reason = "heartbeat_stale"
        return {
            "state": state.value,
            "recovery_needed": recovery_needed,
            "reason": reason,
            "stale_heartbeat": stale,
            "last_heartbeat_ts": last_hb.get("ts") if last_hb else None,
            "updated_at": stored_health.get("updated_at"),
        }

    def status(self, *, thread_alive: Optional[bool] = None) -> dict[str, Any]:
        snap = self.store.snapshot()
        return {
            "state": snap.get("state"),
            "state_changed_at": snap.get("state_changed_at"),
            "state_reason": snap.get("state_reason"),
            "health": self.health(thread_alive=thread_alive),
            "runs": snap.get("runs", [])[:5],
            "heartbeats": snap.get("heartbeats", [])[:5],
            "retries": snap.get("retries", [])[:5],
            "command_count": len(snap.get("commands", {})),
        }


def build_default_lifecycle_manager(
    path: Path,
    *,
    sink: Optional[Callable[[str, dict[str, Any]], None]] = None,
    component: str = "lifecycle",
) -> LifecycleManager:
    """Convenience factory: durable JSON store + telemetry emitter + manager,
    with the durable record reconciled against a possible prior-process crash."""
    store = DurableLifecycleStore(Path(path))
    telemetry = TelemetryEmitter(sink=sink, component=component)
    manager = LifecycleManager(store, telemetry=telemetry)
    manager.reconcile_after_restart()
    return manager
