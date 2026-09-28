"""IB Gateway adapter with selected-account validation and durable order identity."""
from __future__ import annotations

import asyncio
import atexit
import copy
import functools
import itertools
import json
import math
import os
import queue
import random
import re
import threading
import time
from concurrent.futures import Future
from collections import deque
from contextlib import contextmanager
from datetime import datetime, time as dtime, timezone
from typing import Any
from pathlib import Path
from zoneinfo import ZoneInfo

# A single owner thread keeps asyncio and the Gateway socket together. All reads
# and order operations serialize on that thread and retain the same client ID.
_API_LOCK = threading.RLock()
_VERIFIED: dict[str, Any] = {}
_DEAD = {"cancelled", "canceled", "apicancelled", "inactive"}
_TASKS = queue.PriorityQueue()
_TASK_SEQUENCE = itertools.count()
_WORKER = None
_WORKER_LOCK = threading.Lock()
_STOP = threading.Event()
_QUIESCING = threading.Event()
_CLOSE_FUTURE = None
_CLIENT = None
_CLIENT_SETTINGS = None
_PNL = {}
_PNL_UPDATED = {}
_ACCOUNT_READY = {}
_ACCOUNT_EQUITY = {}
_API_PULSE = {}
_RESYNC_REQUIRED = False
_SERVER_UNAVAILABLE = False
_CONNECTION = {"connected": False, "error": None}
_RECONNECT_AFTER = 0.0
_SOFT_RECONNECT_AFTER = 0.0
_ACCOUNT_UNSUBSCRIBED = False
_ACCOUNT_RESUBSCRIBE_AFTER = 0.0
_FAILED_SETTINGS = None
# Watchdog for Daily P&L / soft reconnect. Never invents risk_ready.
_PNL_WATCH = {
    "ui_status": None,  # e.g. "Recovering Daily P&L..."
    "last_error_code": None,
    "soft_reconnect_count": 0,
    "last_soft_reconnect_at": 0.0,
    "last_scheduled_refresh_at": 0.0,
    "pending_soft_reconnect": False,
    "pending_reason": None,
    "pending_resubscribe": False,
}
_SOFT_RECONNECT_BACKOFF_BASE = 60.0
_SOFT_RECONNECT_BACKOFF_MAX = 600.0
_PNL_SILENT_RETRY_SEC = 180.0
_PNL_FIRST_CALLBACK_WAIT_SEC = 8.0
_PNL_SOFT_RECONNECT_WAIT_SEC = 180.0
_PNL_TRACE = deque(maxlen=120)
_PNL_TRACE_LOCK = threading.Lock()
_PNL_TRACE_ERROR = None


def _pnl_trace(event, **fields):
    """Bounded lifecycle evidence; never store account IDs, values or wire text."""
    global _PNL_TRACE_ERROR
    row = {"at": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    with _PNL_TRACE_LOCK:
        _PNL_TRACE.append(row)
        try:
            folder = Path(os.environ.get("TOMAHAWK_DATA_DIR") or Path(__file__).parent / "data") / "diagnostics"
            folder.mkdir(exist_ok=True)
            path = folder / "ibkr-pnl.jsonl"
            if path.exists() and path.stat().st_size > 1_000_000:
                os.replace(path, folder / "ibkr-pnl.previous.jsonl")
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, allow_nan=False) + "\n")
            _PNL_TRACE_ERROR = None
        except (OSError, ValueError):
            # Diagnostics must not take over broker state or order handling.
            _PNL_TRACE_ERROR = "P&L lifecycle trace could not be persisted"


def _disconnected():
    _pnl_trace("api_disconnected")
    _VERIFIED.clear()
    _PNL.clear()
    _PNL_UPDATED.clear()
    _ACCOUNT_READY.clear()
    _ACCOUNT_EQUITY.clear()
    _API_PULSE.clear()
    _CONNECTION["connected"] = False


def _pnl_day():
    # The desk's risk day, not a claim about IBKR's instrument reset schedule.
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _account_value_update(row):
    if row.tag == "NetLiquidation" and getattr(row, "currency", "") == "USD" and not getattr(row, "modelCode", ""):
        # Invalidate a risk snapshot if callbacks change equity during preflight.
        for key in list(_ACCOUNT_EQUITY):
            if key[1] == row.account:
                try:
                    _ACCOUNT_EQUITY[key] = _number(row.value)
                except (TypeError, ValueError):
                    _ACCOUNT_EQUITY[key] = None
    if row.tag != "AccountReady" or getattr(row, "modelCode", ""):
        return
    ready = str(row.value).lower() == "true"
    if _ACCOUNT_READY.get(row.account) != ready:
        _pnl_trace("account_ready", ready=ready)
    _ACCOUNT_READY[row.account] = ready
    if not ready:
        _PNL_UPDATED.pop(row.account, None)
        _forget_account_downloads(row.account)


def _api_responsive(ib):
    """A separate Gateway round trip; never manufacture a P&L callback."""
    if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
        return False
    if (_API_PULSE.get("client") == id(ib)
            and time.monotonic() - _API_PULSE.get("checked_at", _API_PULSE.get("at", 0)) <= 15):
        return _API_PULSE.get("responsive", True) and not _API_PULSE.get("clock_error")
    try:
        sent = datetime.now(timezone.utc)
        started = time.monotonic()
        server_time = ib.reqCurrentTime()
        if server_time is None:
            raise ValueError("Gateway returned no current-time response")
        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
            return False
        elapsed = time.monotonic()-started
        skew = None
        if isinstance(server_time, datetime) and server_time.tzinfo is not None:
            # reqCurrentTime is second precision. Exclude the entire measured
            # round-trip plus one second before identifying clock drift.
            skew = max(0.0, abs((server_time-sent).total_seconds())-elapsed-1.0)
        checked = time.monotonic()
        _API_PULSE.update(client=id(ib), at=checked, checked_at=checked, responsive=True, error_type=None,
                          clock_skew_lower_bound_sec=skew,
                          clock_error="Local clock differs from broker time by more than 5 seconds" if skew is not None and skew > 5 else None)
        return not _API_PULSE.get("clock_error")
    except Exception as exc:
        _API_PULSE.clear()
        # A broken probe must not stall every dashboard read with another
        # synchronous timeout. It remains unready throughout this short hold.
        _API_PULSE.update(client=id(ib), checked_at=time.monotonic(), responsive=False,
                          error_type=type(exc).__name__)
        return False


def _pnl_update(pnl):
    # Only the active account-wide subscription can freshen the risk value.
    # A late callback from a canceled request (or a model-specific request) is
    # not evidence that the replacement subscription is healthy.
    if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED:
        _pnl_trace("callback_ignored", reason="connection_recovery")
        return
    for (_, account), subscription in _PNL.items():
        if (subscription["value"] is pnl and not getattr(pnl, "modelCode", "")
                and getattr(pnl, "account", account) == account and _ACCOUNT_READY.get(account) is not False):
            _PNL_UPDATED[account] = time.monotonic()
            subscription["callbacks"] = subscription.get("callbacks", 0) + 1
            subscription["day"] = _pnl_day()
            now = time.monotonic()
            try:
                _number(pnl.dailyPnL)
                numeric = True
            except (ValueError, TypeError):
                numeric = False
            if subscription["callbacks"] == 1 or now - subscription.get("trace_at", 0) >= 30:
                _pnl_trace("callback", callbacks=subscription["callbacks"], day=subscription["day"], numeric=numeric)
                subscription["trace_at"] = now
            # An unset callback is received data, but not successful recovery.
            # Keep the accumulated cooldown and pending retry until usable P&L.
            if numeric:
                _PNL_WATCH.update(ui_status=None, soft_reconnect_count=0, pending_soft_reconnect=False,
                                  pending_reason=None, last_error_code=None,
                                  last_scheduled_refresh_at=now)
            return
    _pnl_trace("callback_ignored", reason="inactive_subscription_or_account_reset")


def _server_error(req_id, code, message, *args):
    global _RESYNC_REQUIRED, _SERVER_UNAVAILABLE, _ACCOUNT_UNSUBSCRIBED
    if code in (1100, 1101, 1102, 2100, 2110):
        _pnl_trace("server_event", code=code)
    if code in (1100, 2110):
        _forget_account_downloads()
        _SERVER_UNAVAILABLE = True
        _VERIFIED.clear()
        _PNL_UPDATED.clear()
        _API_PULSE.clear()
        _PNL_WATCH.update(last_error_code=code, ui_status="Recovering Daily P&L...")
        _CONNECTION.update(connected=False, error="IBKR server connection lost; waiting for recovery")
    elif code == 1101:
        _forget_account_downloads()
        _SERVER_UNAVAILABLE = False
        _RESYNC_REQUIRED = True
        _VERIFIED.clear()
        _PNL_UPDATED.clear()
        _API_PULSE.clear()
        _PNL_WATCH.update(last_error_code=1101, ui_status="Recovering Daily P&L...")
    elif code == 2100:
        _forget_account_downloads()
        # "API client has been unsubscribed from account data."
        # Prefer in-socket account+PnL re-subscribe; soft reconnect only if that fails.
        # Never invent risk_ready; never restart Gateway / touch 2FA here.
        _ACCOUNT_UNSUBSCRIBED = True
        _PNL_UPDATED.clear()
        _API_PULSE.clear()
        _PNL_WATCH.update(
            last_error_code=2100,
            ui_status="Recovering Daily P&L...",
            pending_soft_reconnect=False,
            pending_reason="error_2100_unsubscribed_account_data",
            pending_resubscribe=True,
        )
    elif code == 1102:
        # IBKR retained its subscriptions. Reopening the socket here discarded
        # a valid P&L response in the exported Gateway trace. Keep the current
        # subscription and its original timestamp; recovery is not a P&L tick.
        # Do not erase a pending 1101 resync if notifications arrive together.
        was_unavailable = _SERVER_UNAVAILABLE
        _SERVER_UNAVAILABLE = False
        connected = bool(_CLIENT is not None and _CLIENT.isConnected())
        _CONNECTION.update(connected=connected, error=None)
        if was_unavailable and connected and not _RESYNC_REQUIRED and not _ACCOUNT_UNSUBSCRIBED:
            # The original request can be hours old when upstream returns.
            # Give retained subscriptions a full callback window from this
            # restoration, without changing their actual request/P&L timestamps
            # or accepting any value delivered while upstream was unavailable.
            # Duplicate 1102 notices must not postpone recovery indefinitely.
            now = time.monotonic()
            retained = 0
            for (client_id, _), subscription in _PNL.items():
                if client_id == id(_CLIENT):
                    subscription["restored_at"] = now
                    retained += 1
            if retained:
                _pnl_trace("subscriptions_retained", count=retained,
                           callback_wait_seconds=_PNL_SILENT_RETRY_SEC)


_WORKER_ACTIVITY = {"operation": None, "started": None, "completed": 0}


def worker_snapshot():
    current = dict(_WORKER_ACTIVITY)
    age = max(0, time.monotonic()-current["started"]) if current["started"] is not None else 0
    return {"alive": bool(_WORKER and _WORKER.is_alive()), "operation": current["operation"],
            "active_seconds": round(age, 3), "queued": _TASKS.qsize(), "completed": current["completed"],
            "stalled": age > 180, "closing": _QUIESCING.is_set(),
            "recovery": "One API owner only. A slow active call cannot be preempted safely; do not create a duplicate connection or retry an ambiguous order."}


def _api_worker():
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        while not _STOP.is_set():
            try:
                _, _, fn, args, kwargs, future = _TASKS.get(timeout=0.1)
            except queue.Empty:
                pass
            else:
                if future.set_running_or_notify_cancel():
                    _WORKER_ACTIVITY.update(operation=fn.__name__, started=time.monotonic())
                    try:
                        future.set_result(fn(*args, **kwargs))
                    except Exception as exc:
                        future.set_exception(exc)
                    finally:
                        _WORKER_ACTIVITY.update(operation=None, started=None, completed=_WORKER_ACTIVITY["completed"]+1)
            # Pump subscriptions even between HTTP requests.
            if _CLIENT is not None:
                try:
                    _CLIENT.sleep(0.05)
                except Exception as exc:
                    _CONNECTION.update(connected=False, error=friendly_connection_error(str(exc))[:240])
                    _VERIFIED.clear()
    finally:
        try:
            if _CLIENT is not None:
                _CLIENT.disconnect()
        except Exception as exc:
            _pnl_trace("worker_disconnect_failed", error_type=type(exc).__name__)
        finally:
            asyncio.get_event_loop().close()


def _task_priority(name, args, kwargs):
    if name == "_poll" and (kwargs.get("cancel") or (len(args) > 3 and args[3])):
        return 0
    if name in ("place_from_desk_order", "find_order_by_signal", "_poll"):
        return 1
    if name in ("get_account", "get_positions", "get_open_orders"):
        return 2
    return 3


_READ_OPERATIONS = frozenset({"get_account", "get_positions", "get_option_positions",
    "get_position_exposure", "get_open_orders", "get_safety_snapshot", "stock_quote",
    "option_chain", "option_quotes", "verify_execution_context", "execution_history", "account_window_pnl"})
READ_CALL_TIMEOUT_SEC = 30.
MAX_READ_QUEUE = 32


def _on_api_thread(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        global _WORKER
        if threading.current_thread() is _WORKER:
            return fn(*args, **kwargs)
        read_only = fn.__name__ in _READ_OPERATIONS
        with _WORKER_LOCK:
            if _QUIESCING.is_set():
                raise ConnectionError("Broker connection is closing for a desk restart; request was not started")
            if _WORKER is None or not _WORKER.is_alive():
                _STOP.clear()
                _WORKER = threading.Thread(target=_api_worker, name="ibkr-api", daemon=True)
                _WORKER.start()
            started = _WORKER_ACTIVITY.get("started")
            if read_only and (_TASKS.qsize() >= MAX_READ_QUEUE or
                    (started is not None and time.monotonic() - started > READ_CALL_TIMEOUT_SEC)):
                raise ConnectionError("Broker is busy or not responding; account data is unavailable until it recovers")
            future = Future()
            _TASKS.put((_task_priority(fn.__name__, args, kwargs), next(_TASK_SEQUENCE), fn, args, kwargs, future))
        # Never retry a possibly submitted order when the HTTP caller times out.
        if not read_only:
            return future.result()
        try:
            return future.result(timeout=READ_CALL_TIMEOUT_SEC)
        except TimeoutError as exc:
            # Cancel only a queued read. A running call keeps its single owner;
            # no replacement thread or second IBKR connection is created.
            future.cancel()
            raise ConnectionError("Broker read timed out; waiting for the existing connection to recover") from exc
    return call


def _close_for_restart():
    """Runs only on the existing API owner after its active operation finishes."""
    global _CLIENT, _CLIENT_SETTINGS
    client = _CLIENT
    canceled = []
    if client is not None:
        if client.isConnected():
            canceled = _cancel_all_pnl(client)
            # Even if the flush/disconnect fails, canceled stream values must
            # not authorize risk when the still-running desk resumes reads.
            _PNL.clear()
            _PNL_UPDATED.clear()
            # Allow the data-cancellation bytes to leave before closing. This
            # is not a server acknowledgement and never cancels an order.
            client.sleep(0.25)
        client.disconnect()
        if client.isConnected():
            raise ConnectionError("Broker socket did not close")
    _CLIENT, _CLIENT_SETTINGS = None, None
    _disconnected()
    rejected = 0
    while True:
        try:
            _, _, _, _, _, future = _TASKS.get_nowait()
        except queue.Empty:
            break
        if future.set_running_or_notify_cancel():
            future.set_exception(ConnectionError("Broker connection is closing; queued request was not started"))
            rejected += 1
    _STOP.set()
    _pnl_trace("restart_connection_closed", data_subscriptions_canceled=len(canceled), queued_requests_rejected=rejected)
    return True


# Extra wait for a cleanup that already owns the worker when the caller's deadline passes.
RESTART_CLEANUP_GRACE_SEC = 5.


def disconnect_for_restart(timeout=10.):
    """Bounded owner-thread cleanup; no new connection, Gateway stop or order cancel.

    An operation already running keeps ownership until it returns. If a queued
    cleanup times out it is canceled, so it cannot disconnect the desk later.
    Once cleanup starts, new work stays blocked until restart (or cleanup fails).
    """
    global _CLOSE_FUTURE
    with _WORKER_LOCK:
        if _CLOSE_FUTURE is None:
            if _WORKER is None or not _WORKER.is_alive():
                if _CLIENT is not None:
                    _pnl_trace("restart_close_refused", reason="connection_owner_unavailable")
                    return False
                _QUIESCING.set()
                return True
            _QUIESCING.set()
            _CLOSE_FUTURE = Future()
            _TASKS.put((-1, next(_TASK_SEQUENCE), _close_for_restart, (), {}, _CLOSE_FUTURE))
        future = _CLOSE_FUTURE
    try:
        return future.result(timeout=timeout) is True
    except TimeoutError:
        with _WORKER_LOCK:
            # A TimeoutError raised *inside* cleanup differs from our caller
            # giving up while it is still running. The former may be retried.
            finished = future.done() and not future.cancelled()
            released = (finished or future.cancel()) and _CLOSE_FUTURE is future
            if released:
                _CLOSE_FUTURE = None
                _QUIESCING.clear()
        if not released and not finished and not future.cancelled():
            # Cleanup already owns the worker: no order operation can be active,
            # and it will close the socket whatever the caller does. Returning
            # False here would leave a running desk with a closed connection, so
            # let it finish briefly and allow the restart to proceed.
            try:
                future.result(timeout=RESTART_CLEANUP_GRACE_SEC)
            except Exception:  # noqa: BLE001 - the process is exiting either way
                pass
            _pnl_trace("restart_close_slow", completed=future.done())
            return True
        _pnl_trace("restart_close_failed" if finished else "restart_close_timeout", cleanup_running=future.running())
        return False
    except Exception as exc:
        with _WORKER_LOCK:
            if _CLOSE_FUTURE is future:
                _CLOSE_FUTURE = None
                _QUIESCING.clear()
        _pnl_trace("restart_close_failed", error_type=type(exc).__name__)
        return False


def _shutdown():
    _STOP.set()
    if _WORKER is not None and threading.current_thread() is not _WORKER:
        _WORKER.join(timeout=2)


atexit.register(_shutdown)


def _ib():
    global _CLIENT, _CLIENT_SETTINGS, _RESYNC_REQUIRED, _SERVER_UNAVAILABLE, _RECONNECT_AFTER, _FAILED_SETTINGS
    if _SERVER_UNAVAILABLE and _CLIENT is not None and _CLIENT.isConnected():
        raise ConnectionError("IBKR server connection unavailable")
    settings = _settings()
    if settings == _FAILED_SETTINGS and time.monotonic() < _RECONNECT_AFTER:
        raise ConnectionError(_CONNECTION.get("error") or "IBKR reconnect pending")
    if _CLIENT is not None and _CLIENT_SETTINGS == settings and _CLIENT.isConnected() and not _RESYNC_REQUIRED:
        return _CLIENT
    if _CLIENT is not None:
        _CLIENT.disconnect()
    _disconnected()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    from ib_async import IB
    client = IB()
    _preserve_commission_pnl(client)
    _observe_account_download(client)
    client.RequestTimeout = 5
    client.disconnectedEvent += _disconnected
    if hasattr(client, "pnlEvent"):
        client.pnlEvent += _pnl_update
        client.errorEvent += _server_error
        client.accountValueEvent += _account_value_update
        if hasattr(client, "accountSummaryEvent"):
            client.accountSummaryEvent += _account_value_update
    try:
        client.connect(os.environ.get("IB_GATEWAY_HOST", "127.0.0.1"),
                       int(os.environ.get("IB_GATEWAY_PORT", "4002")),
                       clientId=int(os.environ.get("IB_CLIENT_ID", "37")), timeout=5)
        _CLIENT, _CLIENT_SETTINGS = client, settings
        _RESYNC_REQUIRED = False
        _SERVER_UNAVAILABLE = False
        _RECONNECT_AFTER, _FAILED_SETTINGS = 0.0, None
        _CONNECTION.update(connected=True, error=None)
        _pnl_trace("api_connected")
        _note_gateway_api_up()
        return client
    except Exception as exc:
        client.disconnect()
        message = str(exc) or _CONNECTION.get("error") or "IBKR account synchronization timed out"
        low = (message or "").lower()
        if "326" in (message or "") or "already in use" in low or "duplicate clientid" in low.replace(" ", ""):
            cid = int(os.environ.get("IB_CLIENT_ID", "37"))
            message = (
                f"IBKR clientId {cid} is already in use (Error 326). "
                "Another desk/process holds this id — stop the other connection "
                "or set IB_CLIENT_ID to a free id. Gateway was left alone."
            )
        message = friendly_connection_error(message)
        _CONNECTION.update(connected=False, error=message[:240])
        # A dashboard refresh asks for several broker snapshots. One Gateway
        # outage must not queue a fresh five-second connection for each one.
        _RECONNECT_AFTER, _FAILED_SETTINGS = time.monotonic() + 5, settings
        raise ConnectionError(message) from exc


@contextmanager
def _session():
    with _API_LOCK:
        ib = None
        try:
            ib = _ib()
            yield ib
        except Exception:
            _VERIFIED.clear()
            raise


def _live() -> bool:
    return os.environ.get("IBKR_LIVE", "false").strip().lower() in ("1", "true", "yes", "on")


def is_configured() -> bool:
    return os.environ.get("BROKER_PROVIDER", "alpaca").strip().lower() == "ibkr"


def friendly_connection_error(message: str) -> str:
    """Plain words for socket failures (e.g. "[WinError 1225] The remote computer refused
    the network connection"); other messages are returned unchanged."""
    text = str(message or "")
    low = text.lower()
    endpoint = _settings()["endpoint"]
    if any(k in low for k in ("1225", "10061", "refused", "errno 111", "connectionrefused")):
        return (f"IB Gateway is not accepting connections on {endpoint}. Start IB Gateway and sign in, "
                "or check its API port under Configure > Settings > API.")
    if any(k in low for k in ("timed out", "timeout", "10060")):
        return (f"IB Gateway did not answer on {endpoint} in time. It may still be starting or waiting "
                "for sign-in; if it stays like this, check that its API is enabled.")
    return text


def _settings() -> dict[str, Any]:
    return {"endpoint": f"{os.environ.get('IB_GATEWAY_HOST', '127.0.0.1')}:{os.environ.get('IB_GATEWAY_PORT', '4002')}",
            "paper_mode": not _live(), "selected_account": os.environ.get("IBKR_ACCOUNT", "").strip(),
            "client_id": int(os.environ.get("IB_CLIENT_ID", "37"))}


def _identity(ib) -> dict[str, Any]:
    settings = _settings()
    accounts = list(ib.managedAccounts() or [])
    account = settings["selected_account"]
    if not account:
        if len(accounts) != 1:
            raise ValueError("Set IBKR_ACCOUNT to select exactly one managed account")
        account = accounts[0]
    if account not in accounts:
        raise ValueError("IBKR_ACCOUNT is not available on this Gateway")
    if account.startswith("DU") and account[2:].isdigit():
        paper = True
    elif account.startswith("U") and account[1:].isdigit():
        paper = False
    else:
        raise ValueError("Unsupported IBKR account type; cannot verify paper/live mode")
    if paper != settings["paper_mode"]:
        raise ValueError("IBKR_LIVE disagrees with the connected account")
    port = int(os.environ.get("IB_GATEWAY_PORT", "4002"))
    if (port in (4001, 7496) and paper) or (port in (4002, 7497) and not paper):
        raise ValueError("IB Gateway port disagrees with the selected paper/live account")
    identity = {"broker": "ibkr", "account_id": account, "endpoint": settings["endpoint"],
                "paper_mode": paper, "client_id": settings["client_id"]}
    _VERIFIED.update(settings=settings, identity=identity)
    return identity


@_on_api_thread
def verify_execution_context() -> dict[str, Any]:
    try:
        with _session() as ib:
            return {"ok": True, "identity": _identity(ib)}
    except Exception as exc:
        _VERIFIED.clear()
        return {"ok": False, "error": friendly_connection_error(str(exc))[:240]}


def paper_mode() -> bool | None:
    if _VERIFIED.get("settings") == _settings():
        return _VERIFIED["identity"]["paper_mode"]
    return None


def public_status() -> dict[str, Any]:
    pm = paper_mode()
    label = "IBKR mode unverified" if pm is None else ("IBKR PAPER" if pm else "IBKR LIVE")
    return {"broker": "ibkr", "configured": is_configured(), "paper_mode": pm,
            "connected": bool(_CONNECTION["connected"]), "connection_error": _CONNECTION.get("error"),
            "endpoint": _settings()["endpoint"], "status": "unverified" if pm is None else ("paper" if pm else "live"),
            "connected_label": label, "ui_badge": label,
            "masthead": "BROKER UNVERIFIED" if pm is None else ("PAPER ONLY" if pm else "LIVE ENDPOINT"),
            "masthead_title": label}


def _number(value) -> float:
    number = float(value)
    if not math.isfinite(number) or abs(number) >= 1e100:
        raise ValueError("IBKR returned an unavailable numeric value")
    return number


def _summary_amount(rows, account, tag):
    """Read one selected-account USD amount; zero and unknown are different."""
    if not isinstance(rows, (list, tuple)):
        return None, "unreadable"
    matches = [r for r in rows if getattr(r, "account", None) == account
               and getattr(r, "tag", None) == tag]
    selected = [r for r in matches if getattr(r, "currency", None) == "USD"]
    if not selected:
        return None, "currency_unverified" if matches else "missing"
    numbers = []
    for row in selected:
        try:
            value = getattr(row, "value", None)
            if isinstance(value, bool):
                raise ValueError("Boolean account amount")
            numbers.append(_number(value))
        except (TypeError, ValueError, OverflowError):
            return None, "unavailable"
    if any(value != numbers[0] for value in numbers[1:]):
        return None, "conflicting"
    return numbers[0], "reported"


def _account_balances(rows, account):
    """Normalize current cache contents, without inventing a broker value time."""
    if not isinstance(rows, (list, tuple)):
        raise ValueError("IBKR account summary is unreadable")
    currencies = {getattr(r, "currency", None) for r in rows
                  if getattr(r, "account", None) == account and getattr(r, "tag", None) == "NetLiquidation"}
    if currencies != {"USD"}:
        raise ValueError("IBKR desk requires a USD base-currency account")
    values, statuses = {}, {}
    for name, tag in (("equity", "NetLiquidation"), ("cash", "TotalCashValue"),
                      ("settled_cash", "SettledCash"), ("buying_power", "BuyingPower"),
                      ("available_funds", "AvailableFunds"), ("excess_liquidity", "ExcessLiquidity")):
        values[name], statuses[name] = _summary_amount(rows, account, tag)
    if values["equity"] is None or values["equity"] <= 0:
        raise ValueError("IBKR equity must be positive and unambiguous")
    values["funds_observation"] = {
        "source": "IBKR account-summary cache", "currency": "USD",
        "read_at": datetime.now(timezone.utc).isoformat(), "broker_value_time": None,
        "field_status": statuses,
        "note": "Read time is not the broker's value-update time. Cash, settled cash, buying power and available funds are separate balances. They are not added together. These base-currency figures do not establish per-currency settlement, reserved-order cash, instrument permission or a full margin calculation.",
    }
    return values


def _preserve_commission_pnl(client):
    """Capture availability before this client's ib_async wrapper replaces UNSET with zero."""
    wrapper = getattr(client, "wrapper", None)
    if wrapper is None or hasattr(wrapper, "_tomahawk_realized_pnl"):
        return
    raw_values = wrapper._tomahawk_realized_pnl = {}
    original = wrapper.commissionReport

    def commission_report(report):
        try:
            raw_values[report.execId] = _number(report.realizedPNL)
        except (TypeError, ValueError):
            raw_values[report.execId] = None
        return original(report)

    # Instance-only adaptation: no global library/class patch or installed-file change.
    wrapper.commissionReport = commission_report


def _observe_account_download(client):
    """Remember real accountDownloadEnd callbacks on this connection only."""
    wrapper = getattr(client, "wrapper", None)
    original = getattr(wrapper, "accountDownloadEnd", None)
    if not callable(original) or hasattr(wrapper, "_tomahawk_account_downloads"):
        return
    downloads = wrapper._tomahawk_account_downloads = {}

    def account_download_end(account):
        result = original(account)  # Preserve ib_async's waiting-future completion.
        downloads[account] = time.monotonic()
        _pnl_trace("account_download_completed")  # No account IDs or balances.
        return result

    wrapper.accountDownloadEnd = account_download_end
    client.disconnectedEvent += downloads.clear


def _forget_account_downloads(account=None):
    downloads = getattr(getattr(_CLIENT, "wrapper", None), "_tomahawk_account_downloads", {})
    if account is None:
        downloads.clear()
    else:
        downloads.pop(account, None)


@_on_api_thread
def execution_history(expected_identity=None) -> dict[str, Any]:
    """Read execution/commission reports; never submit, modify or cancel orders."""
    try:
        from ib_async import ExecutionFilter
        with _session() as ib:
            identity = _identity(ib)
            if expected_identity is not None and identity != expected_identity:
                raise ValueError("Execution history account changed")
            fills = ib.reqExecutions(ExecutionFilter(acctCode=identity["account_id"]))
            ib.sleep(.2)  # permit commission callbacks to join the returned fills
            result, excluded = [], 0
            def optional(value):
                try:
                    return _number(value)
                except (TypeError, ValueError):
                    return None
            for fill in fills:
                ex, contract = fill.execution, fill.contract
                if ex.acctNumber != identity["account_id"]:
                    continue
                stamp = getattr(ex, "time", None)
                qty, price = optional(ex.shares), optional(ex.price)
                revision = re.fullmatch(r"(.+)\.(\d+)", str(ex.execId))
                voided = qty == 0 and revision is not None and int(revision[2]) > 1
                if not ex.execId or not stamp or stamp.tzinfo is None or qty is None or qty < 0 or (not voided and (qty == 0 or not price or price <= 0)) or ex.side not in ("BOT", "SLD"):
                    excluded += 1
                    continue
                commission = getattr(fill, "commissionReport", None)
                matched = commission and commission.execId == ex.execId
                raw_pnl = getattr(getattr(ib, "wrapper", None), "_tomahawk_realized_pnl", {})
                realized = (raw_pnl[ex.execId] if ex.execId in raw_pnl else optional(commission.realizedPNL)) if matched else None
                # A pre-adaptation cached numeric zero has ambiguous provenance.
                if matched and hasattr(ib, "wrapper") and ex.execId not in raw_pnl and realized == 0:
                    realized = None
                result.append({"execution_id": ex.execId,"account_id": identity["account_id"],
                    "paper_mode": identity["paper_mode"], "ts": stamp.astimezone(timezone.utc).isoformat(),
                    "ticker": contract.symbol,"con_id":contract.conId,"asset_type":contract.secType,
                    "currency":contract.currency,"side":"buy" if ex.side == "BOT" else "sell",
                    "expiry":getattr(contract,"lastTradeDateOrContractMonth",None),
                    "strike":optional(getattr(contract,"strike",None)), "right":getattr(contract,"right",None),
                    "multiplier":getattr(contract,"multiplier",None), "local_symbol":getattr(contract,"localSymbol",None),
                    "shares":qty,"price":price or 0.0,"exchange":ex.exchange,"voided":voided,
                    "cumulative_shares":optional(getattr(ex,"cumQty",None)),
                    "pending_price_revision":bool(getattr(ex,"pendingPriceRevision",False)),
                    "order_id":ex.orderId,"client_id":ex.clientId,"permanent_order_id":ex.permId,
                    "order_ref":ex.orderRef,"commission":optional(commission.commission) if matched else None,
                    "commission_currency":commission.currency if matched else None,
                    "broker_realized_pnl":realized,
                    "broker_realized_pnl_reported":realized is not None if matched else None,
                    "source":"IBKR execution report","verified":True})
            if _identity(ib) != identity:
                raise ValueError("Execution history account changed during the read")
            if expected_identity is not None:
                _stop_context(ib, expected_identity)  # Same paper connection after all blocking calls.
            return {"ok":True,"identity":identity,"executions":result,"excluded":excluded,
                    "coverage":"Only executions exposed by this Gateway session/history window; this is not a complete account statement."}
    except Exception as exc:
        return {"ok":False,"error":f"Execution journal unavailable: {type(exc).__name__}","executions":[]}


@_on_api_thread
def stock_quote(symbol: str) -> dict[str, Any]:
    """Short-lived IBKR stock quote; execution guards require real-time data."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
        return {"ok": False, "error": "Invalid US stock/ETF symbol", "fresh": False}
    try:
        from ib_async import Stock
        with _session() as ib:
            identity = _identity(ib)
            contract = Stock(symbol, "SMART", "USD")
            if not ib.qualifyContracts(contract) or not contract.conId:
                return {"ok": False, "error": "Underlying stock could not be qualified",
                        "symbol": symbol, "fresh": False, "identity": identity}
            # Type 3 permits display-only delayed fallback. IBKR returns type 1
            # when entitled; execution guards reject types 2/3/4 before orders.
            try:
                ib.reqMarketDataType(3)
            except Exception:
                pass
            ticker = ib.reqMktData(contract, "", False, False)
            observed = {}
            def capture(updated):
                _capture_stock_ticks(updated, observed)
            updates = getattr(ticker, "updateEvent", None)
            if updates is not None:
                updates += capture
            try:
                _capture_stock_ticks(ticker, observed)
                for _ in range(25):
                    ib.sleep(0.15)
                    _capture_stock_ticks(ticker, observed)
                    if "bid" in observed and "ask" in observed:
                        break
                quote = _stock_tick_quote(ticker, observed)
                price, stamp = quote.get("price"), quote.get("market_time")
                if price is None or stamp is None:
                    return {"ok": False, "error": "No IBKR trade/quote tick yet", "symbol": symbol,
                            "fresh": False, "identity": identity, "source": "IBKR mkt data"}
                return {
                    "ok": True, "symbol": symbol, **quote,
                    "identity": identity, "con_id": int(contract.conId),
                }
            finally:
                if updates is not None:
                    updates -= capture
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
    except Exception as exc:
        return {"ok": False, "error": (str(exc)[:180] if isinstance(exc, ValueError)
                else f"Stock quote unavailable: {type(exc).__name__}"), "fresh": False, "symbol": symbol}


def _safe_num(v):
    try:
        return _number(v)
    except (ValueError, TypeError):
        return None


def _capture_stock_ticks(ticker, observed):
    """Ignore close, volume and cached ticker fields when establishing freshness."""
    names = {1: "bid", 2: "ask", 4: "last", 66: "bid", 67: "ask", 68: "last"}
    for tick in list(getattr(ticker, "ticks", None) or []):
        name = names.get(getattr(tick, "tickType", None))
        stamp = getattr(tick, "time", None)
        value = _safe_num(getattr(tick, "price", None))
        if name and isinstance(stamp, datetime) and stamp.tzinfo and value is not None and value > 0:
            if name not in observed or stamp >= observed[name][1]:
                observed[name] = (value, stamp)


def _stock_tick_quote(ticker, observed, now=None):
    now = now or datetime.now(timezone.utc)
    kind = getattr(ticker, "marketDataType", None)
    row = {"market_data_type": kind, "delayed": kind in (3, 4), "frozen": kind in (2, 4),
           "source": {1: "IBKR live mkt data", 2: "IBKR frozen mkt data", 3: "IBKR delayed mkt data",
                      4: "IBKR delayed frozen mkt data"}.get(kind, "IBKR unknown mkt data"),
           "time_kind": "local_receipt", "exchange_time": None, "received_at": now.isoformat(),
           "bid_size": _safe_num(getattr(ticker, "bidSize", None)), "ask_size": _safe_num(getattr(ticker, "askSize", None))}
    for name in ("bid", "ask", "last"):
        row[name] = observed.get(name, (None, None))[0]
        row[name + "_time"] = observed[name][1].isoformat() if name in observed else None
    if row["bid"] and row["ask"] and row["bid"] <= row["ask"]:
        price, stamp = (row["bid"]+row["ask"])/2, min(observed["bid"][1], observed["ask"][1])
    else:
        price, stamp = observed.get("last", (None, None))
    age = (now-stamp).total_seconds() if stamp else None
    row.update(price=price, market_time=stamp.isoformat() if stamp else None, age_sec=round(age, 1) if age is not None else None,
               fresh=kind in (1, 3) and age is not None and 0 <= age <= 15)
    return row


def _stock_execution_error(quote, order, identity, con_id):
    if not quote.get("ok") or quote.get("identity") != identity or quote.get("con_id") != con_id:
        return "Final stock quote or its account/contract identity is unavailable"
    # Owner decision (2026-09-28): new stock risk needs real-time bid/ask.
    # Reducing orders skip this check in place_from_desk_order.
    if quote.get("delayed") or quote.get("market_data_type") in (3, 4):
        return "Final stock quote is delayed; real-time bid and ask are required for broker orders"
    if not quote.get("fresh") or quote.get("frozen") or quote.get("market_data_type") != 1:
        return "Final stock quote is stale, frozen or unverified"
    for name in ("bid", "ask"):
        try:
            stamp = datetime.fromisoformat(quote[name+"_time"])
            if stamp.tzinfo is None or not 0 <= (datetime.now(timezone.utc)-stamp).total_seconds() <= 15:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            return "Final stock bid/ask receipt is missing or older than 15 seconds"
    bid, ask = _safe_num(quote.get("bid")), _safe_num(quote.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return "Final stock market is one-sided or crossed"
    if (ask-bid)/((ask+bid)/2)*10000 > 50:
        return "Final stock spread exceeds the 50 bps execution ceiling"
    size = _safe_num(quote.get("ask_size" if order.get("side") == "buy" else "bid_size"))
    if order.get("type", "market") == "market" and (size is None or size < float(order.get("shares") or 0)):
        return "Market order exceeds verified displayed size; use a reviewed limit order"
    return None



@_on_api_thread
def option_chain(symbol):
    """Read listed contract parameters, without requesting orders or paid snapshots."""
    if not isinstance(symbol,str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",symbol):
        return {"ok":False,"error":"Invalid US stock/ETF symbol"}
    try:
        from ib_async import Stock
        with _session() as ib:
            _identity(ib)
            stock = Stock(symbol,"SMART","USD")
            if not ib.qualifyContracts(stock):
                raise ValueError("Underlying stock could not be qualified")
            chains = ib.reqSecDefOptParams(symbol,"","STK",stock.conId)
            today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
            rows = [{"trading_class":c.tradingClass,"multiplier":100,
                     "expirations":[f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in sorted(c.expirations) if len(d)==8 and d >= today],
                     "strikes":sorted(float(s) for s in c.strikes if math.isfinite(s) and s>0)}
                    for c in chains if c.exchange=="SMART" and c.tradingClass==symbol and str(c.multiplier)=="100"]
            return {"ok":bool(rows),"symbol":symbol,"chains":rows,"source":"IBKR contract definitions",
                    "error":None if rows else "No standard 100-share SMART option class exposed for this symbol",
                    "note":"Not every listed strike/expiry combination exists; each requested leg is qualified separately."}
    except Exception as exc:
        return {"ok":False,"error":str(exc)[:180] if isinstance(exc,ValueError) else f"Option chain unavailable: {type(exc).__name__}"}


@_on_api_thread
def option_quotes(legs):
    """Short-lived live bid/ask subscriptions, with per-side receipt provenance."""
    try:
        from ib_async import Option
        if not isinstance(legs,list) or not 1 <= len(legs) <= 2:
            raise ValueError("Request one option or two vertical-spread legs")
        with _session() as ib:
            identity = _identity(ib)
            contracts = []
            for leg in legs:
                if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",str(leg.get("symbol", ""))) or leg.get("right") not in ("C","P") or leg.get("action") not in ("BUY","SELL"):
                    raise ValueError("Invalid option contract")
                expiry = datetime.strptime(leg["expiry"],"%Y-%m-%d").strftime("%Y%m%d")
                contract = Option(leg["symbol"],expiry,_number(leg["strike"]),leg["right"],"SMART",multiplier="100",currency="USD",tradingClass=leg["symbol"])
                found = ib.qualifyContracts(contract)
                if (len(found)!=1 or contract.secType!="OPT" or contract.currency!="USD" or contract.multiplier!="100"
                        or contract.tradingClass!=leg["symbol"] or contract.symbol!=leg["symbol"]
                        or contract.lastTradeDateOrContractMonth!=expiry or contract.right!=leg["right"]
                        or contract.strike!=float(leg["strike"]) or not contract.conId):
                    raise ValueError("Only qualified standard 100-share US equity options are supported")
                contracts.append(contract)
            streams = []
            try:
                for leg, contract in zip(legs,contracts):
                    started = datetime.now(timezone.utc)
                    ticker = ib.reqMktData(contract,"",False,False)
                    seen = {}
                    def changed(t, seen=seen, started=started):
                        for tick in t.ticks:
                            if tick.tickType in (1,2) and tick.time.tzinfo is not None and tick.time >= started:
                                seen["bid" if tick.tickType==1 else "ask"] = tick.time.isoformat()
                    ticker.updateEvent += changed
                    streams.append((leg,contract,ticker,seen,changed))
                for _ in range(16):
                    ib.sleep(.25)
                    if all("bid" in row[3] and "ask" in row[3] for row in streams):
                        break
                def optional(v):
                    try:return _number(v)
                    except (ValueError,TypeError):return None
                rows = []
                for leg,contract,ticker,seen,_ in streams:
                    g = ticker.modelGreeks
                    rows.append(dict(leg,con_id=contract.conId,multiplier=100,local_symbol=contract.localSymbol,
                        bid=optional(ticker.bid),ask=optional(ticker.ask),bid_size=optional(ticker.bidSize),ask_size=optional(ticker.askSize),
                        bid_observed_at=seen.get("bid"),ask_observed_at=seen.get("ask"),market_data_type=ticker.marketDataType,
                        halted=optional(ticker.halted),exchange_market_time=None,
                        greeks={k:optional(getattr(g,k,None)) for k in ("delta","gamma","theta","vega","impliedVol")}))
                return {"ok":True,"legs":rows,"source":"IBKR streaming bid/ask","received_at":datetime.now(timezone.utc).isoformat(),
                        "identity":identity,"note":"Times are local receipt of bid/ask events, not exchange timestamps. Greeks are broker model estimates. Market-data entitlements are required."}
            finally:
                for _,contract,ticker,_,handler in streams:
                    ticker.updateEvent -= handler
                    ib.cancelMktData(contract)  # Only this function's data subscription; not an order cancellation.
    except Exception as exc:
        return {"ok":False,"error":str(exc)[:180] if isinstance(exc,ValueError) else f"Options quote unavailable: {type(exc).__name__}"}


def _account_window_pnl(ib, account):
    """Account-window values are informational, never daily-loss inputs.

    Gateway reports these as $LEDGER fields on some accounts. BASE is the
    aggregate in the already-verified USD base currency; a USD currency segment
    alone must not be mistaken for the whole account's P&L.
    """
    rows = [row for row in ib.accountValues(account)
            if row.account == account and not getattr(row, "modelCode", "")] if hasattr(ib, "accountValues") else []
    values = {(row.tag, row.currency): row.value for row in rows}
    ready = [row.value.lower() for row in rows if row.tag == "AccountReady"]
    if ready and any(value != "true" for value in ready):
        return {"realized": None, "unrealized": None, "status": "account_resetting"}
    result = {"status": "unavailable"}
    for name, tag in (("realized", "RealizedPnL"), ("unrealized", "UnrealizedPnL")):
        result[name] = None
        for key in (("$LEDGER-" + tag, "BASE"), (tag, "BASE"), (tag, "USD")):
            try:
                result[name] = _number(values.get(key))
                break
            except (ValueError, TypeError):
                continue
    if any(result[name] is not None for name in ("realized", "unrealized")):
        result["status"] = "reported"
    return result


def _recover_initial_pnl(ib, account, subscription):
    """Refresh read-only inputs once, after the P&L observer is attached."""
    if (_SERVER_UNAVAILABLE or _RESYNC_REQUIRED or _ACCOUNT_READY.get(account) is False
            or not ib.isConnected()):
        return False
    recovery = subscription.setdefault("initial_recovery", {})
    now = time.monotonic()
    stage = None
    if "positions_at" not in recovery:
        stage = "positions"
        recovery["positions_at"] = now
        method = getattr(ib, "reqPositions", None)
        if not callable(method):
            recovery[stage] = "unsupported"
            return False
    elif "account_download" not in recovery and now - recovery["positions_at"] >= 15:
        stage = "account_download"
        if (not _ACCOUNT_UNSUBSCRIBED
                and account in getattr(getattr(ib, "wrapper", None), "_tomahawk_account_downloads", {})):
            # A repeated subscribe=True does not start another download on an
            # already subscribed account. Today's Gateway/API exports show no
            # second end callback, so waiting for one creates a false timeout.
            recovery[stage] = "already_complete"
            return False
        method = getattr(ib, "reqAccountUpdates", None)
        if not callable(method):
            recovery[stage] = "unsupported"
            return False
    if stage is None:
        return False
    # Mark before pumping callbacks so a timeout cannot cause repeated refreshes.
    recovery[stage] = "attempted"
    try:
        if stage == "positions":
            method()
        else:
            # Re-request the download without stopping a healthy account feed.
            # Gateway's exported trace shows STOP_UPDATE itself emits 2100;
            # treating that self-generated warning as failure replaced P&L again.
            if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
                raise ConnectionError("Gateway connection changed during account refresh")
            method(account)
        recovery[stage] = "completed"
        if stage == "account_download" and _ACCOUNT_UNSUBSCRIBED:
            # Warning 2100 during/after cancel dropped account data — restore on this socket.
            # Silent DailyPnL without 2100 stays on the existing cancel/retry path.
            _resubscribe_account_and_pnl(ib, account)
        return True
    except Exception as exc:
        recovery[stage] = "failed"
        recovery["last_error"] = (str(exc) or type(exc).__name__).replace(account, "selected account")[:200]
        return False



def _resubscribe_account_and_pnl(ib, account: str) -> list[str]:
    """After Warning 2100 (or intentional cancel), restore account+PnL feeds.

    Gateway process is left running. Never invents day_pnl / risk_ready.
    """
    global _ACCOUNT_UNSUBSCRIBED, _ACCOUNT_RESUBSCRIBE_AFTER
    actions: list[str] = []
    if time.monotonic() < _ACCOUNT_RESUBSCRIBE_AFTER:
        return ["resubscribe_cooldown"]
    try:
        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
            return ["resubscribe_held_upstream"]
        _pnl_trace("resubscribe_started")
        _ACCOUNT_UNSUBSCRIBED = False
        _PNL_WATCH["pending_resubscribe"] = False
        method = getattr(ib, "reqAccountUpdates", None)
        if callable(method):
            # STOP_UPDATE creates our own 2100 and can invalidate a fresh P&L
            # response. Request the download without unsubscribing first.
            method(account)
            actions.append(f"account_subscribe:{account}")
        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
            raise ConnectionError("Upstream changed during account resubscription")
        # Replace PnL subscription for this account on the live socket.
        try:
            ib.cancelPnL(account)
            actions.append(f"pnl_cancel:{account}")
        except Exception:
            pass
        carried_recovery = {}
        carried_started = None
        carried_retries = 0
        for key in [k for k in list(_PNL) if k[1] == account]:
            prev = _PNL.pop(key, None) or {}
            carried_recovery = prev.get("initial_recovery") or carried_recovery
            carried_started = prev.get("started_at", carried_started)
            carried_retries = prev.get("retries", carried_retries)
        _PNL_UPDATED.pop(account, None)
        now = time.monotonic()
        key = (id(ib), account)
        _PNL[key] = {
            "value": ib.reqPnL(account),
            "requested_at": now,
            "started_at": carried_started if carried_started is not None else now,
            "retries": carried_retries,
            "initial_recovery": carried_recovery,
            "callbacks": 0,
        }
        actions.append(f"pnl_subscribe:{account}")
        _pnl_trace("subscription_requested", reason="account_resubscribe")
        # Pump long enough for an initial DailyPnL callback (on_change may be slow at 0.0).
        # Soft reconnect too early after Warning 2100 causes a reconnect storm.
        for _ in range(80):
            if account in _PNL_UPDATED:
                break
            ib.sleep(0.15)
        _ACCOUNT_RESUBSCRIBE_AFTER = time.monotonic() + 30
        _pnl_trace("resubscribe_completed", callback_received=account in _PNL_UPDATED,
                   account_unsubscribed=_ACCOUNT_UNSUBSCRIBED)
        actions.append("resubscribe_done")
    except Exception as exc:
        _ACCOUNT_UNSUBSCRIBED = True
        _PNL_WATCH["pending_resubscribe"] = True
        _pnl_trace("resubscribe_failed", error_type=type(exc).__name__)
        actions.append(f"resubscribe_failed:{type(exc).__name__}")
        _ACCOUNT_RESUBSCRIBE_AFTER = time.monotonic() + 5
    return actions


def _cancel_all_pnl(ib):
    """Best-effort cancel of account PnL subscriptions; Gateway process untouched."""
    canceled = []
    for account in {account for (owner, account) in list(_PNL) if owner == id(ib)}:
        try:
            ib.cancelPnL(account)
            canceled.append(account)
        except Exception:
            pass
    return canceled


def _soft_reconnect_backoff_sec(count: int) -> float:
    """Exponential cooldown between soft reconnects (API only; Gateway untouched)."""
    n = max(1, int(count))
    delay = _SOFT_RECONNECT_BACKOFF_BASE * (2 ** min(n - 1, 4))
    return float(min(_SOFT_RECONNECT_BACKOFF_MAX, delay * random.uniform(1.0, 1.2)))


def _arm_soft_reconnect(reason: str):
    """Drop the API socket only. Does not restart IB Gateway."""
    global _RESYNC_REQUIRED, _CLIENT, _SOFT_RECONNECT_AFTER
    client = _CLIENT
    _pnl_trace("soft_reconnect", reason=reason)
    actions = [reason]
    if client is not None:
        actions.extend(f"cancel:{a}" for a in _cancel_all_pnl(client))
        try:
            client.disconnect()
            actions.append("api_disconnected")
        except Exception as exc:
            actions.append(f"disconnect_failed:{type(exc).__name__}")
    _disconnected()
    _CLIENT = None
    _RESYNC_REQUIRED = True
    count = int(_PNL_WATCH.get("soft_reconnect_count") or 0) + 1
    backoff = _soft_reconnect_backoff_sec(count)
    _SOFT_RECONNECT_AFTER = time.monotonic() + backoff
    _PNL_WATCH.update(
        soft_reconnect_count=count,
        last_soft_reconnect_at=time.monotonic(),
        ui_status="Recovering Daily P&L...",
        pending_soft_reconnect=False,
        pending_reason=None,
        last_arm_reason=reason,
        last_backoff_sec=backoff,
    )
    actions.append(f"backoff:{int(backoff)}s")
    return actions


@_on_api_thread
def refresh_broker_pnl(*, soft_reconnect: bool = True) -> dict[str, Any]:
    """Re-subscribe Daily P&L; optionally bounce the API client (not Gateway).

    Never invents risk_ready or day_pnl. Safe to call from the desk UI.
    """
    actions: list[str] = []
    try:
        if _SERVER_UNAVAILABLE and _CLIENT is not None and _CLIENT.isConnected():
            # 1100 is upstream of the still-open API socket. Give Gateway time
            # to report 1101/1102 instead of canceling retained subscriptions.
            return {"ok": False, "risk_ready": False, "account": {},
                    "error": "Waiting for IBKR server connectivity to recover",
                    "refresh": {"ok": False, "held": True, "actions": [],
                                "gateway_restarted": False, "soft_reconnect": soft_reconnect}}
        if soft_reconnect:
            actions.extend(_arm_soft_reconnect("manual_soft_reconnect"))
            # Rebuild the API socket before reading the account. Required so
            # _RESYNC_REQUIRED is cleared and PnL callbacks are accepted again.
            try:
                _ib()
                actions.append("api_reconnected")
            except Exception as exc:
                actions.append(f"reconnect_failed:{type(exc).__name__}")
                raise
        else:
            client = _CLIENT
            if client is not None and client.isConnected():
                actions.extend(f"cancel:{a}" for a in _cancel_all_pnl(client))
            _PNL.clear()
            _PNL_UPDATED.clear()
            actions.append("subscription_cleared")
        # Call the underlying account read; refresh already owns the API thread.
        acct = getattr(get_account, "__wrapped__", get_account)
        result = acct()
        out = dict(result)
        out["refresh"] = {
            "ok": True,
            "soft_reconnect": soft_reconnect,
            "actions": actions,
            "gateway_restarted": False,
            "note": (
                "API client bounced and Daily P&L re-requested; IB Gateway process was left running."
                if soft_reconnect else
                "Daily P&L subscription canceled and re-requested on the existing API socket."
            ),
        }
        return out
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)[:200],
            "risk_ready": False,
            "account": {},
            "refresh": {
                "ok": False,
                "soft_reconnect": soft_reconnect,
                "actions": actions,
                "gateway_restarted": False,
                "note": "Refresh failed; if Gateway is logged out or farms are down, restart/sign-in to Gateway.",
            },
        }


# A real IBKR callback gets this long before a flat-account $0 is derived.
FLAT_PNL_MIN_WAIT_SEC = 30.
# Executions are re-read from the broker at least this often while deriving.
FLAT_EXECUTIONS_MAX_AGE_SEC = 15.
_FLAT_EXECUTIONS: dict[str, Any] = {}


def _flat_day_evidence(ib, account) -> dict[str, Any]:
    """Proof that today's P&L is $0: no holdings now and no executions today.

    Anything missing or unreadable returns flat=False, so the gate stays closed.
    """
    try:
        positions = [p for p in ib.positions(account) if p.account == account]
        if any(isinstance(p.position, bool) or _number(p.position) != 0 for p in positions):
            return {"flat": False, "reason": "positions_held"}
        now = time.monotonic()
        cached = _FLAT_EXECUTIONS.get(account)
        if not cached or cached["client"] != id(ib) or now - cached["at"] > FLAT_EXECUTIONS_MAX_AGE_SEC:
            from ib_async import ExecutionFilter
            fills = ib.reqExecutions(ExecutionFilter(acctCode=account))
            cached = {"client": id(ib), "at": time.monotonic(),
                      "count": sum(1 for f in fills if getattr(f.execution, "acctNumber", account) == account)}
            _FLAT_EXECUTIONS[account] = cached
        session_fills = sum(1 for f in ib.fills() if getattr(f.execution, "acctNumber", account) == account)
        if cached["count"] or session_fills:
            return {"flat": False, "reason": "executions_today"}
        # Re-check holdings after the blocking read: a fill may have arrived meanwhile.
        if any(_number(p.position) != 0 for p in ib.positions(account) if p.account == account):
            return {"flat": False, "reason": "positions_held"}
        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or _ACCOUNT_READY.get(account) is not True:
            return {"flat": False, "reason": "account_not_ready"}
        return {"flat": True, "positions": 0, "executions_today": 0,
                "executions_checked_seconds_ago": round(time.monotonic() - cached["at"], 1),
                "note": "Derived: no holdings and no trades today, so today's P&L is $0. IBKR's own feed resumes when positions change."}
    except Exception as exc:  # noqa: BLE001 - unverifiable evidence never derives a value
        return {"flat": False, "reason": "unverified:" + type(exc).__name__}


def _flat_evidence_cached(ib, account) -> bool:
    """No broker I/O: the flat proof from live caches plus a recent execution read."""
    try:
        cached = _FLAT_EXECUTIONS.get(account)
        return bool(cached and cached["client"] == id(ib) and cached["count"] == 0
                    and time.monotonic() - cached["at"] <= 60
                    and not any(getattr(f.execution, "acctNumber", account) == account for f in ib.fills())
                    and all(_number(p.position) == 0 for p in ib.positions(account) if p.account == account)
                    and _ACCOUNT_READY.get(account) is True)
    except Exception:  # noqa: BLE001
        return False


@_on_api_thread
def get_account() -> dict[str, Any]:

    try:
        with _session() as ib:
            identity = _identity(ib)
            account = identity["account_id"]
            balances = _account_balances(ib.accountSummary(account), account)
            equity = balances["equity"]
            key = (id(ib), account)
            _ACCOUNT_EQUITY[key] = equity
            first = key not in _PNL
            for row in ib.accountValues(account) if hasattr(ib, "accountValues") else []:
                if row.account == account:
                    _account_value_update(row)
            # Gateway emits account P&L when totals CHANGE. Keep an established
            # stream; a numeric callback is not a periodic connection heartbeat.
            # Only missing/invalidated values may trigger a bounded replacement.
            now = time.monotonic()
            previous = _PNL.get(key)
            valid_sample = False
            if previous and account in _PNL_UPDATED and previous.get("day") == _pnl_day():
                try:
                    _number(previous["value"].dailyPnL)
                    valid_sample = True
                except (ValueError, TypeError):
                    pass
            if not valid_sample and previous and _flat_evidence_cached(ib, account):
                # A proven-flat account already has its $0; silent-feed retries and
                # socket bounces would only disconnect the desk (see _flat_day_evidence).
                valid_sample = True
            if (_ACCOUNT_UNSUBSCRIBED or _PNL_WATCH.get("pending_resubscribe")) and now >= _ACCOUNT_RESUBSCRIBE_AFTER:
                _resubscribe_account_and_pnl(ib, account)
                # Recovery may replace the object even when no callback arrives.
                # Always re-read it so the old request age cannot cancel it again.
                previous = _PNL.get(key)
                first = previous is None
                now = time.monotonic()
                if account not in _PNL_UPDATED:
                    # Stay on this socket; schedule soft reconnect only after backoff.
                    # Immediate soft reconnect after 2100 was thrashing clientId 37.
                    global _SOFT_RECONNECT_AFTER
                    delay = _soft_reconnect_backoff_sec(int(_PNL_WATCH.get("soft_reconnect_count") or 0) + 1)
                    _SOFT_RECONNECT_AFTER = time.monotonic() + max(90.0, delay)
                    _PNL_WATCH.update(
                        pending_soft_reconnect=True,
                        pending_reason="error_2100_resubscribe_silent",
                        last_backoff_sec=max(90.0, delay),
                        ui_status="Recovering Daily P&L...",
                    )
                else:
                    _PNL_WATCH.update(pending_soft_reconnect=False, ui_status=None, pending_reason=None)
                    previous = _PNL.get(key)
                    first = previous is None
            recovery_wait_until = (previous["restored_at"] + _PNL_SILENT_RETRY_SEC
                                   if previous and "restored_at" in previous else 0.)
            if (_PNL_WATCH.get("pending_soft_reconnect")
                    and now >= max(_SOFT_RECONNECT_AFTER, recovery_wait_until)):
                reason = _PNL_WATCH.get("pending_reason") or "pending_soft_reconnect"
                _PNL_WATCH["ui_status"] = "Recovering Daily P&L..."
                carried = dict(previous) if previous else {}
                _arm_soft_reconnect(reason)
                ib = _ib()
                identity = _identity(ib)
                account = identity["account_id"]
                balances = _account_balances(ib.accountSummary(account), account)
                equity = balances["equity"]
                key = (id(ib), account)
                _ACCOUNT_EQUITY[key] = equity
                for row in ib.accountValues(account) if hasattr(ib, "accountValues") else []:
                    if row.account == account:
                        _account_value_update(row)
                previous = {
                    "requested_at": carried.get("requested_at", now),
                    "started_at": carried.get("started_at", carried.get("requested_at", now)),
                    "retries": carried.get("retries", 0),
                    "initial_recovery": carried.get("initial_recovery", {}),
                }
                first = True
                now = time.monotonic()
            if (previous and now - previous["requested_at"] > _PNL_SILENT_RETRY_SEC
                    and not valid_sample and now >= max(_SOFT_RECONNECT_AFTER, recovery_wait_until)):
                # After repeated silent subscriptions, bounce the API client with
                # exponential backoff. Often restores DailyPnL without a Gateway
                # process restart; risk_ready stays false until a real callback.
                # Avoid cancel storms: IB may take >60s to emit the first 0.0 tick.
                # The cooldown also protects the replacement subscription: do
                # not cancel it while waiting for the next permitted reconnect.
                retries = previous.get("retries", 0)
                waiting = now - previous.get("started_at", previous["requested_at"])
                if retries >= 2 and waiting >= _PNL_SOFT_RECONNECT_WAIT_SEC and now >= _SOFT_RECONNECT_AFTER:
                    carried = dict(previous)
                    _arm_soft_reconnect("auto_soft_reconnect")
                    ib = _ib()
                    identity = _identity(ib)
                    account = identity["account_id"]
                    balances = _account_balances(ib.accountSummary(account), account)
                    equity = balances["equity"]
                    key = (id(ib), account)
                    _ACCOUNT_EQUITY[key] = equity
                    for row in ib.accountValues(account) if hasattr(ib, "accountValues") else []:
                        if row.account == account:
                            _account_value_update(row)
                    previous = {
                        "requested_at": carried.get("requested_at", now),
                        "started_at": carried.get("started_at", carried.get("requested_at", now)),
                        "retries": carried.get("retries", 0),
                        "initial_recovery": carried.get("initial_recovery", {}),
                    }
                    first = True
                else:
                    ib.cancelPnL(account)
                    _PNL.pop(key, None)
                    _PNL_UPDATED.pop(account, None)
                    first = True
            if first:
                _PNL_UPDATED.pop(account, None)
                _pnl_trace("subscription_requested", reason="initial" if previous is None else "silent_retry")
                _PNL[key] = {"value": ib.reqPnL(account), "requested_at": now,
                             "started_at": previous.get("started_at", previous["requested_at"]) if previous else now,
                             "retries": previous.get("retries", 0) + 1 if previous else 0,
                             "initial_recovery": previous.get("initial_recovery", {}) if previous else {},
                             "callbacks": 0}
            pnl = _PNL[key]["value"]
            deadline = now + (_PNL_FIRST_CALLBACK_WAIT_SEC if first else 0)
            daily = None
            try:
                while True:
                    try:
                        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or _ACCOUNT_READY.get(account) is False:
                            raise ValueError("IBKR connection recovery is pending")
                        daily = _number(pnl.dailyPnL)
                        if account not in _PNL_UPDATED or _PNL[key].get("day") != _pnl_day():
                            raise ValueError("No valid daily P&L callback for this account session and day")
                        break
                    except (ValueError, TypeError):
                        daily = None
                        if time.monotonic() >= deadline:
                            break
                        ib.sleep(0.1)
            except Exception:
                daily = None
            if daily is None and _recover_initial_pnl(ib, account, _PNL[key]):
                # Download completion is not P&L evidence. Accept only a real
                # callback for this request; final lifecycle checks still apply.
                pnl = _PNL[key]["value"]  # Recovery may have replaced the subscription object.
                if account in _PNL_UPDATED and _PNL[key].get("day") == _pnl_day():
                    try:
                        daily = _number(pnl.dailyPnL)
                    except (ValueError, TypeError):
                        pass
            account_pnl = _account_window_pnl(ib, account)
            # Missing P&L and an unresponsive API are different failures. Probe
            # the socket independently; a reply never substitutes for P&L.
            responsive = _api_responsive(ib)
            recovering = _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or _ACCOUNT_READY.get(account) is False
            if daily is not None:
                # A round trip pumps callbacks; it may have delivered a loss or
                # an unset value while confirming transport responsiveness.
                try:
                    daily = _number(pnl.dailyPnL)
                except (ValueError, TypeError):
                    daily = None
            if daily is not None and (not responsive or account not in _PNL_UPDATED
                    or _PNL[key].get("day") != _pnl_day() or _PNL[key]["value"] is not pnl):
                daily = None
            if recovering:
                daily = None
            pnl_source = "ibkr_daily_pnl" if daily is not None else None
            flat_evidence = None
            if (daily is None and not recovering and responsive
                    and time.monotonic() - _PNL[key]["requested_at"] >= FLAT_PNL_MIN_WAIT_SEC):
                # Owner decision (2026-09-28): IBKR sends daily P&L only on change, so a
                # reconnect while flat can wait forever. With every position at zero and
                # no execution today, nothing was held or traded, so today's P&L is $0.
                flat_evidence = _flat_day_evidence(ib, account)
                if flat_evidence.get("flat") is True:
                    daily, pnl_source = 0.0, "derived_flat_account"
            now = time.monotonic()
            subscription = _PNL[key]
            updated = _PNL_UPDATED.get(account)
            age = max(0, now - updated) if updated is not None else None
            state = ("recovering" if recovering else "ready" if daily is not None else "waiting" if updated is None
                     else "stale" if subscription.get("day") != _pnl_day() else "unavailable")
            if state in ("recovering", "waiting", "stale", "unavailable") and daily is None:
                if not _PNL_WATCH.get("ui_status"):
                    _PNL_WATCH["ui_status"] = "Recovering Daily P&L..." if state == "recovering" else _PNL_WATCH.get("ui_status")
                if state == "recovering":
                    _PNL_WATCH["ui_status"] = "Recovering Daily P&L..."
            elif daily is not None:
                _PNL_WATCH["ui_status"] = None
            diagnostics = {"status": state, "callbacks": subscription.get("callbacks", 0),
                           "update_mode": "on_change",
                           "ui_status": _PNL_WATCH.get("ui_status"),
                           "last_error_code": _PNL_WATCH.get("last_error_code"),
                           "soft_reconnect_count": int(_PNL_WATCH.get("soft_reconnect_count") or 0),
                           "soft_reconnect_backoff_sec": _PNL_WATCH.get("last_backoff_sec"),
                           "api_responsive": responsive,
                           "api_probe_error": _API_PULSE.get("error_type"),
                           "account_download_completed": account in getattr(getattr(ib, "wrapper", None), "_tomahawk_account_downloads", {}),
                           "api_response_age_seconds": round(max(0, now - _API_PULSE["at"]), 1) if responsive else None,
                           "last_update_age_seconds": round(age, 1) if age is not None else None,
                           "subscription_age_seconds": round(max(0, now - subscription["requested_at"]), 1),
                           "recovery_wait_seconds": round(max(0, subscription.get("restored_at", -_PNL_SILENT_RETRY_SEC)
                                                               + _PNL_SILENT_RETRY_SEC - now), 1) if daily is None else 0.,
                           "waiting_seconds": round(max(0, now - subscription.get("started_at", subscription["requested_at"])), 1),
                           "initial_recovery": {k: v for k, v in subscription.get("initial_recovery", {}).items() if k != "positions_at"},
                           "retries": subscription.get("retries", 0),
                           "source": pnl_source,
                           "flat_evidence": flat_evidence}
            reason = {"waiting": "Gateway has sent no daily P&L update for this subscription",
                      "stale": "A daily P&L callback for the current desk day is required",
                      "recovering": "Gateway connection recovery is pending; a new daily P&L update is required",
                      "unavailable": "The daily P&L value or Gateway API responsiveness could not be verified"}.get(state)
            if not recovering and daily is None and diagnostics["recovery_wait_seconds"] > 0:
                reason = "Connection restored; waiting for a new daily P&L update on the retained subscription"
            return {"ok": True, "identity": identity, "paper_mode": identity["paper_mode"],
                    "risk_ready": daily is not None,
                    "risk_error": None if daily is not None else f"IBKR daily P&L unavailable: {reason}; new risk is blocked. The desk retries automatically. If the portfolio-PnL setting is enabled and Gateway has been restarted, compare Client Portal's Daily P&L and report a missing API feed to IBKR.",
                    "pnl_diagnostics": diagnostics,
                    "account": {"id": account, "currency": "USD", **balances,
                                "account_window_pnl": account_pnl, "day_pnl": daily}}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "account": {}}


@_on_api_thread
def get_positions() -> dict[str, Any]:
    """Return STK holdings only. Prefer portfolio marks for last/unrealized.

    Never fold OPT rows into stock qty by underlying symbol.
    """
    try:
        with _session() as ib:
            identity = _identity(ib)
            account = identity["account_id"]
            marks: dict[int, Any] = {}
            try:
                for item in ib.portfolio(account):
                    c = item.contract
                    if item.account != account or c.secType != "STK" or c.currency != "USD":
                        continue
                    marks[int(c.conId)] = item
            except Exception:
                marks = {}
            rows = []
            for p in ib.positions(account):
                c = p.contract
                if p.account != account or c.secType != "STK" or c.currency != "USD":
                    continue
                qty = _number(p.position)
                item = marks.get(int(c.conId)) if c.conId else None
                last = _number(getattr(item, "marketPrice", None)) if item is not None else None
                upnl = _number(getattr(item, "unrealizedPNL", None)) if item is not None else None
                avg = _number(p.avgCost)
                # ib_async avgCost for STK is typically average cost per share.
                rows.append({
                    "symbol": c.symbol,
                    "con_id": int(c.conId or 0), "asset_type": "STK", "currency": "USD",
                    "qty": qty,
                    "side": "long" if qty >= 0 else "short",
                    "avg_entry_price": avg,
                    "current_price": last if last not in (None, 0) else None,
                    "unrealized_pl": upnl,
                })
            if (_identity(ib) != identity or not ib.isConnected() or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED
                    or _ACCOUNT_READY.get(account) is False):
                raise ValueError("Broker account changed or is still refreshing; holdings are unverified")
            return {"ok": True, "positions": rows, "identity": identity,
                    "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "positions": []}


@_on_api_thread
def get_option_positions() -> dict[str, Any]:
    """Return OPT holdings only; never fold into stock qty by underlying symbol."""
    try:
        with _session() as ib:
            identity = _identity(ib)
            rows = []
            for p in ib.positions(identity["account_id"]):
                c = p.contract
                if p.account != identity["account_id"] or c.secType != "OPT" or c.currency != "USD":
                    continue
                qty = _number(p.position)
                try:
                    mult = int(float(c.multiplier or 100))
                except (TypeError, ValueError):
                    mult = 0
                expiry = getattr(c, "lastTradeDateOrContractMonth", None) or ""
                if len(expiry) == 8 and expiry.isdigit():
                    expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
                rows.append({
                    "symbol": c.symbol,
                    "asset_type": "OPT",
                    "con_id": int(c.conId),
                    "multiplier": mult,
                    "right": c.right,
                    "strike": _number(c.strike),
                    "expiry": expiry,
                    "qty": qty,
                    "local_symbol": getattr(c, "localSymbol", None),
                    "side": "long" if qty >= 0 else "short",
                    "avg_entry_price": _number(p.avgCost) / mult if mult else _number(p.avgCost),
                })
            if (_identity(ib) != identity or not ib.isConnected() or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED
                    or _ACCOUNT_READY.get(identity["account_id"]) is False):
                raise ValueError("Broker account changed or is still refreshing; option holdings are unverified")
            return {"ok": True, "positions": rows, "identity": identity,
                    "checked_at": datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "positions": []}




@_on_api_thread
def get_position_exposure() -> dict[str, Any]:
    """All held asset types/currencies, for restart safety; never a stock-only view."""
    try:
        with _session() as ib:
            identity = _identity(ib)
            positions = list(ib.positions(identity["account_id"]))
            held = 0
            for p in positions:
                if p.account != identity["account_id"]:
                    continue
                qty = float(p.position)
                if not math.isfinite(qty):
                    raise ValueError("Position quantity is unverified")
                held += int(qty != 0)
            return {"ok": True, "has_positions": held > 0, "position_count": held}
    except Exception as exc:
        return {"ok": False, "has_positions": None, "error": str(exc)[:200]}


@_on_api_thread
def get_safety_snapshot(*, include_marks=False) -> dict[str, Any]:
    """Read positions and broker-reported working orders together; no order changes."""
    def optional_number(value):
        try:
            return None if isinstance(value, bool) else _number(value)
        except (ValueError, TypeError):
            return None
    try:
        with _session() as ib:
            identity = _identity(ib)
            account = identity["account_id"]
            orders = []
            for t in ib.reqAllOpenOrders():
                if t.order.account != account:
                    continue
                o, c, s = t.order, t.contract, t.orderStatus
                if isinstance(o.totalQuantity, bool) or isinstance(s.filled, bool):
                    raise ValueError("Working order quantities are unreadable")
                orders.append({"id": _order_id(t, account), "con_id": int(c.conId or 0),
                    "symbol": c.symbol, "asset_type": c.secType, "side": o.action.lower(),
                    "currency": c.currency, "local_symbol": c.localSymbol,
                    "multiplier": 1.0 if c.secType == "STK" and c.multiplier in ("", "1") else optional_number(c.multiplier),
                    "limit_price": optional_number(o.lmtPrice) if o.orderType in ("LMT", "STP LMT") else None,
                    "qty": _number(o.totalQuantity), "filled_qty": _number(s.filled),
                    "order_type": o.orderType, "status": s.status, "parent_id": int(o.parentId or 0),
                    "permanent_id": int(o.permId or 0), "transmit": o.transmit is True,
                    "stop_price": _number(o.auxPrice) if o.orderType in ("STP", "STP LMT") else None,
                    "time_in_force": o.tif, "outside_regular_hours": bool(o.outsideRth),
                    "oca_group": o.ocaGroup, "why_held": str(s.whyHeld or ""),
                    "conditions_count": len(o.conditions) if isinstance(o.conditions, list) else None,
                    "good_after_time": o.goodAfterTime, "good_till_date": o.goodTillDate,
                    "active_start_time": o.activeStartTime, "active_stop_time": o.activeStopTime,
                    "algo_strategy": o.algoStrategy, "right": c.right})
            marks = {}
            marks_available = False
            if include_marks:
                try:
                    for item in ib.portfolio(account):
                        if item.account == account:
                            marks.setdefault(int(item.contract.conId), []).append(item)
                    marks_available = True
                except Exception:
                    marks = {}
            positions = []
            for p in ib.positions(account):
                if p.account != account:
                    continue
                if isinstance(p.position, bool):
                    raise ValueError("Position quantity is unreadable")
                c, qty = p.contract, _number(p.position)
                if not qty:
                    continue
                positions.append({"symbol": c.symbol, "asset_type": c.secType, "currency": c.currency,
                    "con_id": int(c.conId or 0), "qty": qty, "right": str(c.right or ""),
                    "expiry": str(c.lastTradeDateOrContractMonth or "")[:8],
                    "local_symbol": c.localSymbol})
                if include_marks:
                    row = positions[-1]
                    row.update(multiplier=1.0 if c.secType == "STK" and c.multiplier in ("", "1") else optional_number(c.multiplier),
                               market_price=None, mark_status="missing" if marks_available else "unavailable")
                    candidates = marks.get(row["con_id"], [])
                    observed = []
                    for item in candidates:
                        contract = item.contract
                        observed.append((contract.secType, contract.currency, contract.symbol,
                                         optional_number(item.position), optional_number(item.marketPrice)))
                    if observed:
                        if any(value != observed[0] for value in observed):
                            row["mark_status"] = "conflicting"
                        elif observed[0][:3] != (c.secType, c.currency, c.symbol):
                            row["mark_status"] = "contract_mismatch"
                        elif observed[0][3] != qty:
                            row["mark_status"] = "quantity_mismatch"
                        elif observed[0][4] is None or observed[0][4] <= 0:
                            row["mark_status"] = "invalid"
                        else:
                            row.update(market_price=observed[0][4], mark_status="reported")
            if (_identity(ib) != identity or not ib.isConnected() or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED
                    or _ACCOUNT_READY.get(account) is False):
                raise ValueError("Broker account changed or is still refreshing; protection is unverified")
            result = {"ok": True, "paper_mode": identity["paper_mode"], "positions": positions,
                      "orders": orders, "checked_at": datetime.now(timezone.utc).isoformat()}
            if include_marks:
                result["mark_observation"] = {"read_at": result["checked_at"], "broker_price_time": None,
                    "source": "IBKR portfolio account updates", "live_prices_verified": False,
                    "note": "Broker-reported periodic marks. Reading the cache now does not establish when the market price changed."}
            return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _order_id(trade, account: str) -> str:
    permanent = getattr(trade.order, "permId", 0)
    if permanent and 0 < int(permanent) < 2**63:
        return f"{account}:perm:{permanent}"
    return f"{account}:{trade.order.clientId}:{trade.order.orderId}"


def _matches_order(trade, order_id):
    order = trade.order
    return order_id in (_order_id(trade, order.account),
                       f"{order.account}:{order.clientId}:{order.orderId}")


def _known_trades(ib):
    # completedOrder omits clientId/orderId and creates an empty OrderStatus.
    # Preserve retained live trade objects before requesting those snapshots.
    retained = ib.trades() if callable(getattr(ib, "trades", None)) else []
    return list(retained) + list(ib.reqAllOpenOrders()) + list(ib.reqCompletedOrders(apiOnly=False))


def _execution_state(ib, matches, order_id, executions=None):
    """Completed status alone is never execution-price/quantity evidence."""
    trade = matches[-1]
    states = [str(t.orderStatus.status).lower() for t in matches]
    status = next((s for s in reversed(states) if s == "filled" or s in _DEAD), states[-1])
    quantities = []
    required = 0.0
    for candidate in matches:
        os_ = candidate.orderStatus
        try:
            q, p = _number(os_.filled), _number(os_.avgFillPrice)
            required = max(required, q)
            if q > 0 and p > 0:
                quantities.append((q, p))
        except (ValueError, TypeError):
            pass
        try:
            required = max(required, _number(candidate.order.filledQuantity))
        except (ValueError, TypeError, AttributeError):
            pass
        if status == "filled":
            required = max(required, _number(candidate.order.totalQuantity))
    # reqExecutions is read-only and is necessary after a restart or a rapid fill.
    from ib_async import ExecutionFilter
    if executions is None:
        executions = ib.reqExecutions(ExecutionFilter(acctCode=trade.order.account))
    perms = {getattr(t.order, "permId", 0) for t in matches} - {0}
    families = {}
    earliest = {}
    unversioned = []
    for fill in executions:
        ex = fill.execution
        if ex.acctNumber != trade.order.account:
            continue
        if not ((ex.permId in perms) or (not perms and
                order_id == f"{ex.acctNumber}:{ex.clientId}:{ex.orderId}")):
            continue
        revision = re.fullmatch(r"(.+)\.(\d+)", str(getattr(ex, "execId", "")))
        if revision:
            family, version = revision[1], int(revision[2])
            if family not in earliest or version < earliest[family][0]:
                earliest[family] = (version, ex)
            if family not in families or version > families[family][0]:
                families[family] = (version, ex)
        else:
            unversioned.append(ex)
    retained = [ex for _, ex in families.values()] + unversioned
    versions = {family: version for family, (version, _) in families.items()}
    correction = bool(versions) and max(versions.values()) > 1
    for ex in retained:
        q, p = _number(ex.cumQty), _number(ex.avgPrice)
        required = max(required, q)
        if q > 0 and p > 0:
            quantities.append((q, p))
    filled, price = max(quantities, key=lambda value: value[0], default=(0.0, 0.0))
    if correction:
        # Corrected per-execution quantities supersede their original revision;
        # stale orderStatus/cumQty maxima must not restore invalidated shares.
        # A partial history is not authority to reduce a previously retained fill.
        shares = [_number(ex.shares) for ex in retained]
        prices = [_number(ex.price) for ex in retained]
        cumulative = [_number(ex.cumQty) for ex in retained]
        total = sum(shares)
        # A later fill's cumulative field can still include an earlier original
        # that has since been corrected. Original reports can establish complete
        # coverage while corrected per-fill shares/prices supply the new totals.
        originals = [ex for _, ex in earliest.values()]
        original_shares = [_number(ex.shares) for ex in originals]
        original_complete = (all(q >= 0 for q in original_shares)
            and math.isclose(sum(original_shares), max((_number(ex.cumQty) for ex in originals), default=0),
                             rel_tol=0, abs_tol=1e-8))
        complete = (not unversioned and all(q >= 0 for q in shares)
                    and all(q == 0 or p > 0 for q, p in zip(shares, prices))
                    and (original_complete or math.isclose(total, max(cumulative, default=0), rel_tol=0, abs_tol=1e-8)))
        if not complete:
            return {"state": "unknown", "terminal": False, "order_id": order_id,
                    "execution_details_verified": False, "execution_correction": True,
                    "execution_versions": versions,
                    "error": "Corrected execution history is incomplete; reconcile against the broker statement"}
        filled, price = total, sum(q*p for q, p in zip(shares, prices))/total if total else 0.0
        required = total
    terminal = (status == "filled" or status in _DEAD) and filled >= required
    if status == "filled" and filled <= 0 and not correction:
        terminal = False
    return {"state": "filled" if status == "filled" else ("partially_filled" if filled > 0 else ("failed" if terminal else "pending")),
            "terminal": terminal, "filled_qty": filled, "filled_avg_price": price,
            "broker_status": status, "alpaca_status": status, "order_id": order_id,
            "execution_details_verified": filled >= required,
            "execution_versions": versions, "execution_correction": correction}


@_on_api_thread
def refresh_execution_corrections(order_ids, expected_identity):
    """Refresh bounded retained orders with one read-only execution snapshot."""
    try:
        from ib_async import ExecutionFilter
        if not isinstance(order_ids, (list, tuple)) or len(order_ids) > 50:
            raise ValueError("At most 50 retained order IDs may be refreshed")
        with _session() as ib:
            identity = _identity(ib)
            if identity != expected_identity:
                raise ValueError("Execution correction account differs from the retained account")
            trades = _known_trades(ib)
            executions = ib.reqExecutions(ExecutionFilter(acctCode=identity["account_id"]))
            if _identity(ib) != identity or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED:
                raise ValueError("Execution correction account or connection changed")
            updates = []
            for order_id in dict.fromkeys(order_ids):
                if not isinstance(order_id, str) or order_id.split(":", 1)[0] != identity["account_id"]:
                    continue
                matches = [trade for trade in trades if _matches_order(trade, order_id)]
                perms = {getattr(t.order, "permId", 0) for t in matches} - {0}
                matches += [t for t in trades if t not in matches and t.order.account == identity["account_id"]
                            and getattr(t.order, "permId", 0) in perms]
                if matches:
                    update = _execution_state(ib, matches, order_id, executions)
                    update["identity"] = identity
                    updates.append(update)
            return {"ok": True, "identity": identity, "orders": updates}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "orders": []}


def _option_lifecycle_guard(ib, order, identity):
    guard = order.get("option_lifecycle_guard")
    if guard is None:
        return None
    if identity.get("paper_mode") is not True or guard.get("identity") != identity:
        return "Automatic option lifecycle is restricted to the confirmed paper account"
    account, symbol = identity["account_id"], order["ticker"]
    actual, stock = {}, 0.0
    for p in ib.positions(account):
        c = p.contract
        if p.account != account or c.symbol != symbol:
            continue
        qty = _number(p.position)
        if not qty:
            continue
        if c.currency != "USD" or c.secType not in ("STK", "OPT"):
            return "Underlying exposure changed during option preflight"
        if c.secType == "STK":
            stock += qty
        else:
            key = str(c.conId)
            if key in actual or str(c.multiplier) != "100":
                return "Option holding terms changed during preflight"
            actual[key] = qty
    if actual != guard.get("options") or stock != guard.get("stock_qty"):
        return "Option or stock holdings changed during preflight; review possible assignment or external activity"
    if any(t.order.account == account and t.contract.symbol == symbol for t in ib.openTrades()):
        return "Another order now affects this option underlying"
    return None


@_on_api_thread
def option_lifecycle_snapshot(expected_identity):
    """Fresh full holdings + all-client orders; no exercise or trading operations."""
    try:
        with _session() as ib:
            identity = _identity(ib)
            if identity != expected_identity:
                raise ValueError("Option account changed")
            account = identity["account_id"]
            # Wait for positionEnd rather than interpreting an empty cache as flat.
            positions = ib.reqPositions()
            trades = ib.reqAllOpenOrders()
            rows = []
            for p in positions:
                if p.account != account:
                    continue
                c, qty = p.contract, _number(p.position)
                if qty:
                    rows.append(dict(symbol=c.symbol, con_id=int(c.conId), asset_type=c.secType,
                                     currency=c.currency, qty=qty, right=c.right, strike=_number(c.strike),
                                     multiplier=int(c.multiplier) if c.multiplier else None,
                                     expiry=str(c.lastTradeDateOrContractMonth or "")[:8]))
            orders = [dict(symbol=t.contract.symbol, asset_type=t.contract.secType,
                           con_ids=([int(x.conId) for x in (t.contract.comboLegs or [])]
                                    if t.contract.secType == "BAG" else [int(t.contract.conId)]))
                      for t in trades if t.order.account == account]
            if (_identity(ib) != identity or not ib.isConnected() or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED
                    or _ACCOUNT_READY.get(account) is False):
                raise ValueError("Option account is still refreshing")
            # reqAllOpenOrders can process newer position callbacks; use the now-complete cache.
            current = {int(p.contract.conId): _number(p.position) for p in ib.positions(account) if p.account == account}
            if {r["con_id"]: r["qty"] for r in rows} != {k: v for k, v in current.items() if v}:
                raise ValueError("Holdings changed during option check; check again")
            return dict(ok=True, identity=identity, positions=rows, orders=orders,
                        checked_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        return dict(ok=False, error=str(exc)[:200])


def _submission_risk_error(ib, order, identity):
    """No broker I/O: validate callbacks after the final blocking preflight."""
    authorization = order.get("risk_authorization")
    if not isinstance(authorization, dict) or not isinstance(authorization.get("reducing"), bool):
        return "Verified risk authorization required before broker submission"
    account = identity["account_id"]
    if (_SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected()
            or _ACCOUNT_READY.get(account) is False or _VERIFIED.get("identity") != identity
            or _VERIFIED.get("settings") != _settings()):
        return "Broker connection or account changed during preflight; request a fresh review"
    # managedAccounts reads the wrapper cache; it does not issue a broker request.
    try:
        if _identity(ib) != identity:
            return "Broker account changed during preflight; request a fresh review"
    except (ValueError, TypeError):
        return "Broker account unavailable after preflight; request a fresh review"
    equity = authorization.get("equity")
    if isinstance(equity, bool) or not isinstance(equity, (int, float)) or not math.isfinite(equity) or equity <= 0:
        return "Verified broker equity required before broker submission"
    if _ACCOUNT_EQUITY.get((id(ib), account)) != equity:
        return "Broker equity changed during preflight; request a fresh review"
    option_error = _option_lifecycle_guard(ib, order, identity)
    if option_error:
        return option_error
    if authorization["reducing"]:
        return None  # Deliberate exception for an already verified reducing order.
    if _API_PULSE.get("clock_error"):
        return _API_PULSE["clock_error"]
    daily = authorization.get("day_pnl")
    if isinstance(daily, bool) or not isinstance(daily, (int, float)) or not math.isfinite(daily):
        return "Verified daily P&L required before broker submission"
    subscription = _PNL.get((id(ib), account))
    if (daily == 0 and account not in _PNL_UPDATED and subscription
            and _API_PULSE.get("client") == id(ib) and time.monotonic() - _API_PULSE.get("at", 0) <= 15):
        # The derived flat-account $0 (see _flat_day_evidence), re-proven from caches only.
        return None if _flat_evidence_cached(ib, account) else (
            "The account is no longer provably flat and IBKR has sent no daily P&L; request a fresh review")
    if (not subscription or account not in _PNL_UPDATED or subscription.get("day") != _pnl_day()
            or _API_PULSE.get("client") != id(ib) or time.monotonic() - _API_PULSE.get("at", 0) > 15):
        return "Daily P&L or connection validity changed during preflight; request a fresh review"
    try:
        if _number(subscription["value"].dailyPnL) != daily:
            return "Daily P&L changed during preflight; request a fresh review"
    except (TypeError, ValueError):
        return "Daily P&L unavailable after preflight; request a fresh review"
    return None


@_on_api_thread
def get_open_orders() -> dict[str, Any]:
    try:
        with _session() as ib:
            identity = _identity(ib)
            rows = []
            for trade in ib.reqAllOpenOrders():
                if trade.order.account != identity["account_id"]:
                    continue
                rows.append({"id": _order_id(trade, identity["account_id"]),
                             "symbol": trade.contract.symbol, "side": trade.order.action.lower(),
                             "qty": _number(trade.order.totalQuantity),
                             "filled_qty": _number(trade.orderStatus.filled),
                             "limit_price": trade.order.lmtPrice if trade.order.orderType == "LMT" else None,
                             "asset_type": trade.contract.secType,
                             "con_id": int(trade.contract.conId or 0),
                             "multiplier": int(float(trade.contract.multiplier or (100 if trade.contract.secType == "OPT" else 1)))})
            return {"ok": True, "orders": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "orders": []}


def _listed_us_stock(contract):
    return (getattr(contract, "secType", None) == "STK"
            and getattr(contract, "currency", None) == "USD" and getattr(contract, "conId", 0) > 0
            and getattr(contract, "primaryExchange", "").upper() in {
                "NASDAQ", "NYSE", "ARCA", "AMEX", "BATS", "IEX", "ISLAND", "NYSEARCA", "NYSEAMERICAN"})


def _stock_call_cover(ib, account, symbol, *, positions=None, trades=None, exclude=None):
    """Cached, conservative share reservations; no netting of option hedges.

    A pending call buyback has not released shares. A stock stop and a new
    covered call cannot both promise the same shares. Unknown/adjusted calls
    and working spreads require a combined-position review, never a guess.
    """
    stock, calls, stock_sells = 0., 0., 0.
    def amount(value):
        if isinstance(value, bool):
            raise ValueError("Call cover quantities are unverified")
        try:
            return _number(value)
        except (TypeError, ValueError):
            raise ValueError("Call cover quantities are unverified") from None
    def standard_call(c):
        if getattr(c, "currency", None) != "USD" or amount(getattr(c, "multiplier", None)) != 100:
            raise ValueError("Short call cover needs review: adjusted or unknown contract size")
    for p in ib.positions(account) if positions is None else positions:
        c = p.contract
        if p.account != account or c.symbol != symbol:
            continue
        qty = amount(p.position)
        if c.secType == "STK" and c.currency == "USD":
            stock += qty
        elif c.secType == "OPT" and qty < 0:
            if c.right not in ("C", "P"):
                raise ValueError("Short option cover is unverified")
            if c.right == "C":
                standard_call(c)
                calls += -qty * 100
    for t in ib.openTrades() if trades is None else trades:
        c, o = t.contract, t.order
        if exclude is not None and (o.account, o.clientId, o.orderId) == exclude:
            continue  # Only the separately verified owned stop being resized.
        if o.account != account or symbol not in str(getattr(c, "symbol", "")).split(","):
            continue
        total, filled = amount(o.totalQuantity), amount(t.orderStatus.filled)
        if total <= 0 or not 0 <= filled <= total or o.action not in ("BUY", "SELL"):
            raise ValueError("Working quantities for call cover are unverified")
        remaining = total - filled
        if not remaining:
            continue
        if c.secType == "BAG":
            raise ValueError("Call cover needs review while a spread on this stock is working")
        if o.action != "SELL":
            continue
        if c.secType == "STK" and c.currency == "USD":
            stock_sells += remaining
        elif c.secType == "OPT":
            if c.right not in ("C", "P"):
                raise ValueError("Working option cover is unverified")
            if c.right == "C":
                standard_call(c)
                calls += remaining * 100
    return {"held": stock, "call_shares": calls, "working_sells": stock_sells,
            "available": max(0., stock - calls - stock_sells)}


def _stop_context(ib, expected):
    identity = _identity(ib)
    if (identity != expected or identity.get("paper_mode") is not True
            or type(identity.get("client_id")) is not int or identity["client_id"] <= 0):
        raise ValueError("Broker-held stop qualification requires the same IBKR paper account and API client")
    if (not ib.isConnected() or _SERVER_UNAVAILABLE or _RESYNC_REQUIRED
            or _ACCOUNT_READY.get(identity["account_id"]) is not True):
        raise ValueError("Broker is reconnecting or account holdings are unverified")
    return identity


def _reported_open_orders(ib):
    # ib_async only updates selected Order fields on an existing Trade. Capture
    # the actual openOrder reply so an external change to TIF/conditions cannot
    # hide behind that cache. The adapter owner thread serializes this request.
    reported = {}
    original = ib.wrapper.openOrder
    def capture(order_id, contract, order, state):
        reported[(order.account, order.clientId, order_id)] = copy.deepcopy((contract, order, state))
        original(order_id, contract, order, state)
    ib.wrapper.openOrder = capture
    try:
        trades = copy.deepcopy(ib.reqAllOpenOrders())
    finally:
        ib.wrapper.openOrder = original
    for trade in trades:
        key = (trade.order.account, trade.order.clientId, trade.order.orderId)
        if key not in reported:
            raise ValueError("A broker order reply is missing; protection is unverified")
        contract, order, state = reported[key]
        trade.contract, trade.order = contract, order
        trade.orderStatus.status = state.status
    return trades


def _stock_stop_snapshot(ib, ticker, ref, identity):
    """Explicit broker replies, never an optimistic locally changed Order object."""
    from ib_async import Stock
    if not re.fullmatch(r"tstop-[0-9a-f]{32}", str(ref)):
        raise ValueError("A persisted stop reference is required")
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", str(ticker)):
        raise ValueError("A US stock symbol is required")
    contract = Stock(ticker, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if len(qualified) != 1 or not _listed_us_stock(contract) or not contract.conId:
        raise ValueError("The US stock contract could not be verified")
    details = ib.reqContractDetails(contract)
    if len(details) != 1 or details[0].contract.conId != contract.conId:
        raise ValueError("The broker price step is ambiguous")
    step = _number(details[0].minTick)
    if step <= 0:
        raise ValueError("The broker price step is unavailable")
    # Copies prevent subsequent callbacks or local placeOrder mutation from
    # silently rewriting the evidence used by the compare-before-send guard.
    opened = _reported_open_orders(ib)
    matches = [t for t in opened if t.order.account == identity["account_id"] and t.order.orderRef == ref]
    completed = False
    if not matches:
        matches = [copy.deepcopy(t) for t in ib.reqCompletedOrders(apiOnly=False)
                   if t.order.account == identity["account_id"] and t.order.orderRef == ref]
        completed = True
    if len(matches) > 1:
        raise ValueError("Multiple broker orders share the stop reference; review required")
    trade = matches[0] if matches else None
    observed, exclude = None, None
    if trade:
        o, c, s = trade.order, trade.contract, trade.orderStatus
        if (c.conId != contract.conId or c.secType != "STK" or c.currency != "USD"
                or o.action != "SELL" or o.orderType != "STP" or o.tif != "GTC"
                or o.transmit is not True or o.outsideRth is not False
                or o.parentId or o.ocaGroup or o.conditions or o.algoStrategy
                or o.triggerMethod or o.allOrNone or o.notHeld or o.discretionaryAmt
                or o.goodAfterTime or o.goodTillDate or o.activeStartTime or o.activeStopTime):
            raise ValueError("Stop terms changed outside the app; review required")
        # Completed callbacks can omit API client/order IDs. Only open orders
        # may be changed, and only by their original positive API client ID.
        if not completed and (o.clientId != identity["client_id"] or o.orderId <= 0):
            raise ValueError("Stop belongs to another API client; it will not be adopted or rebound")
        total = _number(o.totalQuantity)
        filled = _number(o.filledQuantity if completed else s.filled)
        price = _number(o.auxPrice)
        if (any(isinstance(v, bool) for v in (o.totalQuantity, o.auxPrice, o.filledQuantity if completed else s.filled))
                or total <= 0 or not total.is_integer() or not filled.is_integer()
                or not 0 <= filled <= total or price <= 0):
            raise ValueError("Broker stop quantities or price are unverified")
        observed = {"status": s.status, "quantity": int(total), "filled": int(filled),
                    "stop_price": price, "permanent_id": o.permId,
                    "order_id": o.orderId if not completed else None,
                    "client_id": o.clientId if not completed else None,
                    "why_held": s.whyHeld, "con_id": c.conId}
        exclude = (o.account, o.clientId, o.orderId) if not completed else None
    positions = ib.reqPositions()
    stocks = [p for p in positions if p.account == identity["account_id"]
              and p.contract.symbol == ticker and p.contract.secType == "STK"]
    if (len(stocks) > 1 or any(p.contract.conId != contract.conId or p.contract.currency != "USD"
                              for p in stocks)):
        raise ValueError("Ambiguous stock holdings; review required")
    cover = _stock_call_cover(ib, identity["account_id"], ticker,
                             positions=positions, trades=opened, exclude=exclude)
    if cover["held"] < 0 or not float(cover["held"]).is_integer():
        raise ValueError("Only verified whole long shares qualify for this stop test")
    _stop_context(ib, identity)
    return {"held": int(cover["held"]), "available": cover["available"],
            "min_tick": step, "order": observed}, trade, contract


@_on_api_thread
def observe_stock_stop(ticker, ref, expected_identity):
    try:
        with _session() as ib:
            identity = _stop_context(ib, expected_identity)
            view, _, _ = _stock_stop_snapshot(ib, ticker, ref, identity)
            return {"ok": True, "view": view}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}


@_on_api_thread
def change_stock_stop(command, still_authorized):
    """Internal paper-only entry point. Durable intent must exist before calling."""
    attempted = False
    try:
        from ib_async import StopOrder
        from broker_router import submission_window_error
        from stock_protection import stop_price, WORKING, CANCELLED
        with _session() as ib:
            identity = _stop_context(ib, command.get("broker_identity"))
            action, ref = command["action"], command["ref"]
            if action not in ("create", "modify", "cancel") or not callable(still_authorized):
                raise ValueError("A current protective-stop authorization is required")
            view, trade, contract = _stock_stop_snapshot(ib, command["ticker"], command["previous_ref"], identity)
            if view != command["expected"]:
                raise ValueError("Broker holdings or stop terms changed; reconcile before another action")
            observed = view["order"]
            qty, total, price = command["quantity"], command["total_quantity"], command["stop_price"]
            if type(qty) is not int or type(total) is not int or qty < 0 or total < 0:
                raise ValueError("Verified whole-share stop quantities are required")
            if action == "create":
                if observed and observed["status"] not in CANCELLED:
                    raise ValueError("An earlier stop is still outstanding")
                if trade:
                    fresh, _, _ = _stock_stop_snapshot(ib, command["ticker"], ref, identity)
                    if ref == command["previous_ref"] or fresh["order"] is not None:
                        raise ValueError("Replacement requires a new unused reference")
                    if fresh["held"] != view["held"] or fresh["available"] != view["available"]:
                        raise ValueError("Holdings changed while checking the replacement")
                elif ref != command["previous_ref"]:
                    raise ValueError("Initial stop reference does not match its intent")
            elif (not observed or observed["status"] not in WORKING
                  or observed["client_id"] != identity["client_id"] or ref != command["previous_ref"]):
                raise ValueError("Only this client's confirmed working stop may be changed")
            if action != "cancel":
                if qty <= 0 or qty > view["available"] or qty > view["held"]:
                    raise ValueError("Stop would sell unavailable shares or remove short-call cover")
                if total != qty + (observed["filled"] if action == "modify" else 0):
                    raise ValueError("Stop total must include its confirmed partial fills")
                if stop_price(price, view["min_tick"]) != price:
                    raise ValueError("Stop price must match the broker price step")
                if action == "modify" and price < observed["stop_price"]:
                    raise ValueError("An automatic stop cannot be loosened")
            # All blocking reads have finished. Compare the current cache too:
            # events can change holdings or competing reservations during reads.
            exclude = ((identity["account_id"], identity["client_id"], observed["order_id"])
                       if observed and observed["order_id"] is not None else None)
            current = _stock_call_cover(ib, identity["account_id"], command["ticker"], exclude=exclude)
            if current["held"] != view["held"] or current["available"] != view["available"]:
                raise ValueError("Positions or working orders changed before stop submission")
            _stop_context(ib, identity)
            if observed and observed["order_id"] is not None:
                cached = [t for t in ib.openTrades() if (t.order.account, t.order.clientId, t.order.orderId) == exclude]
                if (len(cached) != 1 or cached[0].orderStatus.status != observed["status"]
                        or _number(cached[0].orderStatus.filled) != observed["filled"]
                        or _number(cached[0].order.totalQuantity) != observed["quantity"]
                        or _number(cached[0].order.auxPrice) != observed["stop_price"]):
                    raise ValueError("Stop changed or filled while checking positions")
            error = submission_window_error(command)
            if error or not still_authorized():
                raise ValueError(error or "Stop authorization changed before submission")
            if action == "cancel":
                attempted = True
                ib.cancelOrder(trade.order)
            else:
                native = StopOrder("SELL", total, price, account=identity["account_id"],
                                   orderRef=ref, tif="GTC", outsideRth=False, transmit=True)
                if action == "modify":
                    native.orderId, native.clientId, native.permId = trade.order.orderId, trade.order.clientId, trade.order.permId
                attempted = True
                ib.placeOrder(contract, native)
            return {"ok": True, "submission_attempted": True, "status": "awaiting_broker_confirmation"}
    except Exception as exc:
        return {"ok": False, "submission_attempted": attempted, "error": str(exc)[:240]}


def _stock_sale_cover_error(ib, contract, order, identity):
    if order.get("side") != "sell":
        return None
    cover = _stock_call_cover(ib, identity["account_id"], contract.symbol)
    if cover["call_shares"] and _number(order["shares"]) > cover["available"]:
        return (f"Stock sale would remove short call cover: only {cover['available']:g} shares remain "
                "after short/working calls and working stock sells. Review the combined position at the broker.")
    return None


def _position_intent_error(ib, contract, order, identity):
    """Final cached position/order check; does not yield after qualification."""
    intent = order.get("position_intent")
    if intent is None:
        return None
    if intent not in ("buy", "sell", "cover") or not _listed_us_stock(contract):
        return "Direct tickets require a verified US-listed stock contract and position intent"
    expected = {"con_id": contract.conId, "symbol": contract.symbol,
                "primary_exchange": contract.primaryExchange, "currency": contract.currency}
    if not order.get("agent_policy") and order.get("contract_identity") != expected:
        return "Stock contract differs from the reviewed instrument; request a fresh review"
    if order.get("side") != ("sell" if intent == "sell" else "buy"):
        return "Position intent does not match the order side"
    from live_ticket import position_error
    account = identity["account_id"]
    held = sum(_number(p.position) for p in ib.positions(account)
               if p.account == account and p.contract.conId == contract.conId)
    reserved = {"buy_qty": 0.0, "sell_qty": 0.0}
    for trade in ib.openTrades():
        if trade.order.account != account or trade.contract.conId != contract.conId:
            continue
        side = trade.order.action.lower()
        total, filled = _number(trade.order.totalQuantity), _number(trade.orderStatus.filled)
        if side not in ("buy", "sell") or total < 0 or filled < 0 or filled > total:
            return "Working order quantities changed; request a fresh review"
        reserved[side + "_qty"] += total-filled
    return position_error(intent, held, int(order["shares"]), reserved)



class _SubmissionUncertain(RuntimeError):
    """The socket send may have reached IBKR. Reconcile; never release intent."""


def _send_native_order(ib, contract, native):
    try:
        return ib.placeOrder(contract, native)
    except Exception as exc:
        raise _SubmissionUncertain(str(exc)[:300]) from exc


@_on_api_thread
def _place_option_from_desk(ib, order, identity, ref):
    """Live options: single-leg OPT (BTO/STC/STO/BTC). Calls and puts.

    Covered STO (calls): requires long underlying shares >= contracts*100.
    Covered STO (calls) also subtracts shares already pledged to short or working calls.
    Naked STO: requires allow_naked on the order plus buying_power >= worst-case loss
    (strike-based floor — not full IBKR margin). BTC verifies short OPT by con_id.
    """
    intent = str(order.get("option_intent") or order.get("position_intent") or "").upper()
    if order.get("asset_type") == "BAG" or order.get("legs"):
        raise ValueError("Use _place_bag_from_desk for multi-leg/BAG orders")
    if intent not in ("BTO", "STC", "STO", "BTC"):
        raise ValueError("Live options accept BTO/STC/STO/BTC only")
    # Gate STO flags before any qualify / account I/O so unflagged shorts fail closed.
    if intent == "STO":
        covered = bool(order.get("covered"))
        allow_naked = bool(order.get("allow_naked") or order.get("allow_naked_short"))
        if not (covered or allow_naked):
            raise ValueError("STO refused: set covered=True with long shares (calls) or allow_naked=True")
    side = str(order.get("side") or "").lower()
    from ib_async import Option, MarketOrder, LimitOrder
    expect_side = "buy" if intent in ("BTO", "BTC") else "sell"
    if side != expect_side:
        raise ValueError("Option intent does not match order side")
    qty = _number(order.get("contracts", order.get("shares")))
    if qty <= 0 or not qty.is_integer():
        raise ValueError("A positive whole contract quantity is required")
    right = str(order.get("right") or "").upper()
    right = "C" if right in ("C", "CALL") else ("P" if right in ("P", "PUT") else "")
    if right not in ("C", "P"):
        raise ValueError("Call or Put required")
    expiry = str(order.get("expiry") or "")
    if len(expiry) == 10 and expiry[4] == "-":
        expiry_ib = expiry.replace("-", "")
    elif len(expiry) == 8 and expiry.isdigit():
        expiry_ib = expiry
        expiry = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
    else:
        raise ValueError("Option expiry must be YYYY-MM-DD")
    strike = _number(order.get("strike"))
    symbol = str(order.get("ticker") or "").upper()
    contract = Option(symbol, expiry_ib, strike, right, "SMART", multiplier="100", currency="USD", tradingClass=symbol)
    found = ib.qualifyContracts(contract)
    if (len(found) != 1 or contract.secType != "OPT" or contract.currency != "USD" or contract.multiplier != "100"
            or not contract.conId):
        raise ValueError("Only qualified standard 100-share US equity options are supported")
    expected = order.get("contract_identity") or {}
    if expected.get("con_id") and int(expected["con_id"]) != int(contract.conId):
        raise ValueError("Option contract differs from the reviewed instrument; request a fresh review")
    if expected.get("local_symbol") and expected["local_symbol"] != contract.localSymbol:
        raise ValueError("Option local symbol differs from the reviewed instrument; request a fresh review")
    from broker_router import submission_window_error
    expiry_error = submission_window_error(order)
    if expiry_error:
        raise ValueError(expiry_error)
    account = identity["account_id"]
    held = sum(_number(p.position) for p in ib.positions(account)
               if p.account == account and p.contract.conId == contract.conId and p.contract.secType == "OPT")
    reserved_sell = 0.0
    reserved_buy = 0.0
    for trade in ib.openTrades():
        if (trade.order.account == account and trade.contract.secType == "BAG"
                and int(contract.conId) in {int(getattr(leg, "conId", 0) or 0)
                                           for leg in (trade.contract.comboLegs or [])}):
            raise ValueError("Option contract already belongs to a working spread; reconcile it before another order")
        if trade.order.account != account or trade.contract.conId != contract.conId:
            continue
        side_w = trade.order.action.lower()
        total, filled = _number(trade.order.totalQuantity), _number(trade.orderStatus.filled)
        if side_w not in ("buy", "sell") or total < 0 or filled < 0 or filled > total:
            raise ValueError("Working option order quantities changed; request a fresh review")
        if side_w == "sell":
            reserved_sell += total - filled
        else:
            reserved_buy += total - filled
    if intent == "STC":
        available = max(0.0, held - reserved_sell)
        if held <= 0:
            raise ValueError("No long option holding for this contract id; STC refused")
        if qty > available:
            raise ValueError(f"Only {int(available)} contracts of this option remain available after working sells")
        # STC receives the reducing-order exception in the desk. It cannot
        # silently remove a long leg that may protect another short option.
        # The broker book does not prove which legs belong together, so require
        # a reviewed whole-spread close instead of inventing that relationship.
        for p in ib.positions(account):
            c = p.contract
            if (p.account == account and c.secType == "OPT" and c.symbol == symbol
                    and c.right == right and _number(p.position) < 0):
                raise ValueError("STC refused: a short option on this stock may need this long leg for protection; "
                                 "review and close the combined position at the broker")
        for t in ib.openTrades():
            c, o = t.contract, t.order
            if (o.account == account and c.secType == "OPT" and c.symbol == symbol
                    and c.right == right and c.conId != contract.conId and o.action == "SELL"):
                total, filled = _number(o.totalQuantity), _number(t.orderStatus.filled)
                if total < 0 or not 0 <= filled <= total:
                    raise ValueError("Working option quantities are unverified")
                if total > filled:
                    raise ValueError("STC refused: another option sell may need this long leg for protection; "
                                     "review the combined position at the broker")
    elif intent == "BTC":
        short_held = max(0.0, -held)
        available = max(0.0, short_held - reserved_buy)
        if held >= 0:
            raise ValueError("No short option holding for this contract id; BTC refused")
        if qty > available:
            raise ValueError(f"Only {int(available)} short contracts remain available to cover after working buys")
    elif intent == "BTO" and held < 0:
        raise ValueError("Existing short option on this contract; use BTC or close in TWS before BTO")
    elif intent == "STO":
        covered = bool(order.get("covered"))
        allow_naked = bool(order.get("allow_naked") or order.get("allow_naked_short"))
        if covered and right == "C":
            cover = _stock_call_cover(ib, account, symbol)
            need = int(qty) * 100
            if cover["available"] < need:
                raise ValueError(
                    f"Covered short call needs {need} unpledged long {symbol} shares; only {cover['available']:g} "
                    "remain after short/working calls and working stock sells"
                )
        elif not allow_naked:
            raise ValueError("STO refused: set covered=True with long shares (calls) or allow_naked=True")
        else:
            from order_terms import option_max_loss
            premium = order.get("limit")
            if premium is None and order.get("option_notional") is not None:
                premium = float(order["option_notional"]) / (int(qty) * 100)
            if premium is None:
                raise ValueError("Naked STO requires a limit premium or option_notional for buying-power check")
            # Worst-case loss (cash-secured strike for puts, strike floor for calls),
            # not the premium received.
            floor = option_max_loss({"contracts": int(qty), "option_intent": "STO", "right": right,
                                     "strike": float(strike), "covered": False}, float(premium))
            bp, state = _summary_amount(ib.accountSummary(account), account, "BuyingPower")
            if bp is None:
                raise ValueError(f"Naked STO blocked: USD buying power for the selected account is {state}; no other balance can substitute")
            if bp < floor:
                raise ValueError(
                    f"Naked STO blocked: buying_power {bp:.2f} < risk floor {floor:.2f}. "
                    "This is not a full IBKR margin calc."
                )
            order["funds_check"] = {
                "source": "IBKR account-summary cache", "currency": "USD", "buying_power": bp,
                "risk_floor": floor, "read_at": datetime.now(timezone.utc).isoformat(),
                "broker_value_time": None,
                "scope": "Selected-account buying-power floor only; not settled cash, reserved cash, instrument permission or full margin approval. A naked call has no bounded maximum loss.",
            }
        if held > 0:
            raise ValueError("Already long this option contract; close or STC before STO")
    if order.get("type") == "limit":
        native = LimitOrder(side.upper(), int(qty), float(order["limit"]),
                            account=account, orderRef=ref, tif="DAY", outsideRth=False)
    else:
        native = MarketOrder(side.upper(), int(qty), account=account, orderRef=ref)
    risk_error = _submission_risk_error(ib, order, identity)
    if risk_error:
        raise ValueError(risk_error)
    return _send_native_order(ib, contract, native)


@_on_api_thread
def _place_bag_from_desk(ib, order, identity, ref):
    """Live BAG vertical: two OPT legs, same underlying/expiry/right, distinct strikes."""
    from ib_async import Contract, ComboLeg, Option, MarketOrder, LimitOrder
    from order_terms import canonical_bag_order
    strategy = str(order.get("option_strategy") or "")
    qty = _number(order.get("contracts", order.get("shares")))
    if qty <= 0 or not qty.is_integer():
        raise ValueError("A positive whole BAG quantity is required")
    opt = {
        "type": order.get("type", "market"),
        "strategy": strategy,
        "right": order.get("right"),
        "expiry": order.get("expiry"),
        "long_strike": order.get("long_strike"),
        "short_strike": order.get("short_strike"),
        "intent": order.get("option_intent") or "OPEN",
    }
    if order.get("type") == "limit":
        opt["limit_price"] = order.get("limit")
    canonical = canonical_bag_order(
        {"side": order.get("side"), "suggested_shares": int(qty), "contracts": int(qty)},
        opt,
    )
    legs_spec = canonical["legs"]
    symbol = str(order.get("ticker") or "").upper()
    expiry = canonical["expiry"]
    expiry_ib = expiry.replace("-", "")
    right = canonical["right"]
    account = identity["account_id"]
    qualified = []
    for leg in legs_spec:
        c = Option(symbol, expiry_ib, float(leg["strike"]), right, "SMART",
                   multiplier="100", currency="USD", tradingClass=symbol)
        found = ib.qualifyContracts(c)
        if (len(found) != 1 or c.secType != "OPT" or c.multiplier != "100" or not c.conId):
            raise ValueError(f"BAG leg strike {leg['strike']} could not be qualified as a standard OPT")
        qualified.append((c, leg))
    expected = order.get("contract_identity") or {}
    expected_legs = expected.get("legs")
    if len({int(c.conId) for c, _ in qualified}) != 2:
        raise ValueError("BAG legs did not qualify as two distinct option contracts")
    if expected_legs is not None:
        try:
            expected_by_strike = {float(leg["strike"]): int(leg["con_id"]) for leg in expected_legs}
            actual_by_strike = {float(c.strike): int(c.conId) for c, _ in qualified}
            if len(expected_legs) != 2 or expected_by_strike != actual_by_strike:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise ValueError("BAG contracts differ from the reviewed legs; request a fresh review") from None
    bag = Contract()
    bag.symbol = symbol
    bag.secType = "BAG"
    bag.currency = "USD"
    bag.exchange = "SMART"
    combo_legs = []
    for c, leg in qualified:
        cl = ComboLeg()
        cl.conId = int(c.conId)
        cl.ratio = int(leg.get("ratio") or 1)
        cl.action = str(leg["action"]).upper()
        cl.exchange = "SMART"
        combo_legs.append(cl)
    bag.comboLegs = combo_legs
    if canonical["option_intent"] == "CLOSE":
        # A CLOSE is authorized as risk-reducing, so it must unwind a spread that
        # is actually held: +qty on the bought strike, -qty on the sold strike.
        held = {}
        for p in ib.positions(account):
            if p.account == account and p.contract.secType == "OPT":
                held[int(p.contract.conId)] = held.get(int(p.contract.conId), 0.0) + _number(p.position)
        (long_c, _), (short_c, _) = qualified
        # Working combos on the same two legs already claim part of the spread;
        # a second CLOSE must not pass on positions that are about to be closed.
        legs_key = {int(long_c.conId), int(short_c.conId)}
        working = 0.0
        for trade in ib.openTrades():
            c = trade.contract
            if trade.order.account != account:
                continue
            if c.secType == "OPT" and int(c.conId) in legs_key:
                raise ValueError("BAG CLOSE refused: an option leg already has a working order")
            other_legs = {int(getattr(cl, "conId", 0) or 0) for cl in (getattr(c, "comboLegs", None) or [])}
            if c.secType == "BAG" and other_legs & legs_key and other_legs != legs_key:
                raise ValueError("BAG CLOSE refused: an overlapping spread already has a working order")
            if (trade.order.account != account or c.secType != "BAG"
                    or {int(getattr(cl, "conId", 0) or 0) for cl in (c.comboLegs or [])} != legs_key):
                continue
            working += max(0.0, _number(trade.order.totalQuantity) - _number(trade.orderStatus.filled))
        need = qty + working
        if held.get(int(long_c.conId), 0.0) < need or held.get(int(short_c.conId), 0.0) > -need:
            raise ValueError("BAG CLOSE refused: the account does not hold this spread in that quantity"
                             + (f" beyond {int(working)} contracts already working" if working else ""))
    from broker_router import submission_window_error
    expiry_error = submission_window_error(order)
    if expiry_error:
        raise ValueError(expiry_error)
    combo_action = str(canonical.get("combo_action") or ("BUY" if order.get("side") == "buy" else "SELL")).upper()
    if order.get("type") == "limit":
        native = LimitOrder(combo_action, int(qty), abs(float(order["limit"])),
                            account=account, orderRef=ref, tif="DAY", outsideRth=False)
    else:
        native = MarketOrder(combo_action, int(qty), account=account, orderRef=ref)
    risk_error = _submission_risk_error(ib, order, identity)
    if risk_error:
        raise ValueError(risk_error)
    return _send_native_order(ib, bag, native)



@_on_api_thread
def place_from_desk_order(order: dict[str, Any]) -> dict[str, Any]:
    trade = None
    identity = None
    attempted = False
    try:
        from ib_async import MarketOrder, Stock
        with _session() as ib:
            identity = _identity(ib)
            if order.get("broker_identity") != identity:
                raise ValueError("Broker account changed; confirm the current account before submitting")
            side = str(order.get("side") or "").lower()
            qty = _number(order.get("shares") if order.get("asset_type") != "OPT" else order.get("contracts", order.get("shares")))
            if side not in ("buy", "sell") or qty <= 0 or not qty.is_integer():
                raise ValueError("A positive whole quantity and buy/sell side are required")
            if order.get("asset_type") == "BAG" or order.get("legs"):
                from order_terms import canonical_bag_order
                opt = {"type": order.get("type", "market"), "right": order.get("right"),
                       "expiry": order.get("expiry"), "long_strike": order.get("long_strike"),
                       "short_strike": order.get("short_strike"),
                       "strategy": order.get("option_strategy"),
                       "intent": order.get("option_intent") or "OPEN"}
                if order.get("type") == "limit":
                    opt["limit_price"] = order.get("limit")
                canonical_bag_order({"side": side, "suggested_shares": qty, "contracts": qty}, opt)
            elif order.get("asset_type") == "OPT" or order.get("option_intent"):
                from order_terms import canonical_option_order
                opt = {"type": order.get("type", "market"), "right": order.get("right"),
                       "expiry": order.get("expiry"), "strike": order.get("strike"),
                       "intent": order.get("option_intent"),
                       "allow_naked": order.get("allow_naked") or order.get("allow_naked_short"),
                       "covered": order.get("covered"),
                       "strategy": order.get("option_strategy")}
                if order.get("type") == "limit":
                    opt["limit_price"] = order.get("limit")
                canonical_option_order({"side": side, "suggested_shares": qty, "contracts": qty}, opt)
            else:
                from order_terms import canonical_order
                options = {"type": order.get("type", "market")}
                if options["type"] == "limit":
                    options["limit_price"] = order.get("limit")
                canonical_order({"side": side, "suggested_shares": qty}, options)
            ref = str(order.get("signal_id") or "")
            if not ref:
                raise ValueError("signal_id required")
            trades = _known_trades(ib)
            matches = { _order_id(t, identity["account_id"]): t for t in trades
                        if t.order.account == identity["account_id"] and t.order.orderRef == ref }
            if len(matches) > 1:
                raise ValueError("Multiple broker orders share this signal; reconcile before continuing")
            trade = next(iter(matches.values()), None)
            if trade is None:
                if order.get("asset_type") == "BAG" or order.get("legs"):
                    trade = _place_bag_from_desk(ib, order, identity, ref)
                    ib.sleep(0.5)
                    return {"ok": True, "status": "paper_submitted" if identity["paper_mode"] else "live_submitted",
                            "broker": "ibkr", "order_id": _order_id(trade, identity["account_id"]),
                            "paper_mode": identity["paper_mode"], "endpoint": identity["endpoint"], "order": order}
                if order.get("asset_type") == "OPT" or order.get("option_intent"):
                    trade = _place_option_from_desk(ib, order, identity, ref)
                    ib.sleep(0.5)
                    return {"ok": True, "status": "paper_submitted" if identity["paper_mode"] else "live_submitted",
                            "broker": "ibkr", "order_id": _order_id(trade, identity["account_id"]),
                            "paper_mode": identity["paper_mode"], "endpoint": identity["endpoint"], "order": order}
                contract = Stock(str(order.get("ticker") or "").upper(), "SMART", "USD")
                if not ib.qualifyContracts(contract):
                    raise ValueError("IBKR could not qualify the stock contract")
                from broker_router import submission_window_error
                expiry_error = submission_window_error(order)
                if expiry_error:
                    raise ValueError(expiry_error)
                if order.get("type") == "limit":
                    from ib_async import LimitOrder
                    native = LimitOrder(side.upper(), int(qty), float(order["limit"]),
                                        account=identity["account_id"], orderRef=ref, tif="DAY", outsideRth=False)
                else:
                    native = MarketOrder(side.upper(), int(qty), account=identity["account_id"], orderRef=ref)
                risk_error = _submission_risk_error(ib, order, identity)
                if not risk_error and not order["risk_authorization"]["reducing"]:
                    final_quote = stock_quote(contract.symbol)
                    risk_error = _stock_execution_error(final_quote, order, identity, contract.conId)
                    # The quote request blocks. Authorization and expiry can
                    # change while waiting; check again immediately before send.
                    risk_error = risk_error or submission_window_error(order) or _submission_risk_error(ib, order, identity)
                if not risk_error:
                    risk_error = _stock_sale_cover_error(ib, contract, order, identity)
                if not risk_error:
                    risk_error = _position_intent_error(ib, contract, order, identity)
                if risk_error:
                    raise ValueError(risk_error)
                attempted = True
                trade = ib.placeOrder(contract, native)
                ib.sleep(0.5)
            return {"ok": True, "status": "paper_submitted" if identity["paper_mode"] else "live_submitted",
                    "broker": "ibkr", "order_id": _order_id(trade, identity["account_id"]),
                    "paper_mode": identity["paper_mode"], "endpoint": identity["endpoint"], "order": order}
    except Exception as exc:
        result = {"ok": False, "status": "error", "broker": "ibkr", "paper_mode": paper_mode(),
                  "error": str(exc)[:300], "submission_attempted": attempted or isinstance(exc, _SubmissionUncertain),
                  "order": dict(order)}
        # Allocation/acceptance can precede a socket failure. Never discard the
        # identity just because a later sleep/read raised, or offer a fresh submit.
        if trade is not None and identity is not None:
            result.update(order_id=_order_id(trade, identity["account_id"]),
                          submission_attempted=True, endpoint=identity["endpoint"])
        return result


@_on_api_thread
def find_order_by_signal(signal_id: str, expected_identity=None) -> dict[str, Any]:
    """Read-only recovery of an ambiguous submit; absence never authorizes retry."""
    try:
        if not signal_id:
            raise ValueError("signal_id required for submission recovery")
        with _session() as ib:
            identity = _identity(ib)
            if expected_identity != identity:
                raise ValueError("Submission belongs to a different broker account")
            trades = _known_trades(ib)
            matches = {_order_id(t, identity["account_id"]): t for t in trades
                       if t.order.account == identity["account_id"] and t.order.orderRef == signal_id}
            if len(matches) > 1:
                raise ValueError("Multiple orders match the submission; manual broker review required")
            if not matches:
                return {"ok": True, "found": False}
            return {"ok": True, "found": True, "broker": "ibkr", "order_id": next(iter(matches)),
                    "paper_mode": identity["paper_mode"], "endpoint": identity["endpoint"]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@_on_api_thread
def _poll(order_id: str, timeout: float, interval: float, cancel: bool = False,
          expected_identity=None, cancel_expires=None) -> dict[str, Any]:
    try:
        with _session() as ib:
            identity = _identity(ib)
            account, client_id, number = order_id.split(":")
            if account != identity["account_id"]:
                raise ValueError("Pending order belongs to another IBKR account")
            deadline = time.monotonic() + max(0, timeout)
            last = {"state": "unknown", "terminal": False, "order_id": order_id}
            canceled = False
            while True:
                trades = _known_trades(ib)
                matches = [t for t in trades if _matches_order(t, order_id)]
                # Old persisted IDs can be joined to completed snapshots by permId.
                perms = {getattr(t.order, "permId", 0) for t in matches} - {0}
                matches += [t for t in trades if t not in matches and t.order.account == account
                            and getattr(t.order, "permId", 0) in perms]
                if matches:
                    last = _execution_state(ib, matches, order_id)
                    if last["terminal"]:
                        return last
                    owned = next((t for t in matches if t.order.clientId == identity["client_id"]
                                  and str(t.orderStatus.status).lower() not in {"filled", *_DEAD}), None)
                    if cancel and not canceled and owned:
                        if expected_identity is not None and (_identity(ib) != expected_identity or time.time() >= cancel_expires):
                            raise ValueError("Cancellation account changed or review expired during broker check")
                        ib.cancelOrder(owned.order)
                        canceled = True
                if time.monotonic() >= deadline:
                    return last
                ib.sleep(interval)
    except Exception as exc:
        return {"state": "unknown", "terminal": False, "error": str(exc)[:200], "order_id": order_id}


def wait_for_fill(order_id: str, timeout: float = 20, interval: float = 0.25) -> dict[str, Any]:
    return _poll(order_id, timeout, interval)


@_on_api_thread
def cancel_reviewed_order(order_id, expected_identity, expires):
    try:
        with _session() as ib:
            if _identity(ib) != expected_identity or time.time() >= expires:
                raise ValueError("Cancellation account changed or review expired")
            return _poll(order_id, 2.0, .25, cancel=True, expected_identity=expected_identity, cancel_expires=expires)
    except Exception as exc:
        return {"state": "unknown", "terminal": False, "error": str(exc)[:200]}


@_on_api_thread
def estimate_order(order):
    """What-if simulation only; estimates cannot guarantee an all-in budget."""
    try:
        from ib_async import Stock, LimitOrder, MarketOrder
        with _session() as ib:
            identity = _identity(ib)
            if identity != order.get("broker_identity"):
                raise ValueError("Broker identity changed")
            contract = Stock(order["ticker"], "SMART", "USD")
            if not ib.qualifyContracts(contract):
                raise ValueError("Contract unavailable")
            contract_verified = _listed_us_stock(contract)
            args = {"account": identity["account_id"], "tif": "DAY", "outsideRth": False}
            native = (LimitOrder(order["side"].upper(), order["shares"], order["limit"], **args)
                      if order["type"] == "limit" else MarketOrder(order["side"].upper(), order["shares"], **args))
            state = ib.whatIfOrder(contract, native)
            # IB uses its maximum double sentinel when commission is unknown.
            commission = _number(state.commission)
            return {"commission_estimate": commission if 0 <= commission < 1e8 else None,
                    "currency": state.commissionCurrency or None, "source": "IBKR what-if",
                    "guaranteed": False, "warning": state.warningText or None,
                    "contract_verified": contract_verified,
                    "contract": {"con_id": contract.conId, "symbol": contract.symbol,
                                 "primary_exchange": contract.primaryExchange, "currency": contract.currency}}
    except Exception as exc:
        return {"commission_estimate": None, "guaranteed": False, "error": str(exc)[:200]}


def reconcile_after_timeout(order_id: str, timeout: float = 2.0) -> dict[str, Any]:
    return _poll(order_id, timeout, 0.25, cancel=True)


def flatten_broker() -> dict[str, Any]:
    return {"ok": False, "error": "IBKR flatten requires explicit per-position confirmation"}

def pnl_watch_snapshot() -> dict[str, Any]:
    """Read-only watchdog snapshot for UI/diagnostics. Never invents risk_ready."""
    with _PNL_TRACE_LOCK:
        trace = list(_PNL_TRACE)
    return {
        "lifecycle_events": trace,
        "trace_error": _PNL_TRACE_ERROR,
        "ui_status": _PNL_WATCH.get("ui_status"),
        "last_error_code": _PNL_WATCH.get("last_error_code"),
        "soft_reconnect_count": int(_PNL_WATCH.get("soft_reconnect_count") or 0),
        "pending_soft_reconnect": bool(_PNL_WATCH.get("pending_soft_reconnect")),
        "pending_reason": _PNL_WATCH.get("pending_reason"),
        "soft_reconnect_after_monotonic": _SOFT_RECONNECT_AFTER,
        "last_backoff_sec": _PNL_WATCH.get("last_backoff_sec"),
        "last_scheduled_refresh_at": _PNL_WATCH.get("last_scheduled_refresh_at"),
        "clock_skew_lower_bound_sec": _API_PULSE.get("clock_skew_lower_bound_sec"),
        "clock_error": _API_PULSE.get("clock_error"),
        "queued_api_tasks": _TASKS.qsize(),
        "note": (
            "IBKR 2FA / IB Key cannot be disabled. Soft reconnect and re-reqPnL "
            "run first; full Gateway logout still requires human 2FA."
        ),
    }


def maybe_scheduled_soft_refresh(*, interval_minutes: float, auto_live: bool, risk_ready: bool) -> dict[str, Any] | None:
    """Optional soft refresh while auto_live and not risk_ready. Never invents Ready."""
    if not auto_live or risk_ready:
        _PNL_WATCH["last_scheduled_refresh_at"] = None
        return None
    try:
        minutes = float(interval_minutes)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    now = time.monotonic()
    if _SERVER_UNAVAILABLE:
        _PNL_WATCH["last_scheduled_refresh_at"] = now
        return None
    last = float(_PNL_WATCH.get("last_scheduled_refresh_at") or 0.0)
    # First observation only arms the timer — do not soft-reconnect until the
    # interval elapses. Immediate soft reconnect on startup races the initial
    # DailyPnL callback and triggers Warning 2100 storms.
    if not last:
        _PNL_WATCH["last_scheduled_refresh_at"] = now
        return None
    # Automatic subscription recovery and this timer share one reconnect clock.
    # Otherwise an overdue timer can immediately undo a newly opened socket.
    last = max(last, float(_PNL_WATCH.get("last_soft_reconnect_at") or 0.0))
    if (now - last) < minutes * 60.0:
        return None
    if now < _SOFT_RECONNECT_AFTER:
        return None
    _PNL_WATCH["last_scheduled_refresh_at"] = now
    _PNL_WATCH["ui_status"] = "Recovering Daily P&L..."
    return refresh_broker_pnl(soft_reconnect=True)


def _gateway_exe_candidates() -> list[str]:
    explicit = (os.environ.get("IB_GATEWAY_EXE") or "").strip()
    out: list[str] = []
    if explicit:
        out.append(explicit)
    bases = [r"C:\Jts\ibgateway", os.path.join(os.path.expanduser("~"), "Jts", "ibgateway")]
    for base in bases:
        if not os.path.isdir(base):
            continue
        try:
            versions = sorted(os.listdir(base), reverse=True)
        except OSError:
            continue
        for name in versions:
            candidate = os.path.join(base, name, "ibgateway.exe")
            if os.path.isfile(candidate):
                out.append(candidate)
    # de-dupe preserve order
    seen = set()
    unique = []
    for path in out:
        key = os.path.normcase(os.path.abspath(path)) if os.path.exists(path) else os.path.normcase(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


_GATEWAY_IMAGES = ("ibgateway.exe", "tws.exe")
GATEWAY_LAUNCH_COOLDOWN_SEC = 180.0
# Automatic (unattended) launches wait until Gateway has been absent this long,
# so a Gateway auto-restart or a window the user is opening is not raced.
GATEWAY_ABSENT_DEBOUNCE_SEC = 90.0
# Hard launch budget, whoever asks: a missed process check can never stack windows.
GATEWAY_LAUNCH_BUDGET = (3, 1800.0)            # at most 3 launches in 30 minutes
GATEWAY_AUTOMATIC_BUDGET = (2, 86400.0)        # at most 2 unattended launches a day
GATEWAY_AUTOMATIC_CHECK_SEC = 30.0             # unattended callers re-check at most this often
_GATEWAY_ABSENT_SINCE: list[float | None] = [None]
_GATEWAY_LAST_AUTOMATIC: dict[str, Any] = {"at": 0.0, "result": None}
# "IBKR Gateway" (10.51+), "IB Gateway", or TWS login and main windows.
# Window titles start with the product name; a folder, file or web page that merely mentions
# it is not Gateway. Browsers, shells and editors are never counted by title.
_GATEWAY_TITLE_RE = re.compile(r"^\s*(?:IBKR|IB)\s+Gateway\b|^\s*Trader Workstation\b", re.I)
_TITLE_IGNORED_IMAGES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "iexplore.exe",
                         "explorer.exe", "code.exe", "notepad.exe", "notepad++.exe", "powershell.exe", "pwsh.exe",
                         "cmd.exe", "windowsterminal.exe", "conhost.exe", "outlook.exe", "winword.exe"}
_GATEWAY_IMAGE_RE = re.compile(r"^(?:ib(?:kr)?gateway|tws)[\w.-]*\.exe$", re.I)
GATEWAY_MUTEX_NAME = "Local\\TomahawkGatewayLaunch"  # shared with Start-Tomahawk.ps1 / Ensure-IBGateway.ps1


def _gateway_autolaunch_disabled() -> bool:
    return (os.environ.get("IB_GATEWAY_AUTOLAUNCH") or "").strip().lower() in ("0", "false", "no", "off")


def _gateway_launch_stamp_path():
    from pathlib import Path
    root = os.environ.get("TOMAHAWK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return Path(root) / "gateway_launch.json"


def _tasklist_gateway_matches(listing: str, pid: Any = None, pid_image: str | None = None) -> list[str]:
    """Gateway/TWS rows in `tasklist /V /FO CSV /NH` output: by image name, window title or launched PID."""
    import csv
    import io
    hits = []
    for row in csv.reader(io.StringIO(listing or "")):
        if not row:
            continue
        image = row[0].strip()
        title = row[-1].strip() if len(row) >= 9 else ""
        by_image = image.lower() in _GATEWAY_IMAGES or bool(_GATEWAY_IMAGE_RE.match(image))
        by_title = (bool(title) and title.upper() != "N/A" and image.lower() not in _TITLE_IGNORED_IMAGES
                    and bool(_GATEWAY_TITLE_RE.search(title)))
        # A recorded PID only counts while it still belongs to the program we started (PIDs get reused).
        by_pid = (pid is not None and len(row) > 1 and row[1].strip() == str(pid)
                  and bool(pid_image) and image.lower() == str(pid_image).lower())
        if by_image or by_title or by_pid:
            hits.append(image or "Gateway window")
    return hits


def _gateway_process_snapshot_matches(snapshot, stamp=None) -> list[str] | None:
    """Validate a complete Windows snapshot before treating no matches as absent."""
    if (not isinstance(snapshot, dict) or snapshot.get("ok") is not True
            or not isinstance(snapshot.get("processes"), list) or not snapshot["processes"]
            or not isinstance(snapshot.get("java"), list)):
        return None
    stamp = stamp or {}
    hits = []
    for row in snapshot["processes"]:
        if (not isinstance(row, dict) or not isinstance(row.get("ProcessName"), str)
                or not row["ProcessName"] or type(row.get("Id")) is not int
                or "MainWindowTitle" not in row):
            return None
        name = row["ProcessName"] + ".exe"
        title = row.get("MainWindowTitle") or ""
        if not isinstance(title, str):
            return None
        by_image = bool(_GATEWAY_IMAGE_RE.match(name))
        by_title = name.lower() not in _TITLE_IGNORED_IMAGES and bool(_GATEWAY_TITLE_RE.search(title))
        by_pid = (row["Id"] == stamp.get("pid") and name.lower() == str(stamp.get("pid_image") or "").lower())
        if by_image or by_title or by_pid:
            hits.append(name)
    # An unreadable JVM could be a headless Gateway. Never call that absent.
    for row in snapshot["java"]:
        if (not isinstance(row, dict) or not isinstance(row.get("Name"), str)
                or not isinstance(row.get("CommandLine"), str) or not row["CommandLine"].strip()):
            return hits or None
        hits.extend(_java_gateway_matches(row["Name"] + "|" + row["CommandLine"]))
    return list(dict.fromkeys(hits))


def _running_gateway_processes() -> list[str] | None:
    """Names of IB Gateway / TWS processes already running, or None when Windows could not be asked.

    Gateway only opens its API port after sign-in, so "port closed" alone must never trigger
    another launch while a login window is up. Three independent checks: process image name,
    window title ("IBKR Gateway", "IB Gateway", "Trader Workstation") and the PID this desk last
    started; then Gateway/TWS JVMs (IBC, launchers that re-execute Java) by command line.
    A check that fails or times out returns None, which callers treat as "maybe running".
    """
    if os.name != "nt":
        return []
    import subprocess
    import json
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    # tasklist /V can hang while querying an unrelated window. Get-Process uses
    # the process APIs and retains the image/title/PID checks; CIM is limited to
    # JVM command lines. A complete structured response is required for absence.
    query = ("$ErrorActionPreference='Stop'; "
             "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
             "$rows=@(Get-Process -ErrorAction Stop | Select-Object Id,ProcessName,MainWindowTitle); "
             "$jvm=@(Get-CimInstance Win32_Process -Filter \"Name='java.exe' OR Name='javaw.exe'\" "
             "-ErrorAction Stop | Select-Object Name,CommandLine); "
             "@{ok=$true; processes=$rows; java=$jvm} | ConvertTo-Json -Depth 4 -Compress")
    try:
        proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=12, creationflags=flags)
        if proc.returncode != 0:
            return None
        return _gateway_process_snapshot_matches(json.loads(proc.stdout), _read_gateway_stamp())
    except Exception:
        return None


def _java_gateway_matches(listing: str) -> list[str]:
    """Pick Gateway/TWS JVMs out of 'name|command line' rows."""
    hits = []
    for line in (listing or "").splitlines():
        name, _, command = line.partition("|")
        low = command.lower()
        if any(mark in low for mark in ("ibgateway", "ibcalpha", "\\jts\\", "/jts/", "jclient", "twslaunch")):
            hits.append(name.strip() or "java.exe")
    return hits


def _read_gateway_stamp() -> dict[str, Any]:
    import json as _json
    try:
        data = _json.loads(_gateway_launch_stamp_path().read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_gateway_stamp(data: dict[str, Any]) -> None:
    import json as _json
    path = _gateway_launch_stamp_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def _stamp_time(data: dict[str, Any], key: str) -> float:
    try:
        return float(data.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _stamp_times(data: dict[str, Any], key: str, window: float, now: float) -> list[float]:
    out = []
    for value in data.get(key) or []:
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if 0 <= now - stamp < window:
            out.append(stamp)
    return out


def _recent_gateway_launch(now: float | None = None) -> float | None:
    """Seconds since the last launch by ANY desk component (shared stamp file)."""
    stamp = _stamp_time(_read_gateway_stamp(), "at")
    if not stamp:
        return None
    age = (now or time.time()) - stamp
    return age if 0 <= age < GATEWAY_LAUNCH_COOLDOWN_SEC else None


def _launch_budget_blocker(automatic: bool, now: float) -> str | None:
    data = _read_gateway_stamp()
    limit, window = GATEWAY_LAUNCH_BUDGET
    if len(_stamp_times(data, "launches", window, now)) >= limit:
        return (f"IB Gateway was already started {limit} times in the last {int(window // 60)} minutes; "
                "not starting another. Close any extra Gateway windows and sign in to one of them.")
    limit, window = GATEWAY_AUTOMATIC_BUDGET
    if automatic and len(_stamp_times(data, "automatic_launches", window, now)) >= limit:
        return (f"The desk already reopened IB Gateway {limit} times today; it will not reopen it again "
                "automatically. Use Ensure Gateway or start Gateway yourself.")
    return None


def _mark_gateway_launch(exe: str, source: str, automatic: bool = False, now: float | None = None) -> None:
    # A new launch has not served the API yet; api_seen_at stays as history.
    now = now or time.time()
    data = _read_gateway_stamp()
    data.update(at=now, exe=exe, source=source, pid=None)
    data["launches"] = _stamp_times(data, "launches", 86400.0, now) + [now]
    if automatic:
        data["automatic_launches"] = _stamp_times(data, "automatic_launches", 86400.0, now) + [now]
    _write_gateway_stamp(data)


def _note_gateway_pid(pid: int, exe: str | None = None) -> None:
    data = _read_gateway_stamp()
    data["pid"] = int(pid)
    data["pid_image"] = os.path.basename(exe or data.get("exe") or "").lower() or None
    _write_gateway_stamp(data)


def _note_gateway_api_up(now: float | None = None) -> None:
    """Record that Gateway served the API (signed in).

    Writes when a launch has not been confirmed yet, else at most once a minute.
    """
    now = now or time.time()
    _GATEWAY_ABSENT_SINCE[0] = None
    data = _read_gateway_stamp()
    seen = _stamp_time(data, "api_seen_at")
    if seen >= _stamp_time(data, "at") and now - seen < 60:
        return
    data["api_seen_at"] = now
    _write_gateway_stamp(data)


def _gateway_hours(now: float | None = None) -> bool:
    """Unattended relaunches only make sense from 9:00 ET to the close on a trading day."""
    try:
        import paper_loop
        moment = datetime.fromtimestamp(now or time.time(), timezone.utc).astimezone(paper_loop.NY_TZ)
        close = paper_loop.session_close_time(moment.date())
    except Exception:  # noqa: BLE001 - unknown hours never start Gateway unattended
        return False
    if close is None:
        return False
    clock = moment.time()
    return clock >= dtime(9, 0) and clock < close


def _automatic_launch_blocker(now: float) -> str | None:
    """Unattended relaunch policy: never reopen a Gateway the owner closed.

    Automatic callers (live agent tick, upkeep watchdog) may relaunch only a
    Gateway that signed in and served the API after its last launch, i.e. one
    that went away unexpectedly, during trading hours. A login window that was
    closed, or that exited without signing in, stays closed until the owner
    opens it (desk button or desktop shortcut).
    """
    data = _read_gateway_stamp()
    launched, seen = _stamp_time(data, "at"), _stamp_time(data, "api_seen_at")
    if launched and seen < launched:
        return ("IB Gateway was closed before it signed in; not reopening it automatically. "
                "Use Ensure Gateway on the desk or the desktop shortcut when you want to sign in.")
    if not _gateway_hours(now):
        return "Outside trading hours; the desk does not reopen IB Gateway on its own."
    absent_since = _GATEWAY_ABSENT_SINCE[0]
    if absent_since is None:
        _GATEWAY_ABSENT_SINCE[0] = now
        absent_since = now
    waited = now - absent_since
    if waited < GATEWAY_ABSENT_DEBOUNCE_SEC:
        return (f"IB Gateway is not running; confirming for {int(GATEWAY_ABSENT_DEBOUNCE_SEC - waited)}s more "
                "before an automatic relaunch (Gateway may be restarting itself).")
    return None


class _CrossProcessLock:
    """One Gateway launch at a time across the desk, the launcher and the daily task.

    Windows: the named mutex Start-Tomahawk.ps1 and Ensure-IBGateway.ps1 also take.
    Elsewhere (tests, development): an exclusive lock file next to the stamp.
    """

    def __init__(self) -> None:
        self.handle = None
        self.path = None

    def __enter__(self) -> bool:
        if os.name == "nt":
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateMutexW(None, False, GATEWAY_MUTEX_NAME)
                if not handle:
                    return False
                state = kernel32.WaitForSingleObject(handle, 0)
                if state in (0, 0x80):  # WAIT_OBJECT_0, WAIT_ABANDONED
                    self.handle = handle
                    return True
                kernel32.CloseHandle(handle)
                return False
            except Exception:  # noqa: BLE001
                return False
        self.path = _gateway_launch_stamp_path().with_suffix(".lock")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and time.time() - self.path.stat().st_mtime > 120:
                self.path.unlink()  # stale lock from a crashed process
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return True
        except OSError:
            self.path = None
            return False

    def __exit__(self, *exc) -> None:
        if self.handle is not None:
            try:
                import ctypes
                ctypes.windll.kernel32.ReleaseMutex(self.handle)
                ctypes.windll.kernel32.CloseHandle(self.handle)
            except Exception:  # noqa: BLE001
                pass
        if self.path is not None:
            try:
                self.path.unlink()
            except OSError:
                pass


_GATEWAY_LAUNCH_LOCK = threading.Lock()


def ensure_gateway(*, launch_if_down: bool = True, automatic: bool = False) -> dict[str, Any]:
    """Ensure IB Gateway API port is reachable; optionally relaunch ibgateway.exe.

    ``automatic`` marks unattended callers (live agent tick, upkeep watchdog).
    They relaunch only a Gateway that had signed in and then went away, during
    trading hours, after it has been absent for GATEWAY_ABSENT_DEBOUNCE_SEC; see
    _automatic_launch_blocker. Every launch, owner or automatic: never while a
    Gateway/TWS process or window exists, never when that cannot be checked,
    one at a time across processes, not inside the cooldown, and within the
    launch budget. IB_GATEWAY_AUTOLAUNCH=0 turns desk launching off entirely.

    Never places orders. Never disables 2FA. Login / IB Key may still be required
    if the Gateway session was fully logged out (not a soft API bounce).
    """
    host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_GATEWAY_PORT", "4002"))
    result: dict[str, Any] = {
        "ok": False,
        "host": host,
        "port": port,
        "port_open": False,
        "launched": False,
        "exe": None,
        "gateway_restarted": False,
        "human_2fa_may_be_required": False,
        "note": "",
    }
    if _port_open(host, port):
        _note_gateway_api_up()
        result.update(ok=True, port_open=True, note="Gateway API port is reachable.")
        return result
    if host not in ("127.0.0.1", "localhost", "::1"):
        result["note"] = f"Remote Gateway {host}:{port} is down; start it on that host."
        result["human_2fa_may_be_required"] = True
        return result
    if not launch_if_down:
        result["note"] = f"Gateway port {port} is down; launch_if_down=false."
        return result
    if _gateway_autolaunch_disabled():
        result.update(autolaunch_disabled=True,
                      note="Desk Gateway launching is off (IB_GATEWAY_AUTOLAUNCH=0). Start IB Gateway yourself.")
        return result
    now = time.time()
    if automatic:
        last = _GATEWAY_LAST_AUTOMATIC
        if last["result"] is not None and now - last["at"] < GATEWAY_AUTOMATIC_CHECK_SEC:
            return dict(last["result"])
    candidates = _gateway_exe_candidates()
    exe = next((p for p in candidates if os.path.isfile(p)), None)
    if not exe:
        result["note"] = "ibgateway.exe not found. Set IB_GATEWAY_EXE or install IB Gateway."
        return result
    result["exe"] = exe
    # One Gateway at a time: never stack login windows (multi-launch bug).
    if not _GATEWAY_LAUNCH_LOCK.acquire(blocking=False):
        result["note"] = "Another Gateway launch check is in progress."
        return result
    try:
        outcome = _ensure_gateway_locked(exe, host, port, result, automatic)
    finally:
        _GATEWAY_LAUNCH_LOCK.release()
    if automatic:
        _GATEWAY_LAST_AUTOMATIC.update(at=time.time(), result=dict(outcome))
    return outcome


def _ensure_gateway_locked(exe: str, host: str, port: int, result: dict[str, Any], automatic: bool) -> dict[str, Any]:
    now = time.time()
    if automatic:
        data = _read_gateway_stamp()
        launched, seen = _stamp_time(data, "at"), _stamp_time(data, "api_seen_at")
        if (launched and seen < launched) or not _gateway_hours(now):  # cheap checks first: no scan every tick
            result.update(automatic_launch_held=True, note=_automatic_launch_blocker(now))
            return result
    running = _running_gateway_processes()
    if running is None:
        result.update(detection_failed=True, human_2fa_may_be_required=True,
                      note="Couldn't check whether IB Gateway is already running, so the desk will not start "
                           "another copy. Sign in to the open Gateway window, or start Gateway yourself.")
        return result
    if running:
        _GATEWAY_ABSENT_SINCE[0] = None
        result.update(already_running=running, human_2fa_may_be_required=True,
                      note=f"{running[0]} is already running; complete sign-in or enable its API on port {port}. "
                           "Not launching another copy.")
        return result
    if automatic:
        blocker = _automatic_launch_blocker(now)
        if blocker:
            result.update(automatic_launch_held=True, note=blocker)
            return result
    recent = _recent_gateway_launch(now)
    if recent is not None:
        result["note"] = (f"Gateway was launched {int(recent)}s ago; waiting for sign-in instead of "
                          f"launching another copy (cooldown {int(GATEWAY_LAUNCH_COOLDOWN_SEC)}s).")
        return result
    budget = _launch_budget_blocker(automatic, now)
    if budget:
        result.update(launch_budget_reached=True, note=budget)
        return result
    with _CrossProcessLock() as held:
        if not held:
            result["note"] = "Another program on this PC is starting IB Gateway right now; not starting a second copy."
            return result
        if _recent_gateway_launch() is not None:  # launched by another process while we checked
            result["note"] = "IB Gateway was just started by another desk component; waiting for sign-in."
            return result
        _mark_gateway_launch(exe, "desk-auto" if automatic else "desk", automatic, now)
        _GATEWAY_ABSENT_SINCE[0] = None
        return _launch_gateway(exe, host, port, result)


def _launch_gateway(exe: str, host: str, port: int, result: dict[str, Any]) -> dict[str, Any]:
    try:
        import subprocess
        proc = subprocess.Popen(
            [exe],
            cwd=os.path.dirname(exe),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            _note_gateway_pid(proc.pid, exe)
        except Exception:  # noqa: BLE001
            pass
        result["launched"] = True
        result["gateway_restarted"] = True  # process relaunch; session may still need login
        result["human_2fa_may_be_required"] = True
        result["note"] = (
            "Started ibgateway.exe because the API port was down. "
            "If Gateway still has a cached session, login may complete without 2FA; "
            "a full logout always requires human IB Key / 2FA. Soft API reconnect is preferred when the port is up."
        )
        # Brief wait then re-check (login window may still be up).
        time.sleep(1.5)
        result["port_open"] = _port_open(host, port)
        result["ok"] = result["port_open"]
        if not result["ok"]:
            result["note"] += " Port not open yet — complete Gateway sign-in if prompted."
    except Exception as exc:
        result["note"] = f"Failed to launch Gateway: {type(exc).__name__}: {str(exc)[:160]}"
    return result
