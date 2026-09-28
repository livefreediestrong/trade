"""Read-only, paper-only P&L reproduction. Defaults to a plan without connecting.

Run with an isolated IBKR official SDK or a selected wrapper. This program never
loads the desk config, calls order methods, changes Gateway, or prints account
identifiers/amounts. Each cycle uses one clean client and retains a fixed ID.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


class ProbeError(Exception):
    """Only fixed, non-sensitive messages may be raised with this type."""


class Receipt:
    def __init__(self):
        self.lock = threading.RLock()
        self.request_id = 9001
        self.started = None
        self.active = False
        self.callbacks = self.usable = self.ignored = 0
        self.first_usable_seconds = None
        self.time_replies = 0
        self.errors = []
        self.upstream_available = True

    def arm(self):
        with self.lock:
            self.started = time.monotonic()
            self.active = True  # Before sending: immediate replies are valid.

    def disarm(self):
        with self.lock:
            self.active = False

    def pnl(self, req_id, daily, *_):
        with self.lock:
            if not self.active or req_id != self.request_id:
                self.ignored += 1
                return
            self.callbacks += 1
            valid = isinstance(daily, (float, int)) and math.isfinite(daily) and abs(daily) < 1e100
            if valid:
                self.usable += 1
                if self.first_usable_seconds is None:
                    self.first_usable_seconds = round(time.monotonic() - self.started, 3)

    def current_time(self, *_):
        with self.lock:
            self.time_replies += 1

    def error(self, code):
        with self.lock:
            if len(self.errors) < 50:
                self.errors.append(int(code))
            if code in (1100, 2110):
                self.upstream_available = False
            elif code in (1101, 1102):
                self.upstream_available = True

    def snapshot(self):
        with self.lock:
            return {"pnl_callbacks": self.callbacks, "usable_callbacks": self.usable,
                    "ignored_callbacks": self.ignored, "first_usable_seconds": self.first_usable_seconds,
                    "time_replies": self.time_replies, "error_codes": list(self.errors),
                    "upstream_available": self.upstream_available,
                    "result": "received" if self.usable else "missing",
                    "scope": "Callback evidence only; not trading readiness or proof of recovery."}


def select_paper_account(accounts, requested=""):
    accounts = list(dict.fromkeys(accounts))
    if requested:
        if requested not in accounts or not requested.startswith("DU"):
            raise ProbeError("The requested paper account was not advertised by Gateway.")
        return requested
    if len(accounts) != 1 or not accounts[0].startswith("DU"):
        raise ProbeError("A single paper account is required. Set PNL_PROBE_ACCOUNT for multiple accounts.")
    return accounts[0]


def desk_is_stopped(port):
    """Unknown or responsive desk means no test: do not race its broker owner."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
            raise ProbeError("The desk is running. Use the attended maintenance window after it is stopped normally.")
    except URLError as exc:
        # Only an explicit connection refusal establishes there is no listener.
        reason = exc.reason
        if isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061:
            return
        raise ProbeError("Cannot confirm the desk is stopped; no broker connection was attempted.") from None


def official_client(receipt):
    # The current official SDK is installed separately, never as a desk dependency.
    import ibapi
    if tuple(int(part) for part in ibapi.get_version_string().split(".")) != (10, 50, 2):
        raise ProbeError("This probe requires the separately installed official IBKR API 10.50.2.")
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class Client(EWrapper, EClient):
        def __init__(self):
            EWrapper.__init__(self)
            EClient.__init__(self, self)
            self.handshake = threading.Event()
            self.accounts_ready = threading.Event()
            self.accounts = []

        def nextValidId(self, orderId):
            self.handshake.set()  # Handshake only; never use this ID for an order.

        def managedAccounts(self, accountsList):
            self.accounts = [a for a in accountsList.split(",") if a]
            self.accounts_ready.set()

        def pnl(self, reqId, dailyPnL, unrealizedPnL, realizedPnL):
            receipt.pnl(reqId, dailyPnL, unrealizedPnL, realizedPnL)

        def currentTime(self, stamp):
            receipt.current_time()

        def error(self, reqId, errorTime, errorCode, errorString, advancedOrderRejectJson=""):
            receipt.error(errorCode)  # Official 10.50 signature; never log wire text.

    return Client()


def observe_official(args, receipt):
    import ibapi
    client = official_client(receipt)
    thread = None
    sent = False
    reader_error = []

    def read():
        try:
            client.run()
        except Exception as exc:
            reader_error.append(type(exc).__name__)

    try:
        client.connect("127.0.0.1", args.port, clientId=args.client_id)
        thread = threading.Thread(target=read, daemon=True, name="pnl-probe-reader")
        thread.start()
        if not client.handshake.wait(10) or not client.accounts_ready.wait(10):
            raise ProbeError("Gateway did not complete the account handshake.")
        account = select_paper_account(client.accounts, os.environ.get("PNL_PROBE_ACCOUNT", ""))
        receipt.arm()
        sent = True
        client.reqPnL(receipt.request_id, account, "")
        deadline = time.monotonic() + args.seconds
        next_probe = 0
        while time.monotonic() < deadline and client.isConnected():
            if reader_error:
                raise ProbeError("The diagnostic reader failed; this cycle is incomplete.")
            if time.monotonic() >= next_probe:
                client.reqCurrentTime()
                next_probe = time.monotonic() + 15
            time.sleep(0.1)
        if reader_error:
            raise ProbeError("The diagnostic reader failed; this cycle is incomplete.")
        return {"library_version": ibapi.get_version_string(), "server_version": client.serverVersion(),
                "connected_at_end": client.isConnected(), **receipt.snapshot()}
    finally:
        receipt.disarm()
        try:
            if sent and client.isConnected():
                client.cancelPnL(receipt.request_id)
        finally:
            client.disconnect()
            if thread:
                thread.join(timeout=3)
                if thread.is_alive():
                    raise ProbeError("The diagnostic reader did not finish; further cycles were stopped.")


def observe_wrapper(args, receipt):
    import importlib
    library = importlib.import_module(args.library)
    client = library.IB()
    account = None
    pnl = None

    def callback(value):
        if value is pnl:
            receipt.pnl(receipt.request_id, value.dailyPnL)

    client.pnlEvent += callback
    client.errorEvent += lambda reqId, code, *_: receipt.error(code)
    try:
        # These wrappers fetch initial account/position data; record this difference
        # from the minimal official client instead of claiming identical handshakes.
        client.connect("127.0.0.1", args.port, clientId=args.client_id, timeout=10, readonly=True)
        account = select_paper_account(client.managedAccounts(), os.environ.get("PNL_PROBE_ACCOUNT", ""))
        receipt.arm()
        pnl = client.reqPnL(account)
        deadline = time.monotonic() + args.seconds
        next_probe = 0
        client.RequestTimeout = 5
        while time.monotonic() < deadline and client.isConnected():
            if time.monotonic() >= next_probe:
                if client.reqCurrentTime() is not None:
                    receipt.current_time()
                next_probe = time.monotonic() + 15
            client.sleep(0.1)
        return {"library_version": library.__version__, "server_version": client.client.serverVersion(),
                "connected_at_end": client.isConnected(), **receipt.snapshot()}
    finally:
        receipt.disarm()
        try:
            if pnl is not None and client.isConnected():
                client.cancelPnL(account)
        finally:
            client.disconnect()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--run-paper", action="store_true", help="Run the read-only paper diagnostic during maintenance.")
    result.add_argument("--library", choices=("official", "ib_async", "ib_insync"), default="official")
    result.add_argument("--port", type=int, choices=(4002, 7497), default=4002)
    result.add_argument("--client-id", type=int, default=937)
    result.add_argument("--desk-port", type=int, default=5056)
    result.add_argument("--seconds", type=int, default=180)
    result.add_argument("--cycles", type=int, default=3)
    result.add_argument("--output", type=Path, default=Path("pnl-probe.json"))
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if args.client_id in (0, 37) or not 1 <= args.client_id <= 2147483647:
        raise ProbeError("Use a reserved positive client ID other than the desk's 37.")
    if not 1 <= args.cycles <= 3 or not 10 <= args.seconds <= 600 or not 1 <= args.desk_port <= 65535:
        raise ProbeError("Use 1–3 cycles, 10–600 seconds, and a valid desk port.")
    if not args.run_paper:
        print(json.dumps({"mode": "plan", "connected": False, "library": args.library,
                          "message": "Ready for an attended paper-only maintenance test. Add --run-paper to execute."}))
        return 0
    # Do not overwrite a previous evidence receipt or a user-selected existing file.
    if args.output.exists():
        raise ProbeError("The output already exists; choose a new evidence filename.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)  # Library logs can contain account identifiers.
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "library": args.library,
              "paper_only": True, "observation_seconds": args.seconds, "cycles": [],
              "startup": "official minimal handshake" if args.library == "official" else "wrapper initial account/position fetch"}
    # Reserve the report before any connection. Errors still leave a useful receipt.
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(report, indent=2) + "\n")
    receipt = None
    try:
        for cycle in range(args.cycles):
            receipt = None
            desk_is_stopped(args.desk_port)
            receipt = Receipt()
            observer = observe_official if args.library == "official" else observe_wrapper
            row = {"cycle": cycle + 1, **observer(args, receipt)}
            report["cycles"].append(row)
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(row))
            if cycle + 1 < args.cycles:
                time.sleep(5)
        return 0 if all(row["usable_callbacks"] and row["connected_at_end"] and row["upstream_available"]
                        for row in report["cycles"]) else 3
    except Exception as exc:
        # Never persist raw SDK/transport exceptions, which may include identifiers.
        report["stopped"] = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
        if receipt is not None and len(report["cycles"]) == cycle:
            report["cycles"].append({"cycle": cycle + 1, "completed": False, **receipt.snapshot()})
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        raise ProbeError(report["stopped"]) from None


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as exc:
        print(json.dumps({"stopped": str(exc)}))
        raise SystemExit(2)
