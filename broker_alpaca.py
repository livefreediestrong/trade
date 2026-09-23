"""
Alpaca equity broker adapter (edu / paper-first).

Default: paper-api.alpaca.markets when ALPACA_PAPER is unset/true.
Uses ALPACA_API_KEY + ALPACA_API_SECRET (requests REST; no alpaca-py).

No unlock phrase / kill-switch ceremony — paper submits when keys are present.
ALPACA_PAPER defaults true (paper-api). If ALPACA_PAPER=false, orders hit
api.alpaca.markets (live money) and are journaled as live_submitted.
Desk must gate can_take_trade before submit and must not dual-book local paper
after a successful broker submit.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

PAPER_BASE = "https://paper-api.alpaca.markets"
LIVE_BASE = "https://api.alpaca.markets"
API_PREFIX = "/v2"
DEFAULT_TIMEOUT = 15


def _load_env() -> None:
    try:
        import data_sources as _ds

        _ds._load_env()
    except Exception:
        for env_path in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
            if not env_path.exists():
                continue
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except OSError:
                pass
            break


_load_env()


def _env_truthy(name: str, default: str = "0") -> bool:
    v = (os.environ.get(name, default) or default).strip().lower()
    return v in ("1", "true", "yes", "on")


def _env_falsey_paper() -> bool:
    """ALPACA_PAPER defaults true; only false/0/no/off switches to live base URL."""
    v = (os.environ.get("ALPACA_PAPER", "true") or "true").strip().lower()
    return v in ("0", "false", "no", "off")


def credentials() -> tuple[str, str]:
    key = (os.environ.get("ALPACA_API_KEY") or "").strip()
    secret = (
        (os.environ.get("ALPACA_API_SECRET") or "").strip()
        or (os.environ.get("ALPACA_SECRET_KEY") or "").strip()
    )
    return key, secret


def is_configured() -> bool:
    key, secret = credentials()
    return bool(key and secret)


def paper_mode() -> bool:
    """True unless ALPACA_PAPER explicitly disables paper."""
    return not _env_falsey_paper()


def base_url() -> str:
    return PAPER_BASE if paper_mode() else LIVE_BASE


def public_status() -> dict[str, Any]:
    """Safe status for /api/health and /api/state (never includes secrets)."""
    configured = is_configured()
    pm = paper_mode()
    return {
        "broker": "alpaca",
        "configured": configured,
        "paper_mode": pm,
        "endpoint": base_url() if configured else None,
        "status": (
            "paper"
            if configured and pm
            else ("live" if configured and not pm else "not_configured")
        ),
        "connected_label": (
            "Alpaca paper"
            if configured and pm
            else (
                "Alpaca LIVE endpoint (real money)"
                if configured
                else "Broker not configured"
            )
        ),
        "ui_badge": (
            "ALPACA PAPER"
            if configured and pm
            else ("LIVE ENDPOINT" if configured else "NO BROKER")
        ),
        "masthead": (
            "PAPER ONLY"
            if (not configured) or pm
            else "LIVE ENDPOINT"
        ),
        "masthead_title": (
            "Fake money — local sim and/or Alpaca paper API"
            if (not configured) or pm
            else "LIVE broker endpoint — real money if keys are live"
        ),
    }


def _headers() -> dict[str, str]:
    key, secret = credentials()
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, Any]:
    url = f"{base_url()}{API_PREFIX}{path}"
    try:
        resp = requests.request(
            method.upper(),
            url,
            headers=_headers(),
            json=json_body,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return 0, {"error": f"request_failed: {exc}"}
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": (resp.text or "")[:500]}
    return resp.status_code, data


def get_account() -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "error": "not_configured"}
    code, data = _request("GET", "/account")
    if code == 200 and isinstance(data, dict):
        # Strip nothing sensitive beyond what Alpaca returns; never echo our keys.
        safe = {
            k: data.get(k)
            for k in (
                "id",
                "account_number",
                "status",
                "currency",
                "buying_power",
                "cash",
                "portfolio_value",
                "equity",
                "last_equity",
                "pattern_day_trader",
                "trading_blocked",
                "account_blocked",
                "trade_suspended_by_user",
            )
            if k in data
        }
        return {"ok": True, "account": safe, "paper_mode": paper_mode()}
    return {"ok": False, "http_status": code, "error": data, "paper_mode": paper_mode()}


def get_positions() -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "error": "not_configured", "positions": []}
    code, data = _request("GET", "/positions")
    if code == 200 and isinstance(data, list):
        return {"ok": True, "positions": data, "paper_mode": paper_mode()}
    return {
        "ok": False,
        "http_status": code,
        "error": data,
        "positions": [],
        "paper_mode": paper_mode(),
    }


def cancel_order(order_id: str) -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "status": "live_not_configured", "error": "not_configured"}
    oid = (order_id or "").strip()
    if not oid:
        return {"ok": False, "error": "order_id required"}
    code, data = _request("DELETE", f"/orders/{oid}")
    ok = code in (200, 204)
    return {
        "ok": ok,
        "http_status": code,
        "result": data,
        "paper_mode": paper_mode(),
        "order_id": oid,
    }


TERMINAL_BAD = {"canceled", "cancelled", "expired", "rejected", "suspended", "stopped"}


def _safe_float(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def get_order(order_id: str) -> dict[str, Any]:
    if not is_configured():
        return {"ok": False, "error": "not_configured"}
    oid = (order_id or "").strip()
    if not oid:
        return {"ok": False, "error": "order_id required"}
    code, data = _request("GET", f"/orders/{oid}")
    if code == 200 and isinstance(data, dict):
        return {"ok": True, "order": data}
    return {"ok": False, "http_status": code, "error": data}


def wait_for_fill(order_id: str, *, timeout: float = 6.0, interval: float = 0.5) -> dict[str, Any]:
    """Poll an order until filled / dead / timeout. Never invents a fill price.

    Returns {state: filled|partially_filled|failed|pending|unknown, filled_qty,
    filled_avg_price, alpaca_status}.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    last: dict[str, Any] = {}
    while True:
        res = get_order(order_id)
        if res.get("ok"):
            last = res["order"]
            st = str(last.get("status") or "").lower()
            if st == "filled":
                break
            if st in TERMINAL_BAD:
                break
        if time.monotonic() >= deadline:
            break
        time.sleep(interval)
    st = str(last.get("status") or "").lower()

    def _f(k: str) -> float | None:
        try:
            v = last.get(k)
            return float(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    fq = _f("filled_qty") or 0.0
    if st == "filled":
        state = "filled"
    elif st in TERMINAL_BAD:
        # Could be partially filled before cancel
        state = "partially_filled" if fq > 0 else "failed"
    elif fq > 0:
        state = "partially_filled"
    elif last:
        state = "pending"
    else:
        state = "unknown"
    return {
        "state": state,
        "alpaca_status": st or None,
        "filled_qty": fq,
        "filled_avg_price": _f("filled_avg_price"),
        "order_id": order_id,
    }


def reconcile_after_timeout(order_id: str, *, timeout: float = 2.0) -> dict[str, Any]:
    """Cancel an order that did not finish polling, then return its final state."""
    current = get_order(order_id)
    if current.get("ok"):
        status = str((current.get("order") or {}).get("status") or "").lower()
        if status not in {"filled", *TERMINAL_BAD}:
            cancel_order(order_id)
        final = get_order(order_id)
        if final.get("ok"):
            order = final.get("order") or {}
            filled_qty = _safe_float(order.get("filled_qty")) or 0.0
            final_status = str(order.get("status") or "").lower()
            if final_status == "filled":
                state = "filled"
            elif filled_qty > 0:
                state = "partially_filled"
            elif final_status in TERMINAL_BAD:
                state = "failed"
            else:
                state = "unknown"
            return {
                "state": state,
                "alpaca_status": final_status or None,
                "filled_qty": filled_qty,
                "filled_avg_price": _safe_float(order.get("filled_avg_price")),
                "order_id": order_id,
            }
    return wait_for_fill(order_id, timeout=max(0.0, timeout))


def _normalize_side(side: str) -> str | None:
    s = (side or "").strip().lower()
    if s in ("buy", "long"):
        return "buy"
    if s in ("sell", "short"):
        return "sell"
    return None


def place_equity_order(
    *,
    symbol: str,
    side: str,
    qty: float | int | str | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    client_order_id: str | None = None,
) -> dict[str, Any]:
    """
    Place a market or limit equity order.

    Returns status paper_submitted | live_submitted | live_not_configured | error fields.
    Never fabricates a successful fill when the HTTP call fails or keys are missing.
    """
    if not is_configured():
        return {
            "ok": False,
            "status": "live_not_configured",
            "message": "Alpaca keys missing (ALPACA_API_KEY / ALPACA_API_SECRET).",
            "paper_mode": paper_mode(),
            "broker": "alpaca",
        }

    sym = (symbol or "").strip().upper().replace("/", "")
    side_n = _normalize_side(side or "")
    if not sym or not side_n:
        return {
            "ok": False,
            "status": "error",
            "error": "symbol and side (buy/sell) required",
            "broker": "alpaca",
            "paper_mode": paper_mode(),
        }

    try:
        q = float(qty) if qty is not None else 0.0
    except (TypeError, ValueError):
        q = 0.0
    if q <= 0:
        return {
            "ok": False,
            "status": "error",
            "error": "qty must be > 0",
            "broker": "alpaca",
            "paper_mode": paper_mode(),
        }

    otype = (order_type or "market").strip().lower()
    if otype not in ("market", "limit"):
        otype = "market"

    body: dict[str, Any] = {
        "symbol": sym,
        "qty": str(int(q) if float(q).is_integer() else q),
        "side": side_n,
        "type": otype,
        "time_in_force": (time_in_force or "day").strip().lower() or "day",
    }
    if otype == "limit":
        if limit_price is None:
            return {
                "ok": False,
                "status": "error",
                "error": "limit_price required for limit orders",
                "broker": "alpaca",
                "paper_mode": paper_mode(),
            }
        body["limit_price"] = str(limit_price)
    if client_order_id:
        body["client_order_id"] = str(client_order_id)[:48]

    code, data = _request("POST", "/orders", json_body=body)
    pm = paper_mode()
    submitted_status = "paper_submitted" if pm else "live_submitted"

    if code in (200, 201) and isinstance(data, dict) and data.get("id"):
        return {
            "ok": True,
            "status": submitted_status,
            "broker": "alpaca",
            "paper_mode": pm,
            "endpoint": base_url(),
            "order_id": data.get("id"),
            "client_order_id": data.get("client_order_id"),
            "alpaca_status": data.get("status"),
            "symbol": data.get("symbol") or sym,
            "side": data.get("side") or side_n,
            "qty": data.get("qty") or body["qty"],
            "type": data.get("type") or otype,
            "submitted_at": data.get("submitted_at"),
            "raw": {
                k: data.get(k)
                for k in ("id", "status", "filled_avg_price", "filled_qty", "asset_class")
                if k in data
            },
        }

    return {
        "ok": False,
        "status": "error",
        "broker": "alpaca",
        "paper_mode": pm,
        "endpoint": base_url(),
        "http_status": code,
        "error": data if not isinstance(data, dict) else (data.get("message") or data),
        "message": "Alpaca order rejected or failed — not treated as a fill.",
    }


def place_from_desk_order(order: dict[str, Any]) -> dict[str, Any]:
    """Map desk order dict → place_equity_order."""
    if not isinstance(order, dict):
        return {"ok": False, "status": "error", "error": "order must be a dict"}
    symbol = order.get("ticker") or order.get("symbol") or ""
    side = order.get("side") or ""
    qty = order.get("shares") or order.get("qty") or order.get("quantity")
    limit = order.get("limit") or order.get("limit_price")
    otype = "limit" if limit not in (None, "", 0, "0") and order.get("type") == "limit" else (
        "limit" if order.get("order_type") == "limit" else "market"
    )
    # Desk usually sends market intent with optional limit hint — default market.
    if order.get("type") in ("market", "limit"):
        otype = order["type"]
    elif order.get("order_type") in ("market", "limit"):
        otype = order["order_type"]
    else:
        otype = "market"
    client_id = order.get("client_order_id") or order.get("signal_id")
    result = place_equity_order(
        symbol=str(symbol),
        side=str(side),
        qty=qty,
        order_type=str(otype),
        limit_price=float(limit) if limit not in (None, "") and otype == "limit" else None,
        client_order_id=str(client_id) if client_id else None,
    )
    result["order"] = {
        "ticker": symbol,
        "side": side,
        "shares": qty,
        "via": order.get("via"),
        "signal_id": order.get("signal_id"),
    }
    return result


def cancel_all_orders() -> dict[str, Any]:
    """Cancel all open orders. Safe no-op shape when not configured."""
    if not is_configured():
        return {
            "ok": False,
            "status": "live_not_configured",
            "error": "not_configured",
            "paper_mode": paper_mode(),
        }
    code, data = _request("DELETE", "/orders")
    ok = code in (200, 204)
    return {
        "ok": ok,
        "http_status": code,
        "result": data,
        "paper_mode": paper_mode(),
        "endpoint": base_url(),
    }


def close_position(symbol: str) -> dict[str, Any]:
    """Close one symbol position (market)."""
    if not is_configured():
        return {
            "ok": False,
            "status": "live_not_configured",
            "error": "not_configured",
            "paper_mode": paper_mode(),
        }
    sym = (symbol or "").strip().upper().replace("/", "")
    if not sym:
        return {"ok": False, "error": "symbol required", "paper_mode": paper_mode()}
    code, data = _request("DELETE", f"/positions/{sym}")
    ok = code in (200, 204)
    return {
        "ok": ok,
        "http_status": code,
        "result": data,
        "symbol": sym,
        "paper_mode": paper_mode(),
        "endpoint": base_url(),
    }


def close_all_positions() -> dict[str, Any]:
    """Close all open positions (cancel orders is separate)."""
    if not is_configured():
        return {
            "ok": False,
            "status": "live_not_configured",
            "error": "not_configured",
            "paper_mode": paper_mode(),
            "closed": False,
        }
    code, data = _request("DELETE", "/positions")
    ok = code in (200, 204)
    return {
        "ok": ok,
        "http_status": code,
        "result": data,
        "paper_mode": paper_mode(),
        "endpoint": base_url(),
        "closed": ok,
    }


def flatten_broker() -> dict[str, Any]:
    """Cancel open orders then close all positions. Honest status when not configured."""
    if not is_configured():
        return {
            "ok": False,
            "status": "live_not_configured",
            "error": "not_configured",
            "paper_mode": paper_mode(),
            "attempted": False,
        }
    cancel = cancel_all_orders()
    close = close_all_positions()
    ok = bool(cancel.get("ok")) and bool(close.get("ok"))
    return {
        "ok": ok,
        "status": "flattened" if ok else "partial_or_failed",
        "paper_mode": paper_mode(),
        "endpoint": base_url(),
        "cancel_orders": {
            "ok": cancel.get("ok"),
            "http_status": cancel.get("http_status"),
            "error": cancel.get("error") or cancel.get("result"),
        },
        "close_positions": {
            "ok": close.get("ok"),
            "http_status": close.get("http_status"),
            "error": close.get("error") or close.get("result"),
        },
        "attempted": True,
    }
