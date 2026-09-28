"""Fidelity portfolio: the desk's reading on holdings you keep at Fidelity (docs/FIDELITY.md).

Owner decision (2026-09-28): suggestions only. Fidelity has no retail trading API, so the
desk never connects to, reads from or orders at Fidelity. You import positions (Fidelity's
"Download" CSV or typed lines) and place any trade yourself in Fidelity. Everything lives in
its own file (data/fidelity_portfolio.json) and never touches the IBKR desk, its limits,
Fox or the paper ledger.
"""
from __future__ import annotations

import copy
import csv
import io
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MAX_HOLDINGS = 30
MAX_IDEAS = 5
CACHE_SEC = 300
DEFAULT_SETTINGS = {"risk_pct": 1.0, "max_position_pct": 20.0, "stop_atr": 2.0}
# Money-market / core cash positions Fidelity lists as holdings.
CASH_SYMBOLS = {"SPAXX", "FDRXX", "FZFXX", "SPRXX", "FCASH", "CORE", "CASH", "USD"}
ROUND_TRIP_COST_BPS = 10.0  # $0 commission at Fidelity; spread + slippage only
_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

_lock = threading.RLock()
_cache: dict[str, Any] = {"key": None, "at": 0.0, "result": None}
_running = threading.Lock()


def _path(desk) -> Path:
    return Path(desk.DATA_DIR) / "fidelity_portfolio.json"


def empty() -> dict[str, Any]:
    return {"positions": [], "cash": 0.0, "imported_at": None, "source": None, "accounts": [],
            "settings": dict(DEFAULT_SETTINGS)}


def load(desk) -> dict[str, Any]:
    try:
        raw = json.loads(_path(desk).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty()
    if not isinstance(raw, dict):
        return empty()
    out = empty()
    out.update({k: raw[k] for k in ("imported_at", "source") if k in raw})
    out["accounts"] = [str(a) for a in raw.get("accounts") or []][:10]
    out["cash"] = _num(raw.get("cash")) or 0.0
    out["positions"] = [p for p in (raw.get("positions") or []) if isinstance(p, dict) and _TICKER.match(str(p.get("ticker") or ""))
                        and (_num(p.get("qty")) or 0) > 0][:MAX_HOLDINGS]
    try:
        out["settings"] = validate_settings({**DEFAULT_SETTINGS, **(raw.get("settings") or {})})
    except ValueError:
        pass
    return out


def save(desk, data: dict[str, Any]) -> None:
    path = _path(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)
    with _lock:
        _cache.update(key=None, at=0.0, result=None)


def _num(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
    else:
        text = str(value).strip().replace("$", "").replace(",", "").replace("+", "")
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]
        if text in ("", "--", "-", "n/a", "N/A"):
            return None
        try:
            v = float(text)
        except ValueError:
            return None
    return v if math.isfinite(v) else None


def validate_settings(body: Any) -> dict[str, float]:
    if not isinstance(body, dict) or set(body) - set(DEFAULT_SETTINGS):
        raise ValueError("Use only the displayed sizing settings")
    out = {}
    for key, lo, hi in (("risk_pct", 0.1, 5.0), ("max_position_pct", 1.0, 100.0), ("stop_atr", 0.5, 5.0)):
        v = _num(body.get(key, DEFAULT_SETTINGS[key]))
        if v is None or not lo <= v <= hi:
            raise ValueError(f"{key} must be between {lo:g} and {hi:g}")
        out[key] = round(v, 2)
    return out


_COLUMNS = {
    "symbol": ("symbol",),
    "qty": ("quantity", "qty", "shares"),
    "avg": ("average cost basis", "avg cost", "$ avg", "avg", "average cost", "cost basis per share"),
    "basis": ("cost basis total", "basis", "cost basis"),
    "value": ("current value", "value", "market value"),
    "account": ("account number", "account name", "account"),
}


def _header_map(row: list[str]) -> dict[str, int] | None:
    names = [re.sub(r"\s+", " ", (c or "").strip().lower()) for c in row]
    found: dict[str, int] = {}
    for key, options in _COLUMNS.items():
        for option in options:
            if option in names:
                found[key] = names.index(option)
                break
    return found if "symbol" in found and "qty" in found else None


def _clean_symbol(raw: str) -> str:
    return str(raw or "").strip().upper().rstrip("*").strip()


def _is_cash(symbol: str, description: str = "") -> bool:
    return symbol in CASH_SYMBOLS or symbol.startswith("CASH") or "MONEY MARKET" in description.upper() \
        or symbol in ("PENDING ACTIVITY",)


def parse_import(text: str) -> dict[str, Any]:
    """Fidelity positions CSV (web or Trader+ export) or typed lines: `META 1.39 718.99`, `CASH 4225.70`."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Paste the positions CSV from Fidelity, or type one holding per line")
    if len(text) > 500_000:
        raise ValueError("That file is too large for a positions export")
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    header_at = next((i for i, r in enumerate(rows[:20]) if _header_map(r)), None)
    positions: dict[str, dict[str, Any]] = {}
    cash, accounts, skipped = 0.0, [], []
    if header_at is not None:
        cols = _header_map(rows[header_at])
        source = "Fidelity CSV"
        for r in rows[header_at + 1:]:
            if len(r) <= cols["qty"] or not any(c.strip() for c in r):
                continue
            symbol = _clean_symbol(r[cols["symbol"]])
            if not symbol or symbol.startswith("THE DATA") or len(symbol) > 40:
                continue  # Fidelity's disclaimer footer
            value = _num(r[cols["value"]]) if "value" in cols and len(r) > cols["value"] else None
            if "account" in cols and len(r) > cols["account"] and r[cols["account"]].strip():
                accounts.append(r[cols["account"]].strip())
            if _is_cash(symbol, " ".join(r)):
                cash += value if value is not None else (_num(r[cols["qty"]]) or 0.0)
                continue
            qty = _num(r[cols["qty"]])
            if not _TICKER.match(symbol) or qty is None or qty <= 0:
                skipped.append(symbol)
                continue
            avg = _num(r[cols["avg"]]) if "avg" in cols and len(r) > cols["avg"] else None
            basis = _num(r[cols["basis"]]) if "basis" in cols and len(r) > cols["basis"] else None
            if avg is None and basis is not None and qty:
                avg = basis / qty
            _merge(positions, symbol, qty, avg)
    else:
        source = "typed"
        for line in text.splitlines():
            parts = line.replace(",", " ").split()
            if not parts:
                continue
            symbol = _clean_symbol(parts[0])
            if symbol == "CASH" and len(parts) >= 2 and _num(parts[1]) is not None:
                cash += _num(parts[1])
                continue
            qty = _num(parts[1]) if len(parts) >= 2 else None
            avg = _num(parts[2]) if len(parts) >= 3 else None
            if not _TICKER.match(symbol) or qty is None or qty <= 0:
                skipped.append(line.strip()[:30])
                continue
            _merge(positions, symbol, qty, avg)
    if not positions and cash <= 0:
        raise ValueError("No holdings found. Use Fidelity's Positions → Download (CSV), or lines like `META 1.39 718.99`")
    if len(positions) > MAX_HOLDINGS:
        raise ValueError(f"The desk reads up to {MAX_HOLDINGS} holdings at a time")
    return {"positions": list(positions.values()), "cash": round(cash, 2), "source": source,
            "accounts": list(dict.fromkeys(accounts))[:10], "skipped": skipped[:20]}


def _merge(positions, symbol, qty, avg):
    """The same stock in several accounts becomes one holding with a weighted average cost."""
    prev = positions.get(symbol)
    if prev is None:
        positions[symbol] = {"ticker": symbol, "qty": round(qty, 6), "avg_cost": round(avg, 4) if avg else None}
        return
    total = prev["qty"] + qty
    if prev.get("avg_cost") and avg:
        prev["avg_cost"] = round((prev["avg_cost"] * prev["qty"] + avg * qty) / total, 4)
    else:
        prev["avg_cost"] = None
    prev["qty"] = round(total, 6)


def size_idea(account_value: float, cash: float, held_value: float, price: float, stop: float,
              settings: dict[str, float]) -> dict[str, Any]:
    """Fractional shares (Fidelity allows them): risk budget / stop distance, capped by the
    position limit and available cash."""
    risk_per_share = price - stop
    if not (account_value > 0 and price > 0 and risk_per_share > 0):
        return {"shares": 0.0, "dollars": 0.0, "why": "No valid stop below the price"}
    risk_budget = account_value * settings["risk_pct"] / 100
    by_risk = risk_budget / risk_per_share
    by_limit = max(0.0, account_value * settings["max_position_pct"] / 100 - held_value) / price
    by_cash = max(0.0, cash) / price
    shares = math.floor(min(by_risk, by_limit, by_cash) * 1000) / 1000
    limit = min((by_risk, "your risk per trade"), (by_limit, "the position limit"), (by_cash, "available cash"))[1]
    return {"shares": shares, "dollars": round(shares * price, 2), "risk_usd": round(shares * risk_per_share, 2),
            "limited_by": limit,
            "why": (f"Risking {settings['risk_pct']:g}% of ${account_value:,.0f} = ${risk_budget:,.2f}; "
                    f"stop is ${risk_per_share:,.2f} below the price, so up to {by_risk:,.3f} shares. "
                    f"Limited by {limit}.")}


def holding_row(pos: dict[str, Any], analysis: dict[str, Any], account_value: float, cash: float,
                settings: dict[str, float]) -> dict[str, Any]:
    qty, avg = float(pos["qty"]), pos.get("avg_cost")
    row: dict[str, Any] = {"ticker": pos["ticker"], "qty": qty, "avg_cost": avg}
    if not analysis or analysis.get("error"):
        row.update(action="unknown", headline="No reading", detail=str((analysis or {}).get("error") or "Analysis unavailable"))
        return row
    price = float(analysis.get("price") or 0)
    atr_usd = _num((analysis.get("intraday") or {}).get("atr_usd"))
    value = qty * price
    row.update(price=price, value=round(value, 2), change_pct=analysis.get("change_pct"),
               verdict=analysis.get("verdict"), verdict_text=analysis.get("verdict_text"),
               weight_pct=round(value / account_value * 100, 1) if account_value else None)
    if avg:
        row["gain_usd"] = round((price - avg) * qty, 2)
        row["gain_pct"] = round((price / avg - 1) * 100, 2)
    stop = round(price - settings["stop_atr"] * atr_usd, 2) if atr_usd and price > settings["stop_atr"] * atr_usd else None
    row["stop"] = stop
    row["atr_usd"] = atr_usd
    sma = analysis.get("sma") or {}
    flags = []
    if row["weight_pct"] and row["weight_pct"] > settings["max_position_pct"]:
        flags.append(f"{row['weight_pct']:.0f}% of the account, above your {settings['max_position_pct']:g}% position limit")
    earnings = analysis.get("earnings") or {}
    if isinstance(earnings, dict) and earnings.get("days_away") is not None and 0 <= int(earnings["days_away"]) <= 7:
        flags.append(f"Earnings in {int(earnings['days_away'])} day(s) ({str(earnings.get('date'))[:10]}); the price can gap either way")
    verdict = analysis.get("verdict")
    # The screener verdict is a same-day ENTRY filter (volume, trend, sector). "AVOID" often only
    # means "no unusual volume today", so the holding's trend is read from its averages instead.
    above_20, above_50 = sma.get("above_20"), sma.get("above_50")
    if above_20 and above_50:
        trend, row["trend"] = "Uptrend: above its 20- and 50-day averages.", "up"
    elif above_20 is False and above_50 is False:
        trend, row["trend"] = "Downtrend: below its 20- and 50-day averages.", "down"
    elif above_20 is None or above_50 is None:
        trend, row["trend"] = "Trend unavailable.", "unknown"
    else:
        trend, row["trend"] = ("Mixed: above its 50-day average, below its 20-day." if above_50
                               else "Mixed: above its 20-day average, below its 50-day."), "mixed"
    reason = str(analysis.get("verdict_text") or "").split(". ")[0].strip().rstrip(".")
    stop_text = (f" A stop near ${stop:,.2f} ({settings['stop_atr']:g}× its typical daily move below today's price) "
                 "would limit further loss." if stop else "")
    if verdict == "PASS" and stop:
        row["action"] = "add"
        row["headline"] = "Buy setup today"
        row["size"] = size_idea(account_value, cash, value, price, stop, settings)
        row["detail"] = f"{trend} Volume, trend and sector checks all pass today, so adding fits the desk's rule."
    elif row["trend"] == "down":
        row["action"] = "review"
        row["headline"] = "Downtrend"
        row["detail"] = f"{trend}{stop_text}"
    else:
        row["action"] = "hold"
        row["headline"] = "Hold"
        row["detail"] = f"{trend} No buy setup today" + (f" ({reason})." if reason else ".") + stop_text
    if flags:
        row["flags"] = flags
        if row["action"] == "hold":
            row["action"] = "review"
            row["headline"] = "Hold, see note"
    return row


def ideas_from_signals(signals: list[dict[str, Any]], held: set[str], account_value: float, cash: float,
                       settings: dict[str, float], *, today: str) -> list[dict[str, Any]]:
    """Today's desk PASS buy setups the Fidelity account doesn't already hold."""
    out, seen = [], set()
    for s in signals:
        ticker = str(s.get("ticker") or "").upper()
        if (s.get("verdict") != "PASS" or str(s.get("side") or "").lower() != "buy" or ticker in held
                or ticker in seen or not _TICKER.match(ticker)):
            continue
        try:
            day = datetime.fromisoformat(str(s.get("ts")).replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            continue
        if day != today:
            continue
        price, stop, target = _num(s.get("signal_price")), _num(s.get("stop")), _num(s.get("target"))
        if not price or not stop or stop >= price:
            continue
        block = str(s.get("execution_block") or "")
        seen.add(ticker)
        out.append({"ticker": ticker, "price": price, "stop": stop, "target": target, "ts": s.get("ts"),
                    "verdict_text": s.get("verdict_text"),
                    "warning": block if block.startswith(("setup_losing_record", "evidence_unavailable")) else None,
                    "size": size_idea(account_value, cash, 0.0, price, stop, settings)})
        if len(out) >= MAX_IDEAS:
            break
    return out


def evidence(desk) -> dict[str, Any]:
    """The same pre-registered test Fox uses, with Fidelity's $0 commission."""
    try:
        import lessons
        import strategy_scorecard
        status = strategy_scorecard.promotion_status(lessons.recent(desk.LESSONS_PATH, 5000),
                                                     hurdle_bps=ROUND_TRIP_COST_BPS)
        return {k: status.get(k) for k in ("passed", "calls", "sessions", "mean_bps", "lower_95_bps", "missing", "plain")}
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "plain": f"Evidence check unavailable ({type(exc).__name__})"}


def _analyze(ticker: str) -> dict[str, Any]:
    import screener_logic
    try:
        return screener_logic.analyze_ticker(ticker)
    except Exception as exc:  # noqa: BLE001 - one stock never stops the rest
        return {"ticker": ticker, "error": f"Analysis failed ({type(exc).__name__})"}


def suggestions(desk, *, force: bool = False, analyze=_analyze) -> dict[str, Any]:
    data = load(desk)
    key = json.dumps([data["positions"], data["cash"], data["settings"]], sort_keys=True)
    with _lock:
        if not force and _cache["key"] == key and time.time() - _cache["at"] < CACHE_SEC and _cache["result"]:
            return copy.deepcopy(_cache["result"])
    if not _running.acquire(blocking=False):
        return {"ok": False, "busy": True, "error": "Already reading your holdings; try again in a moment"}
    try:
        tickers = [p["ticker"] for p in data["positions"]]
        with ThreadPoolExecutor(max_workers=4) as pool:
            analyses = dict(zip(tickers, pool.map(analyze, tickers)))
        prices = {t: float(a.get("price") or 0) for t, a in analyses.items() if not a.get("error")}
        holdings_value = sum(p["qty"] * prices.get(p["ticker"], 0.0) for p in data["positions"])
        account_value = holdings_value + data["cash"]
        rows = [holding_row(p, analyses.get(p["ticker"]) or {}, account_value, data["cash"], data["settings"])
                for p in data["positions"]]
        order = {"review": 0, "add": 1, "unknown": 2, "hold": 3}
        rows.sort(key=lambda r: (order.get(r.get("action"), 9), -(r.get("value") or 0)))
        try:
            signals = desk.load_signals()
        except Exception:  # noqa: BLE001
            signals = []
        today = datetime.now(ET).strftime("%Y-%m-%d")
        ideas = ideas_from_signals(signals, set(tickers), account_value, data["cash"], data["settings"], today=today)
        result = {"ok": True, "checked_at": datetime.now(timezone.utc).isoformat(), "account_value": round(account_value, 2),
                  "holdings_value": round(holdings_value, 2), "cash": data["cash"], "holdings": rows, "ideas": ideas,
                  "evidence": evidence(desk), "settings": data["settings"],
                  "priced": len(prices), "unpriced": [t for t in tickers if t not in prices],
                  "note": "Suggestions only. The desk never connects to Fidelity; you place any trade there yourself."}
        with _lock:
            _cache.update(key=key, at=time.time(), result=copy.deepcopy(result))
        return result
    finally:
        _running.release()


def status(desk) -> dict[str, Any]:
    data = load(desk)
    return {"ok": True, "portfolio": data, "has_holdings": bool(data["positions"] or data["cash"])}


def register(app, desk) -> None:
    from flask import jsonify, request

    @app.get("/api/fidelity")
    def fidelity_status():
        return jsonify(status(desk))

    @app.post("/api/fidelity/import")
    def fidelity_import():
        body = request.get_json(silent=True) or {}
        try:
            parsed = parse_import(body.get("text"))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        with _lock:
            data = load(desk)
            data.update(positions=parsed["positions"], cash=parsed["cash"], source=parsed["source"],
                        accounts=parsed["accounts"], imported_at=datetime.now(timezone.utc).isoformat())
            save(desk, data)
        return jsonify({**status(desk), "skipped": parsed["skipped"]})

    @app.post("/api/fidelity/settings")
    def fidelity_settings():
        try:
            settings = validate_settings(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        with _lock:
            data = load(desk)
            data["settings"] = settings
            save(desk, data)
        return jsonify(status(desk))

    @app.post("/api/fidelity/suggest")
    def fidelity_suggest():
        body = request.get_json(silent=True) or {}
        result = suggestions(desk, force=bool(body.get("force")))
        return jsonify(result), (200 if result.get("ok") else 409)

    @app.post("/api/fidelity/clear")
    def fidelity_clear():
        with _lock:
            data = empty()
            data["settings"] = load(desk)["settings"]
            save(desk, data)
        return jsonify(status(desk))
