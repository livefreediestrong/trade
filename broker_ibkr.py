"""IB Gateway adapter with selected-account validation and durable order identity."""
from __future__ import annotations

import asyncio
import atexit
import functools
import math
import os
import queue
import re
import threading
import time
from concurrent.futures import Future
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# A single owner thread keeps asyncio and the Gateway socket together. All reads
# and order operations serialize on that thread and retain the same client ID.
_API_LOCK = threading.RLock()
_VERIFIED: dict[str, Any] = {}
_DEAD = {"cancelled", "canceled", "apicancelled", "inactive"}
_TASKS = queue.Queue()
_WORKER = None
_WORKER_LOCK = threading.Lock()
_STOP = threading.Event()
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


def _disconnected():
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
    _ACCOUNT_READY[row.account] = ready
    if not ready:
        _PNL_UPDATED.pop(row.account, None)


def _api_responsive(ib):
    """A separate Gateway round trip; never manufacture a P&L callback."""
    if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
        return False
    if _API_PULSE.get("client") == id(ib) and time.monotonic() - _API_PULSE["at"] <= 15:
        return True
    try:
        if ib.reqCurrentTime() is None:
            raise ValueError("Gateway returned no current-time response")
        if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED or not ib.isConnected():
            return False
        _API_PULSE.update(client=id(ib), at=time.monotonic())
        return True
    except Exception:
        _API_PULSE.clear()
        return False


def _pnl_update(pnl):
    # Only the active account-wide subscription can freshen the risk value.
    # A late callback from a canceled request (or a model-specific request) is
    # not evidence that the replacement subscription is healthy.
    if _SERVER_UNAVAILABLE or _RESYNC_REQUIRED:
        return
    for (_, account), subscription in _PNL.items():
        if (subscription["value"] is pnl and not getattr(pnl, "modelCode", "")
                and getattr(pnl, "account", account) == account and _ACCOUNT_READY.get(account) is not False):
            _PNL_UPDATED[account] = time.monotonic()
            subscription["callbacks"] = subscription.get("callbacks", 0) + 1
            subscription["day"] = _pnl_day()
            _PNL_WATCH.update(ui_status=None, soft_reconnect_count=0, pending_soft_reconnect=False,
                              pending_reason=None, last_error_code=None)


def _server_error(req_id, code, message, *args):
    global _RESYNC_REQUIRED, _SERVER_UNAVAILABLE, _ACCOUNT_UNSUBSCRIBED
    if code in (1100, 2110):
        _SERVER_UNAVAILABLE = True
        _VERIFIED.clear()
        _PNL_UPDATED.clear()
        _API_PULSE.clear()
        _PNL_WATCH.update(last_error_code=code, ui_status="Recovering Daily P&L...")
        _CONNECTION.update(connected=False, error="IBKR server connection lost; waiting for recovery")
    elif code == 1101:
        _SERVER_UNAVAILABLE = False
        _RESYNC_REQUIRED = True
        _VERIFIED.clear()
        _PNL_UPDATED.clear()
        _API_PULSE.clear()
        _PNL_WATCH.update(last_error_code=1101, ui_status="Recovering Daily P&L...")
    elif code == 2100:
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
        _SERVER_UNAVAILABLE = False
        _CONNECTION.update(connected=bool(_CLIENT is not None and _CLIENT.isConnected()), error=None)


def _api_worker():
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        while not _STOP.is_set():
            try:
                fn, args, kwargs, future = _TASKS.get(timeout=0.1)
            except queue.Empty:
                pass
            else:
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(fn(*args, **kwargs))
                    except Exception as exc:
                        future.set_exception(exc)
            # Pump subscriptions even between HTTP requests.
            if _CLIENT is not None:
                try:
                    _CLIENT.sleep(0.05)
                except Exception as exc:
                    _CONNECTION.update(connected=False, error=str(exc)[:200])
                    _VERIFIED.clear()
    finally:
        if _CLIENT is not None:
            _CLIENT.disconnect()
        asyncio.get_event_loop().close()


def _on_api_thread(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        global _WORKER
        if threading.current_thread() is _WORKER:
            return fn(*args, **kwargs)
        with _WORKER_LOCK:
            if _WORKER is None or not _WORKER.is_alive():
                _STOP.clear()
                _WORKER = threading.Thread(target=_api_worker, name="ibkr-api", daemon=True)
                _WORKER.start()
        future = Future()
        _TASKS.put((fn, args, kwargs, future))
        # Never retry a possibly submitted order when the HTTP caller times out.
        return future.result()
    return call


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
    from ib_insync import IB
    client = IB()
    _preserve_commission_pnl(client)
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
        return {"ok": False, "error": str(exc)[:200]}


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


def _preserve_commission_pnl(client):
    """Capture availability before this client's ib_insync wrapper replaces UNSET with zero."""
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


@_on_api_thread
def execution_history() -> dict[str, Any]:
    """Read execution/commission reports; never submit, modify or cancel orders."""
    try:
        from ib_insync import ExecutionFilter
        with _session() as ib:
            identity = _identity(ib)
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
                    "order_id":ex.orderId,"client_id":ex.clientId,"permanent_order_id":ex.permId,
                    "order_ref":ex.orderRef,"commission":optional(commission.commission) if matched else None,
                    "commission_currency":commission.currency if matched else None,
                    "broker_realized_pnl":realized,
                    "broker_realized_pnl_reported":realized is not None if matched else None,
                    "source":"IBKR execution report","verified":True})
            return {"ok":True,"identity":identity,"executions":result,"excluded":excluded,
                    "coverage":"Only executions exposed by this Gateway session/history window; this is not a complete account statement."}
    except Exception as exc:
        return {"ok":False,"error":f"Execution journal unavailable: {type(exc).__name__}","executions":[]}


@_on_api_thread
def stock_quote(symbol: str) -> dict[str, Any]:
    """Short-lived IBKR live/delayed stock quote for agent execution freshness."""
    if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol):
        return {"ok": False, "error": "Invalid US stock/ETF symbol", "fresh": False}
    try:
        from ib_insync import Stock
        with _session() as ib:
            identity = _identity(ib)
            contract = Stock(symbol, "SMART", "USD")
            if not ib.qualifyContracts(contract) or not contract.conId:
                return {"ok": False, "error": "Underlying stock could not be qualified",
                        "symbol": symbol, "fresh": False, "identity": identity}
            started = datetime.now(timezone.utc)
            # Live API subscription is often unpaid (Error 10089); delayed is available.
            # Prefer delayed (3) so agent quotes succeed without fighting PnL/account feeds.
            try:
                ib.reqMarketDataType(3)
            except Exception:
                pass
            ticker = ib.reqMktData(contract, "", False, False)
            try:
                price = None
                stamp = None
                for _ in range(25):
                    ib.sleep(0.15)
                    for candidate in (ticker.last, ticker.marketPrice(), ticker.close, ticker.bid, ticker.ask):
                        try:
                            value = _number(candidate)
                            if value > 0:
                                price = value
                                break
                        except (ValueError, TypeError):
                            continue
                    if price is not None:
                        # Prefer exchange tick time when present; else receipt time for live stream.
                        for tick in reversed(list(ticker.ticks or [])):
                            if getattr(tick, "time", None) is not None and getattr(tick.time, "tzinfo", None):
                                stamp = tick.time
                                break
                        if stamp is None:
                            stamp = datetime.now(timezone.utc)
                        break
                if price is None or stamp is None:
                    return {"ok": False, "error": "No IBKR trade/quote tick yet", "symbol": symbol,
                            "fresh": False, "identity": identity, "source": "IBKR mkt data"}
                age = (datetime.now(timezone.utc) - stamp).total_seconds()
                fresh = -15 <= age <= 120
                # ib_insync stamps ticks with local *receipt* time. For delayed
                # (3) / delayed-frozen (4) data the prices are ~15 min behind the
                # market even though age_sec looks near zero, so callers can
                # prefer a real-time source. Not a block: delayed stays usable.
                data_type = getattr(ticker, "marketDataType", None)
                delayed = data_type in (3, 4)
                return {
                    "ok": True, "symbol": symbol, "price": price, "fresh": fresh,
                    "market_time": stamp.isoformat(), "received_at": datetime.now(timezone.utc).isoformat(),
                    "age_sec": round(age, 1),
                    "source": ("IBKR delayed mkt data" if delayed else
                               "IBKR live mkt data" if data_type in (1, 2) else "IBKR mkt data"),
                    "delayed": delayed,
                    "bid": _safe_num(ticker.bid), "ask": _safe_num(ticker.ask),
                    "last": _safe_num(ticker.last), "market_data_type": data_type,
                    "identity": identity, "con_id": int(contract.conId),
                }
            finally:
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



@_on_api_thread
def option_chain(symbol):
    """Read listed contract parameters, without requesting orders or paid snapshots."""
    if not isinstance(symbol,str) or not re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}",symbol):
        return {"ok":False,"error":"Invalid US stock/ETF symbol"}
    try:
        from ib_insync import Stock
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
        from ib_insync import Option
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
        method = getattr(ib, "reqAccountUpdates", None)
        raw_method = getattr(getattr(ib, "client", None), "reqAccountUpdates", None)
        if not callable(method) or not callable(raw_method):
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
            raw_method(False, account)
            ib.sleep(0.1)
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
        raw = getattr(getattr(ib, "client", None), "reqAccountUpdates", None)
        method = getattr(ib, "reqAccountUpdates", None)
        if callable(method):
            # If already unsubscribed (Warning 2100), skip cancel — just subscribe.
            if not _ACCOUNT_UNSUBSCRIBED and callable(raw):
                try:
                    raw(False, account)
                    actions.append(f"account_cancel:{account}")
                    ib.sleep(0.15)
                except Exception:
                    pass
            method(account)
            actions.append(f"account_subscribe:{account}")
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
        # Pump long enough for an initial DailyPnL callback (on_change may be slow at 0.0).
        # Soft reconnect too early after Warning 2100 causes a reconnect storm.
        for _ in range(80):
            if account in _PNL_UPDATED:
                break
            ib.sleep(0.15)
        _ACCOUNT_UNSUBSCRIBED = False
        _ACCOUNT_RESUBSCRIBE_AFTER = time.monotonic() + 30
        actions.append("resubscribe_done")
    except Exception as exc:
        actions.append(f"resubscribe_failed:{type(exc).__name__}")
        _ACCOUNT_RESUBSCRIBE_AFTER = time.monotonic() + 5
    return actions


def _cancel_all_pnl(ib):
    """Best-effort cancel of account PnL subscriptions; Gateway process untouched."""
    canceled = []
    for (_, account) in list(_PNL):
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
    return float(min(_SOFT_RECONNECT_BACKOFF_MAX, delay))


def _arm_soft_reconnect(reason: str):
    """Drop the API socket only. Does not restart IB Gateway."""
    global _RESYNC_REQUIRED, _CLIENT, _SOFT_RECONNECT_AFTER
    client = _CLIENT
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


@_on_api_thread
def get_account() -> dict[str, Any]:

    try:
        with _session() as ib:
            identity = _identity(ib)
            account = identity["account_id"]
            if _ACCOUNT_UNSUBSCRIBED:
                _resubscribe_account_and_pnl(ib, account)
            rows = [x for x in ib.accountSummary(account) if x.account == account]
            currencies = {x.currency for x in rows if x.tag == "NetLiquidation"}
            if currencies != {"USD"}:
                raise ValueError("IBKR desk requires a USD base-currency account")
            values = {x.tag: x.value for x in rows if x.currency == "USD"}
            equity = _number(values.get("NetLiquidation"))
            if equity <= 0:
                raise ValueError("IBKR equity must be positive")
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
            if (_ACCOUNT_UNSUBSCRIBED or _PNL_WATCH.get("pending_resubscribe")) and now >= _ACCOUNT_RESUBSCRIBE_AFTER:
                actions = _resubscribe_account_and_pnl(ib, account)
                _PNL_WATCH["pending_resubscribe"] = False
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
            if _PNL_WATCH.get("pending_soft_reconnect") and now >= _SOFT_RECONNECT_AFTER:
                reason = _PNL_WATCH.get("pending_reason") or "pending_soft_reconnect"
                _PNL_WATCH["ui_status"] = "Recovering Daily P&L..."
                carried = dict(previous) if previous else {}
                _arm_soft_reconnect(reason)
                ib = _ib()
                identity = _identity(ib)
                account = identity["account_id"]
                rows = [x for x in ib.accountSummary(account) if x.account == account]
                currencies = {x.currency for x in rows if x.tag == "NetLiquidation"}
                if currencies != {"USD"}:
                    raise ValueError("IBKR desk requires a USD base-currency account")
                values = {x.tag: x.value for x in rows if x.currency == "USD"}
                equity = _number(values.get("NetLiquidation"))
                if equity <= 0:
                    raise ValueError("IBKR equity must be positive")
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
            if previous and now - previous["requested_at"] > _PNL_SILENT_RETRY_SEC and not valid_sample:
                # After repeated silent subscriptions, bounce the API client with
                # exponential backoff. Often restores DailyPnL without a Gateway
                # process restart; risk_ready stays false until a real callback.
                # Avoid cancel storms: IB may take >60s to emit the first 0.0 tick.
                retries = previous.get("retries", 0)
                waiting = now - previous.get("started_at", previous["requested_at"])
                if retries >= 2 and waiting >= _PNL_SOFT_RECONNECT_WAIT_SEC and now >= _SOFT_RECONNECT_AFTER:
                    carried = dict(previous)
                    _arm_soft_reconnect("auto_soft_reconnect")
                    ib = _ib()
                    identity = _identity(ib)
                    account = identity["account_id"]
                    rows = [x for x in ib.accountSummary(account) if x.account == account]
                    currencies = {x.currency for x in rows if x.tag == "NetLiquidation"}
                    if currencies != {"USD"}:
                        raise ValueError("IBKR desk requires a USD base-currency account")
                    values = {x.tag: x.value for x in rows if x.currency == "USD"}
                    equity = _number(values.get("NetLiquidation"))
                    if equity <= 0:
                        raise ValueError("IBKR equity must be positive")
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
                if account in _PNL_UPDATED and _PNL[key].get("day") == _pnl_day():
                    try:
                        daily = _number(pnl.dailyPnL)
                    except (ValueError, TypeError):
                        pass
            account_pnl = _account_window_pnl(ib, account)
            responsive = _api_responsive(ib) if daily is not None else False
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
                           "api_response_age_seconds": round(max(0, now - _API_PULSE["at"]), 1) if responsive else None,
                           "last_update_age_seconds": round(age, 1) if age is not None else None,
                           "subscription_age_seconds": round(max(0, now - subscription["requested_at"]), 1),
                           "waiting_seconds": round(max(0, now - subscription.get("started_at", subscription["requested_at"])), 1),
                           "initial_recovery": {k: v for k, v in subscription.get("initial_recovery", {}).items() if k != "positions_at"},
                           "retries": subscription.get("retries", 0)}
            reason = {"waiting": "Gateway has sent no daily P&L update for this subscription",
                      "stale": "A daily P&L callback for the current desk day is required",
                      "recovering": "Gateway connection recovery is pending; a new daily P&L update is required",
                      "unavailable": "The daily P&L value or Gateway API responsiveness could not be verified"}.get(state)
            return {"ok": True, "identity": identity, "paper_mode": identity["paper_mode"],
                    "risk_ready": daily is not None,
                    "risk_error": None if daily is not None else f"IBKR daily P&L unavailable: {reason}; new risk is blocked. The desk retries automatically. If the portfolio-PnL setting is enabled and Gateway has been restarted, compare Client Portal's Daily P&L and report a missing API feed to IBKR.",
                    "pnl_diagnostics": diagnostics,
                    "account": {"id": account, "currency": "USD", "equity": equity,
                                "account_window_pnl": account_pnl,
                                "day_pnl": daily, "cash": _number(values.get("TotalCashValue")),
                                "buying_power": _number(values.get("BuyingPower", 0))}}
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
                # ib_insync avgCost for STK is typically average cost per share.
                rows.append({
                    "symbol": c.symbol,
                    "qty": qty,
                    "side": "long" if qty >= 0 else "short",
                    "avg_entry_price": avg,
                    "current_price": last if last not in (None, 0) else None,
                    "unrealized_pl": upnl,
                })
            return {"ok": True, "positions": rows}
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
            return {"ok": True, "positions": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "positions": []}




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
    from ib_insync import ExecutionFilter
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
        from ib_insync import ExecutionFilter
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
    if authorization["reducing"]:
        return None  # Deliberate exception for an already verified reducing order.
    daily = authorization.get("day_pnl")
    if isinstance(daily, bool) or not isinstance(daily, (int, float)) or not math.isfinite(daily):
        return "Verified daily P&L required before broker submission"
    subscription = _PNL.get((id(ib), account))
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
    from ib_insync import Option, MarketOrder, LimitOrder
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
            positions = [p for p in ib.positions(account) if p.account == account]
            stock_held = sum(_number(p.position) for p in positions
                             if p.contract.secType == "STK"
                             and p.contract.symbol == symbol and p.contract.currency == "USD")
            # Shares already pledged to short calls (held or working) cannot cover
            # another one; otherwise repeated covered calls become naked calls.
            short_calls = sum(-_number(p.position) for p in positions
                              if p.contract.secType == "OPT" and p.contract.symbol == symbol
                              and p.contract.right == "C" and _number(p.position) < 0)
            working_calls = 0.0
            for trade in ib.openTrades():
                c = trade.contract
                if (trade.order.account != account or c.secType != "OPT" or c.symbol != symbol
                        or c.right != "C" or trade.order.action.upper() != "SELL"):
                    continue
                working_calls += max(0.0, _number(trade.order.totalQuantity) - _number(trade.orderStatus.filled))
            pledged = (short_calls + working_calls) * 100
            need = int(qty) * 100
            if stock_held - pledged < need:
                raise ValueError(
                    f"Covered short call needs {need} unpledged long {symbol} shares; broker holds {stock_held}, "
                    f"{pledged:.0f} already cover short or working calls"
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
            values = {v.tag: v.value for v in ib.accountSummary(account)}
            try:
                bp = float(values.get("BuyingPower") or values.get("AvailableFunds") or 0)
            except (TypeError, ValueError):
                bp = 0.0
            if bp < floor:
                raise ValueError(
                    f"Naked STO blocked: buying_power/available_funds {bp:.2f} < worst-case loss floor {floor:.2f}. "
                    "This is not a full IBKR margin calc."
                )
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
    return ib.placeOrder(contract, native)


@_on_api_thread
def _place_bag_from_desk(ib, order, identity, ref):
    """Live BAG vertical: two OPT legs, same underlying/expiry/right, distinct strikes."""
    from ib_insync import Contract, ComboLeg, Option, MarketOrder, LimitOrder
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
        if held.get(int(long_c.conId), 0.0) < qty or held.get(int(short_c.conId), 0.0) > -qty:
            raise ValueError("BAG CLOSE refused: the account does not hold this spread in that quantity")
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
    return ib.placeOrder(bag, native)



@_on_api_thread
def place_from_desk_order(order: dict[str, Any]) -> dict[str, Any]:
    trade = None
    identity = None
    attempted = False
    try:
        from ib_insync import MarketOrder, Stock
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
                    from ib_insync import LimitOrder
                    native = LimitOrder(side.upper(), int(qty), float(order["limit"]),
                                        account=identity["account_id"], orderRef=ref, tif="DAY", outsideRth=False)
                else:
                    native = MarketOrder(side.upper(), int(qty), account=identity["account_id"], orderRef=ref)
                risk_error = _submission_risk_error(ib, order, identity)
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
                  "error": str(exc)[:300], "submission_attempted": attempted, "order": dict(order)}
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
        from ib_insync import Stock, LimitOrder, MarketOrder
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
    return {
        "ui_status": _PNL_WATCH.get("ui_status"),
        "last_error_code": _PNL_WATCH.get("last_error_code"),
        "soft_reconnect_count": int(_PNL_WATCH.get("soft_reconnect_count") or 0),
        "pending_soft_reconnect": bool(_PNL_WATCH.get("pending_soft_reconnect")),
        "pending_reason": _PNL_WATCH.get("pending_reason"),
        "soft_reconnect_after_monotonic": _SOFT_RECONNECT_AFTER,
        "last_backoff_sec": _PNL_WATCH.get("last_backoff_sec"),
        "last_scheduled_refresh_at": _PNL_WATCH.get("last_scheduled_refresh_at"),
        "note": (
            "IBKR 2FA / IB Key cannot be disabled. Soft reconnect and re-reqPnL "
            "run first; full Gateway logout still requires human 2FA."
        ),
    }


def maybe_scheduled_soft_refresh(*, interval_minutes: float, auto_live: bool, risk_ready: bool) -> dict[str, Any] | None:
    """Optional soft refresh while auto_live and not risk_ready. Never invents Ready."""
    if not auto_live or risk_ready:
        return None
    try:
        minutes = float(interval_minutes)
    except (TypeError, ValueError):
        return None
    if minutes <= 0:
        return None
    now = time.monotonic()
    last = float(_PNL_WATCH.get("last_scheduled_refresh_at") or 0.0)
    # First observation only arms the timer — do not soft-reconnect until the
    # interval elapses. Immediate soft reconnect on startup races the initial
    # DailyPnL callback and triggers Warning 2100 storms.
    if not last:
        _PNL_WATCH["last_scheduled_refresh_at"] = now
        return None
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
_GATEWAY_ABSENT_SINCE: list[float | None] = [None]


def _gateway_launch_stamp_path():
    from pathlib import Path
    root = os.environ.get("TOMAHAWK_DATA_DIR") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    return Path(root) / "gateway_launch.json"


def _running_gateway_processes() -> list[str]:
    """Names of IB Gateway / TWS processes already running (any version, any path).

    Gateway only opens its API port after sign-in, so "port closed" alone must
    never trigger another launch while a login window is still up. Gateway
    started by IBC (or a launcher that re-executes the JVM) runs as java.exe /
    javaw.exe, so those count when their command line names Gateway/TWS.
    """
    if os.name != "nt":
        return []
    import subprocess
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    found = []
    for image in _GATEWAY_IMAGES:
        try:
            out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, timeout=8, creationflags=flags).stdout
        except Exception:
            continue
        if image.lower() in out.lower():
            found.append(image)
    if found:
        return found
    query = ("Get-CimInstance Win32_Process -Filter \"Name='java.exe' OR Name='javaw.exe'\" | "
             "ForEach-Object { $_.Name + '|' + $_.CommandLine }")
    try:
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query],
                             capture_output=True, text=True, timeout=15, creationflags=flags).stdout
    except Exception:
        return found
    return _java_gateway_matches(out)


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


def _recent_gateway_launch(now: float | None = None) -> float | None:
    """Seconds since the last launch by ANY desk component (shared stamp file)."""
    stamp = _stamp_time(_read_gateway_stamp(), "at")
    if not stamp:
        return None
    age = (now or time.time()) - stamp
    return age if 0 <= age < GATEWAY_LAUNCH_COOLDOWN_SEC else None


def _mark_gateway_launch(exe: str, source: str) -> None:
    # A new launch has not served the API yet; api_seen_at stays as history.
    data = _read_gateway_stamp()
    data.update(at=time.time(), exe=exe, source=source)
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


def _automatic_launch_blocker(now: float) -> str | None:
    """Unattended relaunch policy: never reopen a Gateway the owner closed.

    Automatic callers (live agent tick, upkeep watchdog) may relaunch only a
    Gateway that signed in and served the API after its last launch, i.e. one
    that went away unexpectedly. A login window that was closed, or that exited
    without signing in, stays closed until the owner opens it (desk button or
    desktop shortcut).
    """
    data = _read_gateway_stamp()
    launched, seen = _stamp_time(data, "at"), _stamp_time(data, "api_seen_at")
    if launched and seen < launched:
        return ("IB Gateway was closed before it signed in; not reopening it automatically. "
                "Use Ensure Gateway on the desk or the desktop shortcut when you want to sign in.")
    absent_since = _GATEWAY_ABSENT_SINCE[0]
    if absent_since is None:
        _GATEWAY_ABSENT_SINCE[0] = now
        absent_since = now
    waited = now - absent_since
    if waited < GATEWAY_ABSENT_DEBOUNCE_SEC:
        return (f"IB Gateway is not running; confirming for {int(GATEWAY_ABSENT_DEBOUNCE_SEC - waited)}s more "
                "before an automatic relaunch (Gateway may be restarting itself).")
    return None


_GATEWAY_LAUNCH_LOCK = threading.Lock()


def ensure_gateway(*, launch_if_down: bool = True, automatic: bool = False) -> dict[str, Any]:
    """Ensure IB Gateway API port is reachable; optionally relaunch ibgateway.exe.

    ``automatic`` marks unattended callers (live agent tick, upkeep watchdog).
    They relaunch only a Gateway that had signed in and then went away, after
    it has been absent for GATEWAY_ABSENT_DEBOUNCE_SEC; see
    _automatic_launch_blocker. Owner actions (desk button, launcher) skip that
    policy but still never stack a second Gateway or launch inside the cooldown.

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
        running = _running_gateway_processes()
        if running:
            _GATEWAY_ABSENT_SINCE[0] = None
            result.update(already_running=running, human_2fa_may_be_required=True,
                          note=f"{running[0]} is already running; complete sign-in or enable its API on port {port}. "
                               "Not launching another copy.")
            return result
        if automatic:
            blocker = _automatic_launch_blocker(time.time())
            if blocker:
                result.update(automatic_launch_held=True, note=blocker)
                return result
        recent = _recent_gateway_launch()
        if recent is not None:
            result["note"] = (f"Gateway was launched {int(recent)}s ago; waiting for sign-in instead of "
                              f"launching another copy (cooldown {int(GATEWAY_LAUNCH_COOLDOWN_SEC)}s).")
            return result
        _mark_gateway_launch(exe, "desk-auto" if automatic else "desk")
        _GATEWAY_ABSENT_SINCE[0] = None
        return _launch_gateway(exe, host, port, result)
    finally:
        _GATEWAY_LAUNCH_LOCK.release()


def _launch_gateway(exe: str, host: str, port: int, result: dict[str, Any]) -> dict[str, Any]:
    try:
        import subprocess
        subprocess.Popen(
            [exe],
            cwd=os.path.dirname(exe),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
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

