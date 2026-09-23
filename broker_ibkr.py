"""Optional Interactive Brokers adapter for a local IB Gateway connection.

The adapter is deliberately opt-in. Set BROKER_PROVIDER=ibkr and configure
IB_GATEWAY_PORT (4002 paper, 4001 live) before it can submit anything.
"""
from __future__ import annotations

import os
import time
from typing import Any


def _ib():
    try:
        from ib_insync import IB
    except ImportError as exc:
        raise RuntimeError("ib_insync is not installed") from exc
    client = IB()
    host = os.environ.get("IB_GATEWAY_HOST", "127.0.0.1")
    port = int(os.environ.get("IB_GATEWAY_PORT", "4002"))
    client.connect(host, port, clientId=int(os.environ.get("IB_CLIENT_ID", "37")), timeout=5)
    return client


def _live() -> bool:
    return (os.environ.get("IBKR_LIVE", "false") or "false").strip().lower() in ("1", "true", "yes", "on")


def is_configured() -> bool:
    return (os.environ.get("BROKER_PROVIDER", "alpaca") or "alpaca").strip().lower() == "ibkr"


def paper_mode() -> bool:
    return not _live()


def public_status() -> dict[str, Any]:
    configured = is_configured()
    return {
        "broker": "ibkr",
        "configured": configured,
        "paper_mode": paper_mode(),
        "endpoint": f"{os.environ.get('IB_GATEWAY_HOST', '127.0.0.1')}:{os.environ.get('IB_GATEWAY_PORT', '4002')}",
        "status": "paper" if configured and paper_mode() else ("live" if configured else "not_configured"),
        "connected_label": "IB Gateway paper" if paper_mode() else "IB Gateway LIVE (real money)",
        "ui_badge": "IBKR PAPER" if paper_mode() else "IBKR LIVE",
        "masthead": "PAPER ONLY" if paper_mode() else "LIVE ENDPOINT",
        "masthead_title": "IB Gateway paper account" if paper_mode() else "IB Gateway live account — real money",
    }


def get_account() -> dict[str, Any]:
    try:
        ib = _ib()
        values = {str(x.tag): x.value for x in ib.accountSummary()}
        ib.disconnect()
        return {"ok": True, "account": values}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "account": {}}


def get_positions() -> dict[str, Any]:
    try:
        ib = _ib()
        rows = []
        for p in ib.positions():
            rows.append({
                "symbol": getattr(p.contract, "symbol", ""),
                "qty": float(p.position),
                "side": "long" if float(p.position) >= 0 else "short",
                "avg_entry_price": float(p.avgCost or 0),
            })
        ib.disconnect()
        return {"ok": True, "positions": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "positions": []}


def place_from_desk_order(order: dict[str, Any]) -> dict[str, Any]:
    try:
        from ib_insync import MarketOrder, Stock
        ib = _ib()
        contract = Stock(str(order.get("ticker") or "").upper(), "SMART", "USD")
        ib.qualifyContracts(contract)
        action = "BUY" if str(order.get("side") or "").lower() == "buy" else "SELL"
        trade = ib.placeOrder(contract, MarketOrder(action, int(order.get("shares") or 0)))
        ib.sleep(0.2)
        order_id = str(trade.order.orderId)
        ib.disconnect()
        return {
            "ok": True,
            "status": "live_submitted" if _live() else "paper_submitted",
            "broker": "ibkr",
            "order_id": order_id,
            "paper_mode": paper_mode(),
            "endpoint": public_status()["endpoint"],
            "order": order,
        }
    except Exception as exc:
        return {"ok": False, "status": "error", "broker": "ibkr", "paper_mode": paper_mode(), "error": str(exc)[:300], "order": order}


def wait_for_fill(order_id: str, timeout: float = 20, interval: float = 0.25) -> dict[str, Any]:
    # Gateway order state is queried through open trades; an absent trade is
    # intentionally uncertain rather than reported as a successful fill.
    try:
        ib = _ib()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for trade in ib.openTrades():
                if str(trade.order.orderId) == str(order_id):
                    status = str(trade.orderStatus.status or "").lower()
                    if status == "filled":
                        ib.disconnect()
                        return {"state": "filled", "filled_qty": trade.orderStatus.filled, "filled_avg_price": trade.orderStatus.avgFillPrice, "alpaca_status": status}
                    if status in ("cancelled", "inactive"):
                        ib.disconnect()
                        return {"state": "failed", "alpaca_status": status}
            ib.sleep(interval)
        ib.disconnect()
        return {"state": "pending", "alpaca_status": "pending"}
    except Exception as exc:
        return {"state": "unknown", "error": str(exc)[:200]}


def reconcile_after_timeout(order_id: str, timeout: float = 2.0) -> dict[str, Any]:
    return wait_for_fill(order_id, timeout=timeout)


def flatten_broker() -> dict[str, Any]:
    return {"ok": False, "error": "IBKR flatten requires explicit per-position confirmation"}
