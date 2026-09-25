"""Today at the desk: Fox (the broker agent) and Changing Woman (chores and reasoning).

One read-only snapshot that ties the desk's features together for the sidebar companions
and the "Today at the desk" panel:

- Fox is the broker agent in person: what he is researching, what he bought or sold, the
  positions he is protecting, and why he is waiting.
- Changing Woman keeps the desk's chores (reconciling broker orders, the calendar, the WSB
  threads, the news, the trade journal, upkeep, readable ledgers) and reasons alongside
  Fox: scheduled speeches and releases, WSB crowding, the cost edge and track record of
  his latest idea, the day's give-back guard and the approaching close.

Nothing here places, changes or cancels an order; it only reads cached state.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Blueprint, jsonify

import market_events
import wsb_monitor

TRADE_STATUSES = {"approved", "filled", "executed", "broker_filled"}
SKIP_STATUSES = {"hold", "no_setup", "discarded", "options_skip", "budget"}


def _money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "?"
    if not math.isfinite(number):
        return "?"
    return f"-${abs(number):,.2f}" if number < 0 else f"${number:,.2f}"


def _moment(stamp: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")) if not isinstance(stamp, (int, float)) \
            else datetime.fromtimestamp(float(stamp), timezone.utc)
        return moment if moment.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _ago(stamp: Any, now: datetime) -> str:
    if stamp is None:
        return "never"
    moment = _moment(stamp)
    if moment is None or moment > now:
        return "unknown"
    minutes = int((now - moment).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago" if minutes < 48 * 60 else f"{minutes // 1440} days ago"


# --- Fox --------------------------------------------------------------------------------

def _event_line(event: dict[str, Any]) -> dict[str, Any]:
    status = str(event.get("status") or "")
    ticker = event.get("ticker") or ""
    message = str(event.get("message") or "")
    fill = event.get("fill") if isinstance(event.get("fill"), dict) else None
    try:
        valid_fill = bool(fill) and all(math.isfinite(float(fill[k])) and float(fill[k]) > 0 for k in ("shares", "price"))
    except (KeyError, TypeError, ValueError, OverflowError):
        valid_fill = False
    if fill is not None and not valid_fill:
        kind, text = "info", f"Order update for {ticker}: fill details unavailable. Check the broker record."
    elif status.startswith("exit_"):
        kind = "exit"
        reason = status[5:].replace("_", " ")
        if fill:
            text = f"Sold {float(fill.get('shares') or 0):g} {ticker} at {_money(fill.get('price'))} ({reason})."
        else:
            text = f"Tried a {reason} exit on {ticker}: {message}"
    elif fill:
        kind = "trade"
        verb = "Bought" if event.get("side") != "sell" else "Sold"
        qty = float(fill.get('shares') or 0)
        if event.get("asset_type") in ("OPT", "BAG"):
            text = (f"{verb} {qty:g} {ticker} {event.get('option_strategy') or 'option'} contract{'s' if qty != 1 else ''} "
                    f"at {_money(fill.get('price'))} premium.")
        else:
            text = f"{verb} {qty:g} {ticker} at {_money(fill.get('price'))}."
    elif status in SKIP_STATUSES:
        kind, text = "skip", (f"Passed on {ticker}: {message}" if ticker else message)
    elif status == "broker_pending":
        kind, text = "pending", f"Waiting on the broker for {ticker}: {message}"
    else:
        kind, text = "info", message or status.replace("_", " ")
    return {"at": event.get("at"), "kind": kind, "ticker": ticker, "text": text[:220],
            "id": f"{event.get('at')}|{event.get('signal_id')}|{status}"}


def fox_view(agent: dict[str, Any] | None, window: dict[str, Any] | None, now: datetime) -> dict[str, Any]:
    agent = agent or {}
    events = [_event_line(e) for e in (agent.get("events") or [])[:12]]
    managed = []
    for ticker, row in sorted((agent.get("managed") or {}).items()):
        managed.append({"ticker": ticker, "shares": row.get("shares"), "entry": row.get("entry"), "stop": row.get("stop"),
                        "target": row.get("target"), "breakeven": bool(row.get("breakeven"))})
    phase, message = str(agent.get("phase") or ""), str(agent.get("message") or "")
    ticker = None
    match = re.search(r"Evaluating ([A-Z][A-Z0-9.]{0,9})", message)
    if match:
        ticker = match.group(1)
    if not agent.get("configured"):
        state, headline = "off", "Fox is off duty. Save a broker agent policy on the Auto page to put him to work."
    elif not agent.get("enabled") or agent.get("mode") != "auto_live" or not agent.get("session_active"):
        state, headline = "resting", "Fox is resting: the broker agent is paused. Positions and orders stay at the broker."
    elif phase == "waiting_for_market" or agent.get("market_open") is False:
        state, headline = "waiting", "Fox is waiting for the next regular US session."
    elif window:
        state, headline = "holding", f"Fox is holding off on new trades: {market_events.describe(window, now)}. His exits still work."
    elif phase == "researching":
        state, headline = "researching", f"Fox is researching {ticker or 'the next symbol'}."
    elif phase == "reconciling":
        state, headline = "reconciling", "Fox is waiting for the broker to confirm an order before doing anything else."
    elif phase in ("daily_limit", "budget"):
        state, headline = "done", "Fox has used today's research or order allowance."
    elif phase in ("blocked", "error"):
        state, headline = "blocked", f"Fox is stuck: {message}"
    else:
        state = "watching"
        nxt = agent.get("next_at")
        headline = f"Fox is on watch. Next research no earlier than {market_events.clock_et(nxt)}." if nxt else "Fox is on watch."
    if managed and state not in ("off",):
        protect = ", ".join(f"{m['ticker']} (stop {_money(m['stop'])}{' at entry' if m['breakeven'] else ''}, "
                            f"target {_money(m['target'])})" for m in managed[:3])
        headline += f" Protecting {len(managed)} position{'s' if len(managed) != 1 else ''}: {protect}."
    recent_trades = []
    for row in events:
        at = _moment(row["at"])
        if at is None:
            continue
        if row["kind"] in ("trade", "exit") and timedelta(0) <= now - at <= timedelta(minutes=10):
            recent_trades.append(row)
    return {"state": state, "headline": headline, "ticker": ticker, "managed": managed, "recent": events[:8],
            "latest_trade": recent_trades[0] if recent_trades else None, "today": agent.get("today") or {},
            "message": message}


# --- Changing Woman -------------------------------------------------------------------------

def _chore(key: str, label: str, state: str, detail: str) -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail}


def chores(desk, now: datetime, events_today: list[dict[str, Any]], wsb: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    try:
        pending = len(desk.load_ledger().get("pending_broker_orders") or [])
    except Exception:  # noqa: BLE001
        pending = None
    out.append(_chore("reconcile", "Reconcile broker orders",
                      "done" if pending == 0 else "working" if pending else "attention",
                      "No broker orders waiting for confirmation" if pending == 0 else
                      f"{pending} order(s) waiting for broker confirmation" if pending else "Ledger unreadable"))
    status = market_events.status()
    bad = [name for name, row in (status.get("sources") or {}).items() if not row.get("ok")]
    high_today = [e for e in events_today if e.get("impact") == "high"]
    detail = (f"{len(events_today)} scheduled event(s) today" + (f", {len(high_today)} high impact" if high_today else "")
              if events_today else "No medium or high impact events in available sources today")
    if bad:
        detail += f" · unavailable: {', '.join(bad)} (saved dates may still be shown)"
    elif not status.get("refreshed_at"):
        detail += " · reading the Fed, BLS and White House calendars"
    out.append(_chore("calendar", "Check the calendar",
                      "attention" if bad else "done" if status.get("refreshed_at") else "working", detail))
    if not wsb.get("configured"):
        out.append(_chore("wsb", "Read the WSB threads", "off", "Reddit is not connected (REDDIT_CLIENT_ID/SECRET)"))
    else:
        names = ", ".join(t.get("label") or "thread" for t in wsb.get("threads") or []) or "no discussion thread found yet"
        out.append(_chore("wsb", "Read the WSB threads", "done" if wsb.get("fresh") else "attention",
                          f"{names} · {wsb.get('comments_read', 0):,} comments read · last read {_ago(wsb.get('last_ok_at'), now)}"
                          + (f" · {wsb['error']}" if wsb.get("error") else "")))
    companion = getattr(desk, "_research_companion", None)
    try:
        news = companion.news.snapshot() if companion else None
    except Exception:  # noqa: BLE001
        news = None
    if news:
        sources = news.get("sources") or []
        live = sum(1 for s in sources if s.get("status") in ("connected", "partial"))
        out.append(_chore("news", "Read the news", "done" if live else "attention",
                          f"{live} of {len(sources)} sources connected · {len(news.get('items') or [])} recent headlines"))
    try:
        journal = companion.trades.status() if companion else None
    except Exception:  # noqa: BLE001
        journal = None
    if journal is not None:
        if journal.get("error"):
            out.append(_chore("journal", "Sync the trade journal", "attention", str(journal["error"])[:160]))
        elif journal.get("last_sync"):
            out.append(_chore("journal", "Sync the trade journal", "done",
                              f"Broker executions synced {_ago(journal.get('last_sync'), now)}"))
        else:
            out.append(_chore("journal", "Sync the trade journal", "off", "No broker executions synced yet"))
    try:
        upkeep = json.loads((desk.DATA_DIR / "upkeep" / "last_daily.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        upkeep = None
    if upkeep:
        problems = upkeep.get("problems") or []
        out.append(_chore("upkeep", "Nightly upkeep", "attention" if problems else "done",
                          ("; ".join(map(str, problems))[:160]) if problems else f"Clean · ran {_ago(upkeep.get('at'), now)}"))
    else:
        out.append(_chore("upkeep", "Nightly upkeep", "off", "No upkeep report yet (the Windows task writes it after the close)"))
    if getattr(desk, "DATA_DIR", None):
        import desk_backups
        backup = desk_backups.status(desk)
        out.append(_chore("backup", "Verify local backups", "working" if backup.get("busy") else
                          "done" if backup.get("ok") and not backup.get("stale") and not backup.get("last_error") else "attention",
                          backup.get("last_error") or (f"State snapshot verified {_ago(backup.get('verified_at'), now)}" if backup.get("ok")
                           else backup.get("error") or "No verified state snapshot yet")))
    corrupt = list(getattr(desk, "_CORRUPT_PATHS", []) or [])
    out.append(_chore("ledgers", "Keep the ledgers readable", "attention" if corrupt else "done",
                      f"Needs repair: {', '.join(map(str, corrupt))[:160]}" if corrupt else "All desk files readable"))
    return out


def _note(key: str, level: str, text: str) -> dict[str, Any]:
    return {"key": key, "level": level, "text": text}


def reasoning(desk, cfg: dict[str, Any], now: datetime, fox: dict[str, Any], window: dict[str, Any] | None,
              upcoming: list[dict[str, Any]], wsb_stats: list[dict[str, Any]], last_signal: dict[str, Any] | None,
              minutes_to_close: float | None, agent: dict[str, Any] | None) -> list[dict[str, Any]]:
    notes = []
    if window:
        notes.append(_note("event_window", "block", market_events.window_message(window, now)))
    for row in upcoming:
        start = datetime.fromisoformat(row["start"])
        minutes = (start - now).total_seconds() / 60
        if row.get("impact") != "high" or minutes < 0 or row.get("all_day"):
            continue
        guard = market_events.guard_window(row, cfg) if cfg.get("event_guard_enabled", True) else None
        guard_text = (f" Fox pauses new entries {market_events.clock_et(guard[0])}–{market_events.clock_et(guard[1])}."
                      if guard else "")
        if minutes <= 90:
            notes.append(_note("event_soon:" + row["id"], "caution",
                               f"{market_events.describe(row, now)} is in {round(minutes)} min.{guard_text}"))
        elif start.astimezone(market_events.ET).date() == now.astimezone(market_events.ET).date():
            after_close = start.astimezone(market_events.ET).hour >= 16
            extra = (" It lands after the close, so anything still held carries it overnight." if after_close else guard_text)
            notes.append(_note("event_today:" + row["id"], "caution" if after_close else "info",
                               f"Later today: {market_events.describe(row, now)}.{extra}"))
    medium_soon = [r for r in upcoming if r.get("impact") == "medium"
                   and 0 <= (datetime.fromisoformat(r["start"]) - now).total_seconds() <= 3600 and not r.get("all_day")]
    for row in medium_soon[:2]:
        notes.append(_note("event_medium:" + row["id"], "info", f"Also scheduled: {market_events.describe(row, now)}."))
    tickers = [m["ticker"] for m in fox.get("managed") or []]
    for sym in ([fox.get("ticker")] if fox.get("ticker") else []) + ([last_signal.get("ticker")] if last_signal else []):
        if sym and sym not in tickers:
            tickers.append(sym)
    for sym in tickers:
        crowd = wsb_monitor.crowding(sym, cfg, stats=wsb_stats)
        if crowd.get("crowded"):
            action = cfg.get("wsb_crowding_action", "half")
            effect = {"half": "Fox would buy half size", "skip": "Fox skips new buys in it",
                      "note": "noted only", "off": "WSB caution is off"}.get(action, "")
            notes.append(_note("wsb_crowded:" + sym, "caution", f"{crowd['reason']}. {effect}."))
        row = next((r for r in wsb_stats if r["ticker"] == sym), None)
        if (row and sym in [m["ticker"] for m in fox.get("managed") or []] and row.get("bull_share") is not None
                and row["bull_share"] <= 0.3 and row["mentions_60m"] >= 10):
            notes.append(_note("wsb_bearish:" + sym, "caution",
                               f"WSB is leaning against {sym}, which Fox holds: {round((1 - row['bull_share']) * 100)}% "
                               f"bearish across {row['mentions_60m']} mentions this hour. His stop still stands."))
    for sym, earn in _earnings_rows(fox, last_signal):
        try:
            day = datetime.fromisoformat(str(earn.get("date"))[:10]).date()
        except ValueError:
            continue
        days = (day - now.astimezone(market_events.ET).date()).days
        if 0 <= days <= 1:
            when = "today" if days == 0 else "tomorrow"
            hour = {"bmo": " before the open", "amc": " after the close"}.get(str(earn.get("hour") or "").lower(), "")
            notes.append(_note("earnings:" + sym, "caution" if days == 0 else "info",
                               f"{sym} reports earnings {when}{hour}. Fox opens nothing new in it on its report day."))
    if last_signal:
        sym = last_signal.get("ticker")
        rr, breakeven = last_signal.get("net_reward_risk"), last_signal.get("breakeven_win_rate")
        flags = set(last_signal.get("research_flags") or [])
        if "setup_losing_record" in flags:
            notes.append(_note("record:" + str(sym), "info", f"Fox's {sym} setup has a losing record after costs, so it was not traded."))
        elif isinstance(rr, (int, float)):
            notes.append(_note("edge:" + str(sym), "info",
                               f"Fox's latest idea, {sym}: after costs the target pays {rr:.2f}x the risk"
                               + (f", so it needs to win {round(breakeven * 100)}% of the time to break even." if isinstance(breakeven, (int, float)) else ".")))
    try:
        ledger = desk.load_ledger()
        peak = ((ledger.get("broker_daily") or {}).get(desk._today_str()) or {}).get("peak_pnl")
        book = (getattr(desk, "_BROKER_BOOK_CACHE", {}) or {}).get("val") or {}
        day = book.get("day_pnl_usd")
        giveback = float(cfg.get("giveback_stop_pct") or 0)
        if isinstance(peak, (int, float)) and peak > 0 and isinstance(day, (int, float)) and giveback > 0:
            floor = peak * (1 - giveback / 100)
            level = "caution" if day <= floor + 0.2 * (peak - floor) else "info"
            notes.append(_note("giveback", level, f"Today's broker P&L peaked at {_money(peak)} and is {_money(day)} now. "
                                                  f"New entries pause if it falls to {_money(floor)}."))
    except Exception:  # noqa: BLE001
        pass
    policy = ((agent or {}).get("policy") or {})
    if fox.get("managed") and isinstance(minutes_to_close, (int, float)) and 0 < minutes_to_close <= 30:
        flat = int(policy.get("flatten_before_close_min") or 0)
        notes.append(_note("close", "caution" if not flat else "info",
                           f"{round(minutes_to_close)} min to the close. " +
                           (f"Fox sells what he holds {flat} min before." if flat and policy.get("protective_exits", True)
                            else "Fox is set to hold overnight.")))
    if not wsb_monitor.configured():
        notes.append(_note("wsb_off", "info", "WSB isn't connected, so crowding checks are off."))
    return notes


# --- snapshot ------------------------------------------------------------------------------

def _earnings_rows(fox: dict[str, Any], last_signal: dict[str, Any] | None) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    if last_signal and isinstance(last_signal.get("earnings"), dict) and last_signal.get("ticker"):
        rows.append((str(last_signal["ticker"]), last_signal["earnings"]))
    return rows


def _last_signal(desk, agent: dict[str, Any] | None) -> dict[str, Any] | None:
    ids = [e.get("signal_id") for e in (agent or {}).get("events") or [] if e.get("signal_id")]
    if not ids:
        return None
    try:
        signals = desk.load_signals()
    except Exception:  # noqa: BLE001
        return None
    for sid in ids[:5]:
        for row in signals:
            if row.get("id") == sid:
                return row
    return None


def research_view(desk, now: datetime) -> dict[str, Any]:
    """Describe the existing autonomous feed worker; never fetch or call a model here."""
    companion = getattr(desk, "_research_companion", None)
    try:
        news = companion.news.snapshot(now.timestamp()) if companion else {}
    except Exception:
        news = {}
    news = news if isinstance(news, dict) else {}
    source_rows, item_rows = news.get("sources"), news.get("items")
    sources = [s for s in source_rows if isinstance(s, dict)] if isinstance(source_rows, list) else []
    current = lambda stamp, age: isinstance(stamp, (int, float)) and 0 <= now.timestamp() - stamp <= age
    fresh_sources = [s for s in sources if s.get("status") in ("connected", "partial")
                     and current(s.get("checked_at"), 900)]
    items = [r for r in item_rows if isinstance(r, dict) and r.get("fresh") is True
             and current(r.get("checked_at"), 900) and current(r.get("published_ts"), 36 * 3600)] if isinstance(item_rows, list) else []
    refresh = news.get("refresh_seconds", 300)
    refresh = refresh if isinstance(refresh, (int, float)) and 30 <= refresh <= 3600 else 300
    checked = [s.get("checked_at") for s in sources if isinstance(s.get("checked_at"), (float, int))
               and 0 <= now.timestamp() - s["checked_at"] <= 900]
    checked_at = max(checked) if checked else None
    if news.get("busy"):
        summary = "The headline worker is refreshing its sources. I am keeping the last verified context separate."
    elif fresh_sources:
        summary = f"{len(fresh_sources)} of {len(sources)} feeds current; {len(items)} fresh headlines to assess. I look for catalysts, contrary evidence and the source date."
    else:
        summary = "No current headline feeds verified. I will not turn an old story into a fresh trading reason."
    return {"summary": summary, "busy": bool(news.get("busy")), "fresh_sources": len(fresh_sources),
            "total_sources": len(sources), "fresh_headlines": len(items), "checked_at": checked_at,
            "refresh_seconds": refresh, "evaluation": "App-written research questions; not model conclusions or order signals.",
            "source": "/desk/research#companion-news"}


def snapshot(desk, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = desk.load_config()
    try:
        agent = desk._live_agent.status()
    except Exception as exc:  # noqa: BLE001
        agent = {"configured": False, "message": str(exc)[:160]}
    window = market_events.active_window(cfg, now)
    upcoming = market_events.upcoming(cfg, now, hours=36, min_impact="medium")
    today = now.astimezone(market_events.ET).date()
    events_today = [e for e in upcoming if datetime.fromisoformat(e["start"]).astimezone(market_events.ET).date() == today]
    wsb = wsb_monitor.snapshot(now.timestamp(), limit=8)
    stats = wsb_monitor.ticker_stats(now.timestamp())
    try:
        import live_agent
        to_close = live_agent._minutes_to_close(now)
    except Exception:  # noqa: BLE001
        to_close = None
    fox = fox_view(agent, window, now)
    last = _last_signal(desk, agent)
    woman = {
        "chores": chores(desk, now, events_today, wsb),
        "reasoning": reasoning(desk, cfg, now, fox, window, upcoming, stats, last, to_close, agent),
    }
    open_chores = ([c for c in woman["chores"] if c["state"] == "attention"]
                   + [c for c in woman["chores"] if c["state"] == "working"])
    woman["headline"] = (f"Changing Woman is on it: {open_chores[0]['label'].lower()} — {open_chores[0]['detail']}"
                         if open_chores else "Changing Woman has the chores done and is reading along with Fox.")
    for row in upcoming:
        guard = market_events.guard_window(row, cfg) if cfg.get("event_guard_enabled", True) else None
        row["when"] = market_events.describe(row, now)
        row["guard"] = (f"{market_events.clock_et(guard[0])}–{market_events.clock_et(guard[1])}" if guard else None)
    return {
        "ok": True, "as_of": now.isoformat(), "fox": fox, "woman": woman, "research": research_view(desk, now),
        "events": {"upcoming": upcoming[:10], "active_window": window, "status": market_events.status(),
                   "guard_enabled": bool(cfg.get("event_guard_enabled", True))},
        "wsb": {k: wsb.get(k) for k in ("configured", "fresh", "threads", "top", "error", "comments_read",
                                         "last_ok_at", "history_minutes", "live_chat_note")},
        "market": {"minutes_to_close": round(to_close) if isinstance(to_close, (int, float)) else None,
                   "open": bool((agent or {}).get("market_open"))},
        "note": "Read-only. Fox acts through the broker agent's normal gates; nothing here sends an order.",
    }


def register(app, desk) -> None:
    bp = Blueprint("desk_day", __name__)
    cache: dict[str, Any] = {"at": 0.0, "payload": None}

    @bp.get("/api/desk-day")
    def desk_day():
        if cache["payload"] is not None and time.time() - cache["at"] < 5:
            return jsonify(cache["payload"])
        try:
            payload = snapshot(desk)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}), 500
        cache.update(at=time.time(), payload=payload)
        return jsonify(payload)

    @bp.get("/api/market-events")
    def market_events_route():
        cfg = desk.load_config()
        now = datetime.now(timezone.utc)
        rows = market_events.upcoming(cfg, now, hours=24 * 14, min_impact="low")
        for row in rows:
            row["when"] = market_events.describe(row, now)
        return jsonify({"ok": True, "events": rows[:200], "status": market_events.status(),
                        "active_window": market_events.active_window(cfg, now)})

    @bp.get("/api/wsb")
    def wsb_route():
        return jsonify(wsb_monitor.snapshot(limit=40))

    app.register_blueprint(bp)
