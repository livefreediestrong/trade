"""Safe, optional Interactive Brokers Gateway adapter.

IBKR is always local-Gateway only.  A profile mismatch, unavailable gateway, or
ambiguous terminal order state fails closed; no retry ever repeats an order.
"""
from __future__ import annotations

import os
import threading
import time
from itertools import count
from typing import Any

from broker_capabilities import readiness

_CLIENT_IDS = count(0)
_CLIENT_IDS_LOCK = threading.Lock()
_INFLIGHT: dict[str, dict[str, Any]] = {}
_INFLIGHT_LOCK = threading.Lock()
_TERMINAL = {"filled", "cancelled", "inactive", "apicancelled"}


def _live() -> bool:
    return (os.environ.get("IBKR_LIVE", "false") or "false").strip().lower() in ("1", "true", "yes", "on")


def is_configured() -> bool:
    return (os.environ.get("BROKER_PROVIDER", "alpaca") or "alpaca").strip().lower() == "ibkr"


def paper_mode() -> bool:
    return not _live()


def _profile() -> dict[str, Any]:
    host = (os.environ.get("IB_GATEWAY_HOST", "127.0.0.1") or "").strip()
    raw_port = (os.environ.get("IB_GATEWAY_PORT", "4002") or "").strip()
    try:
        port = int(raw_port)
    except ValueError:
        port = 0
    account = (os.environ.get("IB_MANAGED_ACCOUNT") or "").strip() or None
    errors: list[dict[str, str]] = []
    if host not in {"127.0.0.1", "localhost", "::1"}:
        errors.append({"code": "gateway_host_not_local", "message": "IB Gateway must run on localhost; remote Gateway profiles are refused."})
    expected_port = 4001 if _live() else 4002
    if port != expected_port:
        errors.append({"code": "gateway_environment_mismatch", "message": f"IBKR_LIVE={'true' if _live() else 'false'} requires Gateway port {expected_port}, not {raw_port or 'unset'}."})
    return {"host": host, "port": port, "selected_account": account, "valid": not errors, "diagnostics": errors}


def _new_ib():
    try:
        from ib_insync import IB
    except ImportError as exc:
        raise RuntimeError("ib_insync is not installed; install the optional IBKR adapter dependency") from exc
    return IB()


def _connect(*, retries: int = 3):
    profile = _profile()
    if not profile["valid"]:
        raise RuntimeError(profile["diagnostics"][0]["message"])
    base_id = int(os.environ.get("IB_CLIENT_ID", "37"))
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        ib = _new_ib()
        with _CLIENT_IDS_LOCK:
            client_id = base_id + (next(_CLIENT_IDS) % 100)
        try:
            ib.connect(profile["host"], profile["port"], clientId=client_id, timeout=5)
            return ib
        except Exception as exc:
            last_error = exc
            try:
                ib.disconnect()
            except Exception:
                pass
            if attempt + 1 < retries:
                time.sleep(0.25 * (2 ** attempt))
    raise RuntimeError(f"Unable to connect to IB Gateway after {retries} attempts: {last_error}")


def _disconnect(ib) -> None:
    try:
        ib.disconnect()
    except Exception:
        pass


def _managed_accounts(ib) -> list[str]:
    return sorted({str(account) for account in (ib.managedAccounts() or []) if str(account).strip()})


def _selected_account(ib) -> tuple[str | None, list[str]]:
    accounts = _managed_accounts(ib)
    selected = _profile()["selected_account"]
    if selected and selected not in accounts:
        raise RuntimeError(f"Configured IB_MANAGED_ACCOUNT {selected} is not available from this Gateway.")
    if not selected and len(accounts) == 1:
        selected = accounts[0]
    if not selected and len(accounts) > 1:
        raise RuntimeError("Multiple managed accounts found; set IB_MANAGED_ACCOUNT explicitly before submitting orders.")
    return selected, accounts


def connection_diagnostics() -> dict[str, Any]:
    """Probe the local Gateway and account selection without submitting orders."""
    profile = _profile()
    if not is_configured():
        return {"ok": False, "connected": False, "profile": profile, "diagnostics": [{"code": "not_selected", "message": "Set BROKER_PROVIDER=ibkr to use IB Gateway."}]}
    if not profile["valid"]:
        return {"ok": False, "connected": False, "profile": profile, "diagnostics": profile["diagnostics"]}
    try:
        ib = _connect()
        try:
            selected, accounts = _selected_account(ib)
            return {"ok": True, "connected": True, "profile": profile, "managed_accounts": accounts, "selected_account": selected, "diagnostics": []}
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"ok": False, "connected": False, "profile": profile, "diagnostics": [{"code": "gateway_unreachable", "message": str(exc)[:240]}]}


def public_status() -> dict[str, Any]:
    profile = _profile()
    base = {
        "broker": "ibkr",
        "configured": is_configured(),
        "paper_mode": paper_mode(),
        "endpoint": f"{profile['host']}:{profile['port']}",
        "profile_valid": profile["valid"],
        "selected_account": profile["selected_account"],
        "status": "paper" if paper_mode() else "live",
        "connected_label": "IB Gateway paper" if paper_mode() else "IB Gateway LIVE (real money)",
        "ui_badge": "IBKR PAPER" if paper_mode() else "IBKR LIVE",
        "masthead": "PAPER ONLY" if paper_mode() else "LIVE ENDPOINT",
        "masthead_title": "IB Gateway paper account" if paper_mode() else "IB Gateway live account — real money",
    }
    base.update(readiness(broker="ibkr", configured=base["configured"], paper_mode=base["paper_mode"], profile_valid=profile["valid"], connected=None, selected_account=profile["selected_account"], diagnostics=profile["diagnostics"]))
    return base


def get_account() -> dict[str, Any]:
    try:
        ib = _connect()
        try:
            selected, accounts = _selected_account(ib)
            values = {str(x.tag): x.value for x in ib.accountSummary(account=selected) if not selected or str(x.account) == selected}
            return {"ok": True, "account": values, "account_id": selected, "managed_accounts": accounts, "paper_mode": paper_mode()}
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240], "account": {}, "paper_mode": paper_mode()}


def get_positions() -> dict[str, Any]:
    try:
        ib = _connect()
        try:
            selected, _ = _selected_account(ib)
            rows = [{"symbol": getattr(p.contract, "symbol", ""), "qty": float(p.position), "side": "long" if float(p.position) >= 0 else "short", "avg_entry_price": float(p.avgCost or 0), "current_price": None, "unrealized_pl": None} for p in ib.positions(account=selected) if not selected or str(p.account) == selected]
            return {"ok": True, "positions": rows, "account_id": selected, "paper_mode": paper_mode()}
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240], "positions": [], "paper_mode": paper_mode()}


def place_from_desk_order(order: dict[str, Any]) -> dict[str, Any]:
    """Submit once. Retries apply only to connecting, never to placement."""
    key = str(order.get("client_order_id") or order.get("signal_id") or "").strip()
    if not key:
        return {"ok": False, "status": "error", "broker": "ibkr", "paper_mode": paper_mode(), "error": "client_order_id or signal_id is required for idempotent IBKR submission"}
    with _INFLIGHT_LOCK:
        previous = _INFLIGHT.get(key)
        if previous:
            return {**previous, "idempotent_replay": True}
    try:
        from ib_insync import MarketOrder, Stock
        ib = _connect()
        try:
            account, _ = _selected_account(ib)
            ticker = str(order.get("ticker") or "").upper()
            shares = int(order.get("shares") or 0)
            if not ticker or shares <= 0:
                raise RuntimeError("ticker and positive whole shares are required")
            contract = Stock(ticker, "SMART", "USD")
            ib.qualifyContracts(contract)
            action = "BUY" if str(order.get("side") or "").lower() == "buy" else "SELL"
            ib_order = MarketOrder(action, shares, account=account, orderRef=key[:64])
            trade = ib.placeOrder(contract, ib_order)
            ib.sleep(0.2)
            result = {"ok": True, "status": "live_submitted" if _live() else "paper_submitted", "broker": "ibkr", "order_id": str(trade.order.orderId), "client_order_id": key, "paper_mode": paper_mode(), "endpoint": public_status()["endpoint"], "account_id": account, "order": order}
            with _INFLIGHT_LOCK:
                _INFLIGHT[key] = result
            return result
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"ok": False, "status": "error", "broker": "ibkr", "paper_mode": paper_mode(), "error": str(exc)[:300], "order": order}


def wait_for_fill(order_id: str, timeout: float = 20, interval: float = 0.25) -> dict[str, Any]:
    try:
        ib = _connect()
        try:
            deadline = time.monotonic() + max(0.0, timeout)
            last: dict[str, Any] | None = None
            while True:
                ib.reqAllOpenOrders()
                trades = list(ib.openTrades())
                try:
                    trades.extend(ib.reqCompletedOrders(apiOnly=False) or [])
                except Exception:
                    pass
                for trade in trades:
                    if str(trade.order.orderId) != str(order_id):
                        continue
                    status = str(trade.orderStatus.status or "").lower()
                    filled = float(trade.orderStatus.filled or 0)
                    last = {"state": "filled" if status == "filled" else ("partially_filled" if filled > 0 else ("failed" if status in _TERMINAL else "pending")), "filled_qty": filled, "filled_avg_price": trade.orderStatus.avgFillPrice, "broker_status": status, "alpaca_status": status, "order_id": order_id, "terminal": status in _TERMINAL}
                    if last["terminal"]:
                        return last
                if time.monotonic() >= deadline:
                    return last or {"state": "unknown", "broker_status": "not_found", "alpaca_status": "not_found", "order_id": order_id, "terminal": False}
                ib.sleep(max(0.05, interval))
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)[:240], "order_id": order_id, "terminal": False}


def reconcile_after_timeout(order_id: str, timeout: float = 2.0) -> dict[str, Any]:
    """Re-read to a terminal state; ambiguity remains unknown/pending."""
    return wait_for_fill(order_id, timeout=timeout, interval=0.2)


def flatten_broker() -> dict[str, Any]:
    """Cancel visible open orders, flatten the selected account, and verify zero exposure."""
    try:
        from ib_insync import MarketOrder
        ib = _connect()
        try:
            account, _ = _selected_account(ib)
            ib.reqAllOpenOrders()
            for trade in ib.openTrades():
                if not account or str(getattr(trade.order, "account", "") or account) == account:
                    ib.cancelOrder(trade.order)
            ib.sleep(0.25)
            for position in ib.positions(account=account):
                qty = float(position.position)
                if qty:
                    ib.placeOrder(position.contract, MarketOrder("SELL" if qty > 0 else "BUY", abs(int(qty)), account=account))
            ib.sleep(0.5)
            remaining = [p for p in ib.positions(account=account) if abs(float(p.position)) > 0]
            open_orders = [t for t in ib.openTrades() if str(t.orderStatus.status or "").lower() not in _TERMINAL]
            ok = not remaining and not open_orders
            return {"ok": ok, "status": "flattened" if ok else "verification_failed", "attempted": True, "verified": ok, "remaining_positions": [{"symbol": p.contract.symbol, "qty": float(p.position)} for p in remaining], "open_orders": [str(t.order.orderId) for t in open_orders], "paper_mode": paper_mode(), "account_id": account}
        finally:
            _disconnect(ib)
    except Exception as exc:
        return {"ok": False, "status": "error", "attempted": True, "verified": False, "error": str(exc)[:300], "paper_mode": paper_mode()}
