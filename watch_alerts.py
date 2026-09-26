"""Watchlist alerts: big price moves, earnings coming up and high-impact headlines for the
stocks you follow (docs/WATCH_ALERTS.md).

Owner decision: notification only. Alerts go through desk_alerts (desk toast, optional
webhook/SMS) and never place, size or block a trade. Settings live in their own file
(data/watch_alerts.json), so saving them cannot change trading configuration.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DEFAULTS = {"enabled": True, "move_pct": 3.0, "earnings_days": 2, "news": True}
MAX_SYMBOLS = 40
CHECK_EVERY_SEC = 300
EARNINGS_EVERY_SEC = 6 * 3600
NEWS_MAX_AGE_SEC = 2 * 3600
MAX_MOVE_ALERTS = 6  # per check; the rest are grouped into one summary alert
KEEP_SENT_DAYS = 7

_lock = threading.RLock()
_state: dict[str, Any] = {"last_run": None, "last_error": None, "last_earnings": 0.0, "running": False}


def _path(desk) -> Path:
    return Path(desk.DATA_DIR) / "watch_alerts.json"


def load(desk) -> dict[str, Any]:
    try:
        raw = json.loads(_path(desk).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    try:
        settings = validate({**DEFAULTS, **{k: v for k, v in settings.items() if k in DEFAULTS}})
    except ValueError:
        settings = dict(DEFAULTS)
    sent = raw.get("sent") if isinstance(raw.get("sent"), dict) else {}
    history = [h for h in raw.get("history") or [] if isinstance(h, dict)][:50]
    return {"settings": settings, "sent": sent, "history": history}


def save(desk, raw: dict[str, Any]) -> None:
    path = _path(desk)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    tmp.replace(path)


def validate(body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - set(DEFAULTS):
        raise ValueError("Use only the displayed alert settings")
    value = {**DEFAULTS, **body}
    for key in ("enabled", "news"):
        if not isinstance(value[key], bool):
            raise ValueError(f"{key} must be true or false")
    try:
        move = float(value["move_pct"])
        days = float(value["earnings_days"])
    except (TypeError, ValueError):
        raise ValueError("Enter numbers for the move and earnings settings") from None
    if not math.isfinite(move) or not 1 <= move <= 50:
        raise ValueError("Price move alert must be between 1% and 50%")
    if not math.isfinite(days) or days != int(days) or not 0 <= days <= 14:
        raise ValueError("Earnings warning must be 0–14 whole days (0 turns it off)")
    return {"enabled": value["enabled"], "move_pct": round(move, 2), "earnings_days": int(days), "news": value["news"]}


def followed(desk) -> list[str]:
    """Stocks the agent holds first, then the watchlist; equities only, capped."""
    symbols: list[str] = []
    try:
        managed = (desk._live_agent.load().get("managed") or {}) if getattr(desk, "_live_agent", None) else {}
        symbols += [str(t).upper() for t in managed]
    except Exception:  # noqa: BLE001
        pass
    try:
        import paper_loop
        symbols += paper_loop.equity_loop_symbols(list((desk.load_config() or {}).get("watchlist") or []))
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(s for s in symbols if s))[:MAX_SYMBOLS]


def _price(value: float) -> str:
    return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"


def move_alerts(quotes: dict[str, dict[str, Any]], move_pct: float) -> list[dict[str, Any]]:
    """One alert per symbol, day and size band (3%, then 6%, 9%… with a 3% setting)."""
    out = []
    for sym, q in quotes.items():
        try:
            price, prev = float(q.get("regularMarketPrice")), float(q.get("regularMarketPreviousClose"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(price) and math.isfinite(prev)) or price <= 0 or prev <= 0:
            continue
        change = (price / prev - 1) * 100
        band = int(abs(change) // move_pct)
        if band < 1:
            continue
        stamp = q.get("regularMarketTime")
        try:
            day = datetime.fromtimestamp(float(stamp), ET).date().isoformat()
        except (TypeError, ValueError, OverflowError, OSError):
            day = datetime.now(ET).date().isoformat()
        example = f"$1,000 of {sym} would now be worth about ${1000 * price / prev:,.0f}."
        out.append({"key": f"move|{sym}|{day}|{'up' if change > 0 else 'down'}|{band}", "kind": "watch_move",
                    "ticker": sym, "level": "warning" if abs(change) >= 2 * move_pct else "info", "change": change,
                    "message": f"{sym} is {'up' if change > 0 else 'down'} {abs(change):.1f}% today "
                               f"({_price(prev)} → {_price(price)}). {example}"})
    out.sort(key=lambda a: -abs(a["change"]))
    return out


def limit_moves(alerts: list[dict[str, Any]], move_pct: float, sent: dict[str, Any]) -> list[dict[str, Any]]:
    """Biggest new moves individually; group the rest into one summary so a volatile day is not a flood."""
    fresh = [a for a in alerts if a["key"] not in sent]
    if len(fresh) <= MAX_MOVE_ALERTS:
        return fresh
    shown, rest = fresh[:MAX_MOVE_ALERTS], fresh[MAX_MOVE_ALERTS:]
    names = ", ".join(f"{a['ticker']} {a['change']:+.1f}%" for a in rest[:8]) + (" …" if len(rest) > 8 else "")
    summary = {"key": "moves|" + "|".join(sorted(a["key"] for a in rest)), "kind": "watch_move", "ticker": "several",
               "level": "info", "covers": [a["key"] for a in rest],
               "message": f"{len(rest)} more followed stocks moved at least {move_pct:g}% today: {names}"}
    return shown + [summary]


def earnings_alerts(rows: dict[str, dict[str, Any]], days: int) -> list[dict[str, Any]]:
    out = []
    for sym, e in rows.items():
        try:
            away = int(e.get("days_away"))
            when = str(e.get("date"))[:10]
        except (TypeError, ValueError):
            continue
        if days and 0 <= away <= days:
            timing = "today" if away == 0 else "tomorrow" if away == 1 else f"in {away} days"
            out.append({"key": f"earnings|{sym}|{when}", "kind": "watch_earnings", "ticker": sym, "level": "info",
                        "message": f"{sym} reports earnings {timing} ({when}). Prices often jump or drop sharply on the report."})
    return out


def news_alerts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"key": f"news|{r['ticker']}|{r['headline_key']}", "kind": "watch_news", "ticker": r["ticker"], "level": "info",
             "message": f"{r['ticker']} headline: {r['title'][:180]} ({r.get('source') or 'news'})", "url": r.get("url")}
            for r in rows if r.get("headline_key") and r.get("title") and r.get("ticker")]


def _earnings_rows(symbols: list[str]) -> dict[str, dict[str, Any]]:
    import screener_logic
    import yfinance as yf
    out = {}
    for sym in symbols:
        try:
            earnings, _ = screener_logic._fetch_earnings(sym, yf.Ticker(sym))
        except Exception:  # noqa: BLE001 - one lookup never stops the rest
            continue
        if isinstance(earnings, dict):
            out[sym] = earnings
    return out


def run(desk, *, now: datetime | None = None, force_earnings: bool = False) -> dict[str, Any]:
    """Check once and emit new alerts. Safe to call from the route or the background loop."""
    with _lock:
        if _state["running"]:
            return status(desk)
        _state["running"] = True
    try:
        now = now or datetime.now(timezone.utc)
        raw = load(desk)
        settings = raw["settings"]
        if not settings["enabled"]:
            return status(desk)
        symbols = followed(desk)
        candidates: list[dict[str, Any]] = []
        errors = []
        if symbols:
            try:
                import data_sources
                moves = move_alerts({s: q for s, q in (data_sources.yahoo_quote_batch(symbols, timeout=10) or {}).items()
                                     if s in symbols}, settings["move_pct"])
                candidates += limit_moves(moves, settings["move_pct"], raw["sent"])
            except Exception as exc:  # noqa: BLE001
                errors.append(f"prices unavailable ({type(exc).__name__})")
            if settings["earnings_days"] and (force_earnings or time.time() - _state["last_earnings"] >= EARNINGS_EVERY_SEC):
                try:
                    candidates += earnings_alerts(_earnings_rows(symbols), settings["earnings_days"])
                    _state["last_earnings"] = time.time()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"earnings dates unavailable ({type(exc).__name__})")
            if settings["news"]:
                try:
                    import news_stream
                    candidates += news_alerts(news_stream.material_headlines(symbols, max_age_sec=NEWS_MAX_AGE_SEC))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"headlines unavailable ({type(exc).__name__})")
        import desk_alerts
        fresh = []
        stamp = now.isoformat()
        for alert in candidates:
            if alert["key"] in raw["sent"]:
                continue
            raw["sent"][alert["key"]] = stamp
            for covered in alert.get("covers") or []:
                raw["sent"][covered] = stamp
            desk_alerts.emit(alert["kind"], alert["message"], level=alert["level"], dedupe_key=alert["key"],
                             detail={"ticker": alert["ticker"], "url": alert.get("url"), "source": "watch_alerts"})
            fresh.append({"at": stamp, **{k: alert.get(k) for k in ("kind", "ticker", "message", "url")}})
        cutoff = (now - timedelta(days=KEEP_SENT_DAYS)).isoformat()
        raw["sent"] = {k: v for k, v in raw["sent"].items() if str(v) >= cutoff}
        raw["history"] = (fresh + raw["history"])[:50]
        save(desk, raw)
        with _lock:
            _state.update(last_run=stamp, last_error="; ".join(errors) or None, symbols=len(symbols))
        return status(desk)
    finally:
        with _lock:
            _state["running"] = False


def status(desk) -> dict[str, Any]:
    raw = load(desk)
    with _lock:
        state = copy.deepcopy(_state)
    return {"ok": True, "settings": raw["settings"], "history": raw["history"][:20], "last_run": state.get("last_run"),
            "last_error": state.get("last_error"), "symbols": state.get("symbols"), "notification_only": True,
            "note": "Alerts only notify you. They never place, size or block a trade."}


def start_background(desk) -> None:
    if (os.environ.get("TOMAHAWK_NO_BG") or "").strip().lower() in ("1", "true", "yes", "on"):
        return

    def loop() -> None:
        time.sleep(60)  # let the desk finish starting
        while True:
            try:
                run(desk)
            except Exception as exc:  # noqa: BLE001 - alerts never stop the desk
                with _lock:
                    _state["last_error"] = f"Check failed ({type(exc).__name__})"
            time.sleep(CHECK_EVERY_SEC)

    threading.Thread(target=loop, name="watch-alerts", daemon=True).start()


def register(app, desk) -> None:
    from flask import jsonify, request

    @app.get("/api/watch-alerts")
    def watch_alerts_status():
        return jsonify(status(desk))

    @app.post("/api/watch-alerts")
    def watch_alerts_save():
        try:
            settings = validate(request.get_json(silent=True))
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        with _lock:
            raw = load(desk)
            raw["settings"] = settings
            save(desk, raw)
        return jsonify(status(desk))

    @app.post("/api/watch-alerts/check")
    def watch_alerts_check():
        return jsonify(run(desk, force_earnings=True))

    start_background(desk)
