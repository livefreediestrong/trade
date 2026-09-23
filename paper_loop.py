"""
Jev-style equities paper trading decision loop.

Runs when session_active + loop_enabled and mode is auto_paper or manual
(Ask me first). auto_paper auto-executes; manual enqueues Waiting for Approve/Skip.
Housekeeping (stop/TP exits + horizon outcomes) runs whenever session_active,
even outside auto_paper.
RTH-gated (America/New_York Mon–Fri 9:30–16:00) when rth_only.
Dry-run / paper fills only — no live broker.
Abstain/hold is always allowed (never forced into a trade).
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

NY_TZ = ZoneInfo("America/New_York") if ZoneInfo else None
RTH_OPEN = dtime(9, 30)
RTH_CLOSE = dtime(16, 0)

DECISIONS_MAX = 500
MIN_LOOP_INTERVAL_SEC = 30
DEFAULT_LOOP_INTERVAL_SEC = 60


EARLY_CLOSE = dtime(13, 0)


def _easter(year: int) -> "date":
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> "date":
    """n-th (1-based) weekday of a month; n=-1 means the last one."""
    if n > 0:
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)
    nxt = date(year + (month == 12), month % 12 + 1, 1)
    d = nxt - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: "date") -> "date":
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def nyse_holidays(year: int) -> set:
    """NYSE full-day closures (rule-based; ad-hoc closures like national days of
    mourning are not covered — add them to EXTRA_CLOSED_DATES)."""
    hol = {
        _nth_weekday(year, 1, 0, 3),   # MLK Day
        _nth_weekday(year, 2, 0, 3),   # Presidents Day
        _easter(year) - timedelta(days=2),  # Good Friday
        _nth_weekday(year, 5, 0, -1),  # Memorial Day
        _observed(date(year, 7, 4)),   # Independence Day
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)), # Christmas
    }
    ny = date(year, 1, 1)
    if ny.weekday() != 5:  # NYSE does not observe a Saturday New Year on Dec 31
        hol.add(_observed(ny))
    if year >= 2022:
        hol.add(_observed(date(year, 6, 19)))  # Juneteenth
    return hol | {d for d in EXTRA_CLOSED_DATES if d.year == year}


EXTRA_CLOSED_DATES: set = set()


def session_close_time(d: "date") -> Optional[dtime]:
    """Regular-session close for a date, or None if the market is closed all day."""
    if d.weekday() >= 5 or d in nyse_holidays(d.year):
        return None
    early = set()
    jul3 = date(d.year, 7, 3)
    if jul3.weekday() < 5 and jul3 not in nyse_holidays(d.year):
        early.add(jul3)
    early.add(_nth_weekday(d.year, 11, 3, 4) + timedelta(days=1))  # day after Thanksgiving
    dec24 = date(d.year, 12, 24)
    if dec24.weekday() < 5:
        early.add(dec24)
    return EARLY_CLOSE if d in early else RTH_CLOSE


def is_rth(now: Optional[datetime] = None) -> bool:
    """True during NYSE regular hours (9:30 to 16:00, or 13:00 on early-close days)
    in America/New_York, excluding weekends and NYSE holidays."""
    if NY_TZ is None:
        now = now or datetime.now()
        if now.tzinfo is not None:
            now = now.replace(tzinfo=None)
    else:
        now = now or datetime.now(NY_TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=NY_TZ)
        else:
            now = now.astimezone(NY_TZ)
    close = session_close_time(now.date())
    if close is None:
        return False
    t = now.time()
    return RTH_OPEN <= t < close


def equity_loop_symbols(watchlist: list[str]) -> list[str]:
    """Prefer tradeable equity tickers for the paper loop.

    Drops index/meta (``.DJI``), FX/crypto pairs (``BTC / USD``), pure numeric
    foreign codes (``2353``), and symbols that start with a digit (``29M``).
    Allows class shares like ``BRK.B``.
    """
    out: list[str] = []
    seen: set[str] = set()
    for raw in watchlist or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym.startswith("."):
            continue
        if any(ch in sym for ch in (" ", "/", ":")):
            continue
        if sym[0].isdigit():
            continue
        if sym.isdigit():
            continue
        # Allow A-Z, digits, and at most one internal dot (BRK.B)
        if sym.count(".") > 1:
            continue
        core = sym.replace(".", "")
        if not core.isalnum() or not any(c.isalpha() for c in core):
            continue
        if len(core) > 6:
            continue
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out

# Curated liquid US mega-caps + ETFs for focused paper/scan mode (~40).
CURATED_LIQUID_US: list[str] = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AMD", "AVGO",
    "NFLX", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "TXN", "MU", "AMAT", "CSCO",
    "COST", "WMT", "HD", "MCD", "NKE", "SBUX", "DIS", "BA", "CAT", "GE",
    "JPM", "BAC", "GS", "V", "MA", "XOM", "CVX",
    "SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH",
]


def prune_watchlist_for_trading(
    watchlist: list[str],
    *,
    focus: str = "liquid",
    curated: list[str] | None = None,
) -> list[str]:
    """Focus symbols for scan + paper loop.

    Always starts from equity_loop_symbols. When focus is ``liquid``, intersects
    with CURATED_LIQUID_US when that intersection is non-empty; otherwise keeps
    the equity-filtered list. Does not mutate the saved watchlist.
    """
    base = equity_loop_symbols(list(watchlist or []))
    mode = (focus or "liquid").strip().lower()
    if mode != "liquid":
        return base
    curated_set = {s.upper() for s in (curated if curated is not None else CURATED_LIQUID_US)}
    focused = [s for s in base if s in curated_set]
    return focused if focused else base


def merge_radar_into_focus(
    base: list[str],
    radar: list[str] | None,
    *,
    cap_extra: int = 12,
) -> list[str]:
    """Prepend hot radar tickers not already in focus. Paper research only."""
    try:
        import market_radar as _mr

        return _mr.merge_into_focus(list(base or []), list(radar or []), cap_extra=cap_extra)
    except Exception:
        return list(base or [])


def clamp_interval(sec: Any) -> int:
    try:
        v = int(sec)
    except (TypeError, ValueError):
        v = DEFAULT_LOOP_INTERVAL_SEC
    return max(MIN_LOOP_INTERVAL_SEC, v)


def probs_from_side_conf(side: str, confidence: float) -> dict[str, float]:
    """Build buy/sell/flat probability bars from LLM side + confidence.

    Abstain/hold maps to flat. Single conf → mass on chosen side, remainder
    split across the other two (flat gets more of the residual).
    """
    conf = max(0.0, min(1.0, float(confidence or 0)))
    s = (side or "flat").lower().strip()
    if s in ("hold", "abstain"):
        s = "flat"
    if s not in ("buy", "sell", "flat"):
        s = "flat"
    residual = 1.0 - conf
    if s == "buy":
        buy, sell, flat = conf, residual * 0.35, residual * 0.65
    elif s == "sell":
        sell, buy, flat = conf, residual * 0.35, residual * 0.65
    else:
        flat, buy, sell = max(conf, 0.5), residual * 0.5, residual * 0.5
        if conf < 0.5:
            # Low-conf flat: spread more evenly but keep flat dominant
            flat = 0.5 + conf * 0.5
            rem = 1.0 - flat
            buy = rem * 0.5
            sell = rem * 0.5
    total = buy + sell + flat
    if total <= 0:
        return {"buy": 0.0, "sell": 0.0, "flat": 1.0}
    return {
        "buy": round(buy / total, 3),
        "sell": round(sell / total, 3),
        "flat": round(flat / total, 3),
    }


class DecisionRing:
    """Thread-safe ring buffer persisted to decisions.json."""

    def __init__(self, path: Path, max_events: int = DECISIONS_MAX) -> None:
        self.path = path
        self.max_events = max_events
        self._lock = threading.RLock()
        self._seq = 0
        self._events: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            self._events = []
            self._seq = 0
            return
        try:
            import json

            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._events = list(raw.get("events") or [])
                self._seq = int(raw.get("seq") or 0)
            elif isinstance(raw, list):
                self._events = list(raw)
                self._seq = max((int(e.get("seq") or 0) for e in self._events), default=0)
            else:
                self._events = []
                self._seq = 0
        except Exception:
            # Fail closed: backup corrupt file; do not silent-overwrite with empty
            try:
                bak = self.path.with_suffix(self.path.suffix + ".corrupt.bak")
                if self.path.exists() and not bak.exists():
                    bak.write_bytes(self.path.read_bytes())
            except OSError:
                pass
            self._events = []
            self._seq = 0

    def _persist(self) -> None:
        import json

        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"seq": self._seq, "events": self._events}
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            ev = dict(event)
            ev.setdefault("id", str(uuid.uuid4()))
            ev["seq"] = self._seq
            ev.setdefault("ts", datetime.now(timezone.utc).isoformat())
            ev.setdefault("sim", True)
            ev.setdefault("dry_run", True)
            self._events.insert(0, ev)
            self._events = self._events[: self.max_events]
            self._persist()
            return ev

    def patch(self, event_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Merge updates onto an existing event by id (outcome stamps)."""
        if not event_id:
            return None
        with self._lock:
            for e in self._events:
                if e.get("id") == event_id:
                    e.update(updates or {})
                    self._persist()
                    return dict(e)
            return None

    def since(
        self,
        *,
        since_iso: Optional[str] = None,
        since_id: Optional[str] = None,
        since_seq: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if since_seq is not None:
            try:
                seq = int(since_seq)
            except (TypeError, ValueError):
                seq = 0
            events = [e for e in events if int(e.get("seq") or 0) > seq]
        elif since_id:
            match_seq = None
            for e in events:
                if e.get("id") == since_id:
                    match_seq = int(e.get("seq") or 0)
                    break
            if match_seq is not None:
                events = [e for e in events if int(e.get("seq") or 0) > match_seq]
        elif since_iso:
            try:
                since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
            except ValueError:
                since_dt = None
            if since_dt is not None:
                filtered = []
                for e in events:
                    try:
                        ts = datetime.fromisoformat(str(e.get("ts", "")).replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts > since_dt:
                        filtered.append(e)
                events = filtered
        return events[: max(1, min(limit, 500))]

    def latest(self, n: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._events[:n])

    def latency_stats(self, n: int = 40) -> dict[str, Any]:
        with self._lock:
            samples = [
                int(e["latency_ms"])
                for e in self._events[:n]
                if e.get("latency_ms") is not None and e.get("event") in ("decision", "intent")
            ]
        if not samples:
            return {"last_ms": None, "avg_ms": None, "n": 0}
        return {
            "last_ms": samples[0],
            "avg_ms": int(round(sum(samples) / len(samples))),
            "n": len(samples),
        }

    @property
    def last_ts(self) -> Optional[str]:
        with self._lock:
            if not self._events:
                return None
            return self._events[0].get("ts")

    @property
    def last_decision(self) -> Optional[dict[str, Any]]:
        with self._lock:
            for e in self._events:
                if e.get("event") in ("decision", "intent"):
                    return dict(e)
            return None

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


class PaperLoop:
    """Background decision loop — one ticker per tick, round-robin."""

    def __init__(
        self,
        *,
        decisions: DecisionRing,
        get_deps: Callable[[], dict[str, Any]],
    ) -> None:
        self.decisions = decisions
        self.get_deps = get_deps
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rr = 0
        self._last_decision_ts: Optional[str] = None
        self._last_skip: Optional[str] = None
        self._running_flag = False
        self._generation = 0
        self._cycle_count = 0
        self._lock = threading.RLock()
        self._skip_last: dict[str, float] = {}
        # One trade-brain decision in flight (watchlist scan stays parallel elsewhere)
        self._decision_in_flight = False
        self._decision_started_at = 0.0
        self._last_cycle_at: str | None = None
        self._last_error: str | None = None
        self._error_streak = 0
        self._next_retry_at = 0.0
        self._pending_intents: dict[str, dict[str, Any]] = {}
        self._session_totals: dict[str, Any] = {
            "late_blocks": 0,
            "intents": 0,
            "fills": 0,
            "model_usd": 0.0,
            "friction_usd": 0.0,
            "paper_pnl": 0.0,
            "capped": 0,
            "incoherent": 0,
            "cancels": 0,
        }

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._running_flag)

    def status(self, cfg: Optional[dict] = None) -> dict[str, Any]:
        cfg = cfg or {}
        interval = clamp_interval(cfg.get("loop_interval_sec", DEFAULT_LOOP_INTERVAL_SEC))
        rth_ok = is_rth()
        session_on = bool(cfg.get("session_active")) and bool(cfg.get("loop_enabled"))
        mode_ok = cfg.get("mode") in ("auto_paper", "manual")
        actively = self.running and session_on and mode_ok
        lat = self.decisions.latency_stats()
        with self._lock:
            totals = dict(self._session_totals)
            in_flight = bool(self._decision_in_flight)
            pending_n = len(self._pending_intents)
        return {
            "running": actively,
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "rth_ok": rth_ok,
            "rth_only": bool(cfg.get("rth_only", True)),
            "outside_rth": bool(cfg.get("rth_only", True)) and not rth_ok,
            "last_decision_ts": self._last_decision_ts or self.decisions.last_ts,
            "interval_sec": interval,
            "last_skip": self._last_skip,
            "rr_index": self._rr,
            "loop_enabled": bool(cfg.get("loop_enabled")),
            "latency": lat,
            "last_decision": self.decisions.last_decision,
            "max_session_loss_usd": cfg.get("max_session_loss_usd"),
            "decision_in_flight": in_flight,
            "in_flight_ticker": getattr(self, "_in_flight_ticker", None) if in_flight else None,
            "pending_intents": pending_n,
            "session_totals": totals,
            "brain_mode": cfg.get("brain_mode") or cfg.get("model"),
            "automation_health": {
                "last_cycle_at": self._last_cycle_at,
                "last_error": self._last_error,
                "error_streak": self._error_streak,
                "retry_in_sec": max(0, round(self._next_retry_at - time.time(), 1)),
            },
        }

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                self._running_flag = True
                return
            self._stop.clear()
            self._running_flag = True
            self._thread = threading.Thread(target=self._run, name="paper-loop", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._running_flag = False
        with self._lock:
            self._generation += 1

    def session_totals(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._session_totals)

    def _roll_totals_day(self) -> None:
        """Session totals are per trading day (NY). A session left on overnight used
        to carry yesterday's model $ / fills into today. Caller holds self._lock."""
        try:
            day = datetime.now(NY_TZ).strftime("%Y-%m-%d") if NY_TZ else datetime.now().strftime("%Y-%m-%d")
        except Exception:
            return
        prev = getattr(self, "_totals_day", None)
        if prev is None:
            self._totals_day = day
        elif prev != day:
            self._totals_day = day
            for k, v in list(self._session_totals.items()):
                self._session_totals[k] = 0.0 if isinstance(v, float) else 0

    def _bump_total(self, key: str, amount: float | int = 1) -> None:
        with self._lock:
            self._roll_totals_day()
            cur = self._session_totals.get(key, 0) or 0
            try:
                if isinstance(cur, float) or isinstance(amount, float):
                    self._session_totals[key] = round(float(cur) + float(amount), 6)
                else:
                    self._session_totals[key] = int(cur) + int(amount)
            except Exception:
                self._session_totals[key] = cur

    def _set_pending_intent(self, ticker: str, intent: dict[str, Any] | None) -> None:
        with self._lock:
            if not ticker:
                return
            if intent is None:
                self._pending_intents.pop(ticker, None)
            else:
                self._pending_intents[ticker] = dict(intent)

    def cancel_pending_intent(
        self,
        ticker: str,
        *,
        reason: str,
        append_journal: Callable | None = None,
    ) -> bool:
        """Clear resting paper limit/intent for symbol so stale ideas cannot fill later."""
        with self._lock:
            had = ticker in self._pending_intents
            self._pending_intents.pop(ticker, None)
            if had:
                self._session_totals["cancels"] = int(self._session_totals.get("cancels") or 0) + 1
        if had:
            self.decisions.append(
                {
                    "event": "cancel",
                    "ticker": ticker,
                    "side": "hold",
                    "decision": "hold",
                    "action": "Cancel",
                    "hold": True,
                    "abstain": True,
                    "filled": False,
                    "late": "late" in (reason or "").lower(),
                    "status": "cancelled",
                    "reason": reason,
                    "sim": True,
                    "dry_run": True,
                    "simulated": True,
                }
            )
            if append_journal:
                try:
                    append_journal("intent_cancelled", {"ticker": ticker, "reason": reason})
                except Exception:
                    pass
        return had

    def reset_session_totals(self) -> None:
        with self._lock:
            self._session_totals = {
                "late_blocks": 0,
                "intents": 0,
                "fills": 0,
                "model_usd": 0.0,
                "friction_usd": 0.0,
                "paper_pnl": 0.0,
                "capped": 0,
                "incoherent": 0,
                "cancels": 0,
            }
            self._pending_intents.clear()

    def _run(self) -> None:
        while not self._stop.is_set():
            slept = 5
            try:
                retry_in = self._next_retry_at - time.time()
                if retry_in > 0:
                    self._stop.wait(timeout=min(retry_in, 60))
                    continue
                slept = self._tick()
                self._last_cycle_at = datetime.now(timezone.utc).isoformat()
                self._last_error = None
                self._error_streak = 0
                self._next_retry_at = 0.0
            except Exception as exc:  # noqa: BLE001
                self._error_streak = min(self._error_streak + 1, 6)
                self._last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
                self._next_retry_at = time.time() + min(60.0, 5.0 * (2 ** (self._error_streak - 1)))
                try:
                    deps = self.get_deps()
                    append_journal = deps.get("append_journal")
                    if append_journal:
                        append_journal(
                            "paper_loop_error",
                            {
                                "error": self._last_error,
                                "error_streak": self._error_streak,
                                "retry_in_sec": round(self._next_retry_at - time.time(), 1),
                            },
                        )
                except Exception:
                    pass
                slept = min(60, 5 * (2 ** (self._error_streak - 1)))
            self._stop.wait(timeout=max(1, slept))

    def _tick(self) -> int:
        deps = self.get_deps()
        load_config = deps["load_config"]
        append_journal = deps["append_journal"]
        load_ledger = deps["load_ledger"]
        daily_target_progress = deps["daily_target_progress"]
        get_preset = deps["get_preset"]
        execute_decision = deps["execute_loop_decision"]
        analyze_ticker = deps["analyze_ticker"]
        trade_thesis = deps["trade_thesis"]
        fetch_last_price = deps.get("fetch_last_price")
        cancel_queue = deps.get("cancel_pending_signals")

        def _cancel_symbol(sym: str, reason: str) -> None:
            self.cancel_pending_intent(sym, reason=reason, append_journal=append_journal)
            if cancel_queue:
                try:
                    cancel_queue(sym, reason)
                except Exception:
                    pass

        cfg = load_config()
        interval = clamp_interval(cfg.get("loop_interval_sec", DEFAULT_LOOP_INTERVAL_SEC))

        # Stop/take-profit exits run even after STOP: open positions stay protected.
        try:
            check_exits = deps.get("check_paper_exit_intents")
            if check_exits:
                check_exits(cfg)
        except Exception:
            pass
        # Once-a-day midday risk check (cuts big losers, protects winners).
        try:
            midday = deps.get("midday_risk_check")
            if midday:
                midday(cfg)
        except Exception:
            pass
        # Housekeeping whenever session is active (equity curve / pending outcomes),
        # including Ask me first (manual) — not gated on auto_paper.
        if cfg.get("session_active"):
            try:
                refresh_eq = deps.get("maybe_refresh_equity_curve")
                if refresh_eq:
                    refresh_eq(cfg)
            except Exception:
                pass
            try:
                check_out = deps.get("check_decision_outcomes")
                if check_out:
                    check_out()
            except Exception:
                pass

        if not self._running_flag:
            return 5
        if not cfg.get("session_active"):
            self._last_skip = "session_inactive"
            return 5
        if not cfg.get("loop_enabled", True):
            self._last_skip = "loop_disabled"
            return 5
        # auto_paper: auto-execute; manual (Ask me first): enqueue Waiting
        if cfg.get("mode") not in ("auto_paper", "manual"):
            self._last_skip = "mode_not_auto_paper"
            return 5

        rth_only = bool(cfg.get("rth_only", True))
        rth_ok = is_rth()
        if rth_only and not rth_ok:
            self._last_skip = "outside_rth"
            self._emit_skip(
                "loop_skip_rth",
                {"rth_ok": False, "rth_only": True, "message": "outside RTH — loop paused"},
                append_journal,
                throttle_key="rth",
            )
            return min(interval, 60)

        ledger = load_ledger()
        progress = daily_target_progress(cfg, ledger)
        if progress.get("target_hit"):
            self._last_skip = "target_hit"
            self._emit_skip(
                "loop_skip_target",
                {"pnl": progress.get("pnl"), "target": progress.get("target_usd")},
                append_journal,
                throttle_key="target",
            )
            # API pack: push alert (webhook / UI queue)
            try:
                import desk_alerts as _da
                _da.alert_goal_hit(progress.get("pnl"))
            except Exception:
                pass
            return interval

        loss_limit = cfg.get("max_session_loss_usd")
        if loss_limit is None:
            preset = get_preset(cfg.get("risk_preset"))
            equity = float(ledger.get("equity") or cfg.get("paper_equity") or 100_000)
            loss_limit = equity * (float(preset.get("max_daily_loss_pct") or 2) / 100.0)
        else:
            loss_limit = float(loss_limit)
        pnl = float(progress.get("pnl") or 0)
        # Prefer realized+open MTM when helper provided
        session_pnl_fn = deps.get("session_pnl_with_mtm")
        if session_pnl_fn:
            try:
                pnl = float(session_pnl_fn(ledger))
            except Exception:
                pass
        ks = cfg.get("kill_switch") or {}
        if ks.get("armed") and ks.get("max_daily_loss_usd") is not None:
            loss_limit = min(loss_limit, abs(float(ks["max_daily_loss_usd"])))
        if pnl <= -abs(loss_limit):
            self._last_skip = "max_loss"
            self._emit_skip(
                "loop_skip_loss",
                {"pnl": pnl, "max_session_loss_usd": loss_limit},
                append_journal,
                throttle_key="loss",
            )
            try:
                import desk_alerts as _da
                _da.alert_kill_or_loss("max_loss", {"pnl": pnl, "limit": loss_limit})
            except Exception:
                pass
            return interval

        bleed_fn = deps.get("bleed_status")
        if bleed_fn:
            try:
                bs = bleed_fn(ledger, cfg)
            except Exception:
                bs = {}
            if bs.get("paused"):
                self._last_skip = "bleed_pause"
                self._emit_skip("loop_skip_bleed", bs, append_journal, throttle_key="bleed")
                try:
                    import desk_alerts as _da

                    _da.emit(
                        "bleed",
                        f"Paused: last {bs.get('closed_trades')} trades lost ${abs(float(bs.get('net_usd') or 0)):,.2f} after costs",
                        detail=bs,
                        level="error",
                        dedupe_key=f"bleed|{bs.get('since')}",
                    )
                except Exception:
                    pass
                return interval

        symbols = prune_watchlist_for_trading(
            list(cfg.get("watchlist") or []),
            focus=str(cfg.get("watchlist_focus") or "liquid"),
        )
        # Whole-market radar: merge hot movers into focus (no LLM here).
        if bool(cfg.get("radar_enabled")):
            try:
                radar_fn = deps.get("radar_hot_symbols")
                radar_syms = list(radar_fn(cfg) if callable(radar_fn) else [])
            except Exception:
                radar_syms = []
            if radar_syms:
                symbols = merge_radar_into_focus(
                    symbols,
                    radar_syms,
                    cap_extra=min(12, int(cfg.get("radar_top_n") or 20)),
                )
                # Opportunistic cache refresh (throttled inside market_radar)
                refresh_fn = deps.get("radar_maybe_refresh")
                if callable(refresh_fn):
                    try:
                        refresh_fn(cfg)
                    except Exception:
                        pass
        if not symbols:
            self._last_skip = "empty_watchlist"
            return interval

        idx = self._rr % len(symbols)
        ticker = symbols[idx]
        self._rr = (self._rr + 1) % max(1, len(symbols))

        # One decision in flight: atomic claim. Overlap → late/hold skip (do not stack).
        with self._lock:
            busy = bool(self._decision_in_flight)
            if not busy:
                self._decision_in_flight = True
                self._decision_started_at = time.time()
                self._in_flight_ticker = ticker
        if busy:
            self._last_skip = "decision_in_flight"
            self._bump_total("late_blocks", 1)
            probs = probs_from_side_conf("flat", 1.0)
            saved = self.decisions.append({
                "event": "intent",
                "ticker": ticker,
                "side": "hold",
                "decision": "hold",
                "action": "Hold",
                "intended_side": "flat",
                "confidence": 0,
                "probs": probs,
                "buy_prob": probs["buy"],
                "sell_prob": probs["sell"],
                "flat_prob": probs["flat"],
                "late": True,
                "hold": True,
                "abstain": True,
                "filled": False,
                "status": "skipped",
                "error": "decision_in_flight",
                "sim": True,
                "dry_run": True,
                "simulated": True,
            })
            self._last_decision_ts = saved.get("ts")
            # Do not cancel a different symbol's resting intent just because we skipped
            # this tick — only journal skip. Queue cancel still optional for hygiene.
            return max(5, min(interval, 15))

        t0 = time.perf_counter()
        deadline_sec = max(8.0, float(interval) * 0.85)
        mid = None
        bid = None
        ask = None

        try:
            # Pre-fill stop check (generation) before heavy work
            gen_pre = self._generation
            if (not self._running_flag) or self._generation != gen_pre:
                self._last_skip = "session_stopped"
                return interval

            analysis = analyze_ticker(ticker)
            if analysis.get("error"):
                raise RuntimeError(str(analysis["error"]))
            mid = analysis.get("price")
            if mid is None and fetch_last_price:
                mid = fetch_last_price(ticker)
            entry = analysis.get("entry_quality") or {}
            if not isinstance(entry, dict) or not entry:
                # Missing entry_quality → abstain (no LLM spend)
                latency_ms = int((time.perf_counter() - t0) * 1000)
                probs = probs_from_side_conf("flat", 0.0)
                saved = self.decisions.append({
                    "event": "intent", "ticker": ticker, "side": "hold",
                    "decision": "hold", "action": "Hold", "intended_side": "flat",
                    "confidence": 0, "probs": probs, "buy_prob": probs["buy"],
                    "sell_prob": probs["sell"], "flat_prob": probs["flat"],
                    "mid": mid, "latency_ms": latency_ms, "hold": True,
                    "abstain": True, "filled": False, "status": "held",
                    "error": "missing_entry_quality", "rth_ok": rth_ok,
                    "sim": True, "dry_run": True, "simulated": True,
                })
                self._last_decision_ts = saved.get("ts")
                return interval
            lateness_label = entry.get("label") if isinstance(entry, dict) else None
            verdict = analysis.get("verdict")
            quote_bits = analysis.get("quote") if isinstance(analysis.get("quote"), dict) else {}
            bid = quote_bits.get("bid")
            ask = quote_bits.get("ask")

            # Gate-then-LLM: hard gates before spending Gemini budget
            allow_late = bool(cfg.get("allow_late", False))
            llm_on = bool(cfg.get("llm_enabled", True)) and bool(cfg.get("llm_on_scan", True))
            gate_reason = None
            if (verdict or "").upper() not in ("PASS", "WATCH"):
                gate_reason = f"verdict_{(verdict or 'missing').upper()}"
            elif (lateness_label or "").lower() in ("late", "chasing") and not allow_late:
                gate_reason = f"lateness_{(lateness_label or '').lower()}"
            elif not llm_on:
                gate_reason = "llm_on_scan_disabled"

            if gate_reason:
                thesis = {
                    "side": "flat", "confidence": 0.0,
                    "thesis": f"Gated before LLM: {gate_reason}",
                    "error": gate_reason, "llm_model": None,
                    "brain_mode": cfg.get("brain_mode"),
                    "model_cost_usd": 0.0,
                }
            else:
                thesis = trade_thesis(analysis, cfg)

            elapsed = time.perf_counter() - t0
            decision_late = elapsed > deadline_sec
            latency_ms = int(elapsed * 1000)

            llm_error = thesis.get("error")
            raw_side = (thesis.get("side") or "flat").lower().strip()
            if raw_side not in ("buy", "sell", "flat"):
                raw_side = "flat"
            try:
                from llm_trader import safe_confidence as _safe_conf

                _cv = _safe_conf(thesis.get("confidence"))
            except Exception:  # noqa: BLE001
                _cv = None
            if _cv is None:
                confidence = 0.0
                raw_side = "flat"
            else:
                confidence = _cv
            thesis_text = thesis.get("thesis")
            brain_mode = thesis.get("brain_mode") or cfg.get("brain_mode") or "gemini"
            model_cost = float(thesis.get("model_cost_usd") or 0.0)
            if model_cost:
                self._bump_total("model_usd", model_cost)

            # Model probs (honest even when capped / held)
            if isinstance(thesis.get("probs"), dict):
                probs = {
                    "buy": float(thesis["probs"].get("buy") or 0),
                    "sell": float(thesis["probs"].get("sell") or 0),
                    "flat": float(thesis["probs"].get("flat") or 0),
                }
                totp = probs["buy"] + probs["sell"] + probs["flat"]
                if totp <= 0:
                    probs = probs_from_side_conf(raw_side, confidence)
                else:
                    probs = {k: round(v / totp, 3) for k, v in probs.items()}
            else:
                probs = probs_from_side_conf(
                    "flat" if llm_error else raw_side,
                    0.0 if llm_error else confidence,
                )

            # Spread bps from quote when present
            spread_bps = None
            try:
                if bid is not None and ask is not None and mid:
                    spread_bps = round(((float(ask) - float(bid)) / float(mid)) * 10000.0, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                spread_bps = None

            # Shadow coherence auditor (observe-only unless SHADOW_GATE)
            shadow = None
            shadow_fn = deps.get("shadow_audit")
            if shadow_fn and raw_side in ("buy", "sell") and thesis_text and not llm_error:
                try:
                    shadow = shadow_fn(raw_side, thesis_text, analysis=analysis, cfg=cfg)
                except Exception as exc:  # noqa: BLE001
                    shadow = {"coherent": True, "label": "error", "reason": str(exc)[:120]}
            if shadow and shadow.get("model_cost_usd"):
                try:
                    self._bump_total("model_usd", float(shadow.get("model_cost_usd") or 0))
                except (TypeError, ValueError):
                    pass
            if shadow and not shadow.get("coherent"):
                self._bump_total("incoherent", 1)
                append_journal(
                    "shadow_incoherent",
                    {"ticker": ticker, "side": raw_side, "shadow": shadow},
                )

            # Prism-style advisory panel (observe-only by default; never flips side)
            advisory = None
            policy_label = None
            soft_kill = False
            advisory_fn = deps.get("run_advisory_panel")
            if advisory_fn and not llm_error:
                try:
                    advisory = advisory_fn(
                        raw_side,
                        thesis_text or "",
                        analysis=analysis,
                        cfg=cfg,
                    )
                except Exception as exc:  # noqa: BLE001
                    advisory = {
                        "enabled": True,
                        "method": "error",
                        "scores": {},
                        "policy_label": None,
                        "observe_only": True,
                        "size_mult": 1.0,
                        "reason": str(exc)[:120],
                    }
            if advisory and advisory.get("model_cost_usd"):
                try:
                    self._bump_total("model_usd", float(advisory.get("model_cost_usd") or 0))
                except (TypeError, ValueError):
                    pass
            if isinstance(advisory, dict):
                policy_label = advisory.get("policy_label")
                # Soft size only — never flip buy↔sell; never open when directional hold
                try:
                    amult = float(advisory.get("size_mult") or 1.0)
                except (TypeError, ValueError):
                    amult = 1.0
                if amult < 1.0 and amult > 0:
                    thesis = dict(thesis)
                    thesis["advisory_size_mult"] = amult
                # When soft size on and KILL (size_mult 0) → treat as hold later
                soft_kill = bool(amult <= 0 and deps.get("advisory_soft_size_enabled") and deps.get("advisory_soft_size_enabled")())

            # API pack: macro calendar gate (Fed/CPI/earnings) — size + Ask-me-first hint
            try:
                import macro_calendar as _mc
                _macro = _mc.risk_adjustment(ticker, cfg)
                if _macro.get("size_mult", 1.0) < 1.0:
                    thesis = dict(thesis)
                    prev_m = float(thesis.get("advisory_size_mult") or 1.0)
                    thesis["advisory_size_mult"] = round(prev_m * float(_macro["size_mult"]), 4)
                    thesis["macro_size_mult"] = _macro["size_mult"]
                if _macro.get("force_ask_first"):
                    thesis = dict(thesis)
                    thesis["macro_force_ask_first"] = True
                if _macro.get("butler_note"):
                    thesis = dict(thesis)
                    thesis["macro_butler_note"] = _macro.get("butler_note")
                    thesis["macro_reasons"] = _macro.get("reasons") or []
            except Exception:
                pass

            # Confidence gate (P0): below threshold → hold, intent only, no fill
            low_confidence = False
            min_conf_fn = deps.get("min_decision_confidence")
            try:
                if callable(min_conf_fn):
                    try:
                        min_conf = float(min_conf_fn(cfg))
                    except TypeError:
                        min_conf = float(min_conf_fn())
                else:
                    min_conf = float(min_conf_fn if min_conf_fn is not None else 0.55)
            except (TypeError, ValueError):
                min_conf = 0.55
            if not llm_error and raw_side in ("buy", "sell"):
                chosen_prob = float(probs.get(raw_side) or 0.0)
                max_dir = max(float(probs.get("buy") or 0), float(probs.get("sell") or 0))
                conf_metric = max(chosen_prob, confidence)
                # Also respect max(buy,sell) when chosen is missing/zero
                if chosen_prob <= 0:
                    conf_metric = max(max_dir, confidence)
                if conf_metric < min_conf or confidence < min_conf:
                    low_confidence = True

            decision = "hold"
            late = bool(decision_late)
            hold = True
            filled = False
            fill_info = None
            result = None  # execute_loop_decision payload when fill path runs
            size_at_price = None
            error = None
            capped = False
            intent_status = "pending"

            # Late / miss tick deadline → hold, do NOT fill, cancel stale intent
            if decision_late:
                late = True
                hold = True
                decision = "hold"
                error = "decision_late"
                intent_status = "late"
                self._bump_total("late_blocks", 1)
                _cancel_symbol(ticker, "decision_late")
            elif llm_error:
                hold = True
                decision = "hold"
                error = str(llm_error)
                intent_status = "error"
                _cancel_symbol(ticker, f"decision_error:{error}")
            elif (lateness_label or "").lower() in ("late", "chasing"):
                late = True  # entry lateness label (separate from decision_late)
                if raw_side == "flat":
                    decision = "hold"
                    hold = True
                    intent_status = "held"
                else:
                    # still may try fill path unless allow_late gates inside execute
                    pass

            if not decision_late and not llm_error:
                # Confidence gate — force hold, no fill
                if low_confidence:
                    hold = True
                    decision = "hold"
                    error = "low_confidence"
                    intent_status = "held"
                    capped = False
                    self._bump_total("low_confidence", 1)

                # Soft advisory KILL (only when ADVISORY_SOFT_SIZE=1) → hold
                if soft_kill and not low_confidence:
                    hold = True
                    decision = "hold"
                    error = error or "advisory_kill"
                    intent_status = "held"

                # Optional shadow gate (default off) — observe badge always
                gate_shadow = False
                shadow_gate_fn = deps.get("shadow_gate_enabled")
                if shadow and not shadow.get("coherent") and shadow_gate_fn and shadow_gate_fn():
                    gate_shadow = True
                    hold = True
                    decision = "hold"
                    error = "shadow_incoherent"
                    intent_status = "held"
                    capped = False

                gate_conf = low_confidence or soft_kill
                if not gate_shadow and not gate_conf and raw_side == "flat":
                    decision = "hold"
                    hold = True
                    intent_status = "held"
                elif not gate_shadow and not gate_conf and raw_side in ("buy", "sell"):
                    gen_snap = self._generation
                    cfg_chk = load_config()
                    if (
                        (not self._running_flag)
                        or self._generation != gen_snap
                        or (not cfg_chk.get("session_active"))
                        or (not cfg_chk.get("loop_enabled"))
                        or cfg_chk.get("mode") not in ("auto_paper", "manual")
                    ):
                        result = {"ok": False, "abstain": True, "reason": "session_stopped"}
                    else:
                        result = execute_decision(
                            analysis=analysis,
                            thesis=thesis,
                            cfg=cfg_chk,
                            mid=mid,
                        )
                        if (
                            result.get("ok")
                            and result.get("fill")
                            and not result.get("abstain")
                            and (
                                (not self._running_flag)
                                or self._generation != gen_snap
                            )
                        ):
                            reverse_fn = deps.get("reverse_paper_fill")
                            reversed_ok = False
                            if reverse_fn:
                                try:
                                    rev = reverse_fn(result.get("fill"), cfg_chk)
                                    reversed_ok = bool(rev.get("ok"))
                                except Exception:
                                    reversed_ok = False
                            if reversed_ok:
                                result = {
                                    "ok": False,
                                    "abstain": True,
                                    "reason": "session_stopped_fill_reversed",
                                    "fill_reversed": True,
                                    "prior_fill": result.get("fill"),
                                }
                            else:
                                result = {
                                    "ok": True,
                                    "abstain": False,
                                    "fill": result.get("fill"),
                                    "signal": result.get("signal"),
                                    "filled_before_stop": True,
                                    "reason": "filled_before_stop",
                                }
                    reason = str(result.get("reason") or result.get("error") or "")
                    cap_hints = (
                        "max position", "position size", "kill-switch: max position",
                        "Insufficient paper cash", "max trades", "naked_short",
                        "size", "cap",
                    )
                    if result.get("abstain"):
                        decision = "hold"
                        hold = True
                        error = reason
                        filled = False
                        fill_info = result.get("prior_fill") if result.get("fill_reversed") else None
                        intent_status = "held"
                        if any(h.lower() in reason.lower() for h in cap_hints):
                            capped = True
                            self._bump_total("capped", 1)
                    elif result.get("ok") and (result.get("pending") or result.get("queued")):
                        # Ask me first — enqueued for Waiting Approve/Skip
                        decision = raw_side
                        hold = True
                        filled = False
                        fill_info = None
                        intent_status = "pending"
                        try:
                            import desk_alerts as _da
                            _da.alert_waiting_enqueue(ticker, raw_side)
                        except Exception:
                            pass
                        sig_q = result.get("signal") or {}
                        sh = sig_q.get("suggested_shares")
                        px = sig_q.get("signal_price")
                        if sh is not None and px is not None:
                            size_at_price = f"{sh}@{px}"
                    elif result.get("ok") and result.get("fill"):
                        decision = raw_side
                        hold = False
                        filled = True
                        fill_info = result.get("fill")
                        intent_status = "filled"
                        if result.get("filled_before_stop"):
                            error = "filled_before_stop"
                        if fill_info:
                            size_at_price = (
                                f"{fill_info.get('shares')}@{fill_info.get('price')}"
                            )
                    else:
                        decision = "hold"
                        hold = True
                        filled = False
                        error = reason or "fill_blocked"
                        intent_status = "held"
                        if any(h.lower() in (error or "").lower() for h in cap_hints):
                            capped = True
                            self._bump_total("capped", 1)

            latency_ms = int((time.perf_counter() - t0) * 1000)
            ledger2 = load_ledger()
            pnl_now = float(daily_target_progress(cfg, ledger2).get("pnl") or 0)
            self._bump_total("paper_pnl", 0)  # ensure key exists
            with self._lock:
                self._session_totals["paper_pnl"] = round(pnl_now, 2)
            pos = next(
                (p for p in (ledger2.get("positions") or []) if p.get("ticker") == ticker),
                None,
            )

            action_label = {
                "buy": "Buying",
                "sell": "Selling",
                "hold": "Hold",
                "flat": "Hold",
            }.get(decision, "Hold")

            intent_id = str(uuid.uuid4())
            # INTENT event — never treated as a fill (side/decision always hold here)
            intent_event = {
                "event": "intent",
                "intent_id": intent_id,
                "ticker": ticker,
                "side": "hold",
                "decision": "hold",
                "action": "Hold",
                "intended_side": raw_side if not llm_error else "flat",
                "model_side": raw_side if not llm_error else "flat",
                "confidence": round(confidence, 3),
                "probs": probs,
                "buy_prob": probs["buy"],
                "sell_prob": probs["sell"],
                "flat_prob": probs["flat"],
                "mid": mid,
                "bid": bid,
                "ask": ask,
                "spread_bps": spread_bps,
                "latency_ms": latency_ms,
                "latencyMs": latency_ms,
                "quote": {"mid": mid, "bid": bid, "ask": ask, "spread_bps": spread_bps},
                "position": pos,
                "pnl": pnl_now,
                "pnl_delta": round(pnl_now - pnl, 2),
                "late": late,
                "data_error": bool(error) and not late,
                "hold": True,  # intent row is never a live fill
                "abstain": not filled,
                "filled": False,  # intent is never a fill
                "fill": None,
                "size_at_price": None,
                "verdict": verdict,
                "lateness_label": lateness_label,
                "brain_mode": brain_mode,
                "shadow_claude": thesis.get("shadow_claude"),
                "thesis": (thesis_text or "")[:300] if thesis_text else None,
                "horizon": thesis.get("horizon"),
                "horizon_min": thesis.get("horizon_min"),
                "error": error,
                "rth_ok": rth_ok,
                "capped": capped,
                "status": intent_status,
                "brain_mode": brain_mode,
                "model_cost_usd": model_cost,
                "shadow": shadow,
                "coherent": None if not shadow else bool(shadow.get("coherent")),
                "coherence_label": (shadow or {}).get("label"),
                "advisory": advisory,
                "policy_label": policy_label,
                "low_confidence": low_confidence,
                "min_decision_confidence": min_conf,
                "routed": thesis.get("routed"),
                "router_reason": thesis.get("router_reason"),
                "llm_confidence": round(confidence, 3),
                "sim": True,
                "dry_run": True,
                "simulated": True,
            }
            # P1 honesty: persist fill-gate blended conf + macro flags for UI (no fill-path change)
            try:
                if thesis.get("macro_force_ask_first"):
                    intent_event["macro_force_ask_first"] = True
                if thesis.get("macro_butler_note"):
                    intent_event["macro_butler_note"] = thesis.get("macro_butler_note")
                if thesis.get("macro_size_mult") is not None:
                    intent_event["macro_size_mult"] = thesis.get("macro_size_mult")
                if result and isinstance(result, dict):
                    if result.get("macro_force_ask_first"):
                        intent_event["macro_force_ask_first"] = True
                        intent_event["macro_forced_pending"] = bool(
                            result.get("pending") or result.get("queued")
                        )
                    sig_r = result.get("signal") or {}
                    if isinstance(sig_r, dict):
                        if sig_r.get("confidence") is not None:
                            try:
                                intent_event["gate_confidence"] = round(
                                    float(sig_r.get("confidence")), 3
                                )
                            except (TypeError, ValueError):
                                pass
                        if sig_r.get("llm_confidence") is not None:
                            try:
                                intent_event["llm_confidence"] = round(
                                    float(sig_r.get("llm_confidence")), 3
                                )
                            except (TypeError, ValueError):
                                pass
                        if sig_r.get("macro_butler_note"):
                            intent_event["macro_butler_note"] = sig_r.get(
                                "macro_butler_note"
                            )
                        if sig_r.get("macro_size_mult") is not None:
                            intent_event["macro_size_mult"] = sig_r.get(
                                "macro_size_mult"
                            )
                        if sig_r.get("macro_forced_pending"):
                            intent_event["macro_forced_pending"] = True
            except Exception:
                pass
            # P0.3 — schedule horizon mark (helped/hurt/flat) when mid known
            try:
                sched = deps.get("schedule_decision_outcome")
                if sched:
                    sched(intent_event)
            except Exception:
                pass
            saved = self.decisions.append(intent_event)
            self._bump_total("intents", 1)
            self._last_decision_ts = saved.get("ts")
            self._last_skip = None

            if filled and fill_info:
                friction = 0.0
                try:
                    friction = float(fill_info.get("friction_usd") or 0)
                except (TypeError, ValueError):
                    friction = 0.0
                if friction:
                    self._bump_total("friction_usd", friction)
                self._bump_total("fills", 1)
                self._set_pending_intent(ticker, None)
                fill_event = {
                    "event": "fill",
                    "intent_id": intent_id,
                    "ticker": ticker,
                    "side": decision,
                    "decision": decision,
                    "action": action_label,
                    "intended_side": raw_side,
                    "confidence": round(confidence, 3),
                    "llm_confidence": intent_event.get("llm_confidence", round(confidence, 3)),
                    "gate_confidence": intent_event.get("gate_confidence"),
                    "probs": probs,
                    "buy_prob": probs["buy"],
                    "sell_prob": probs["sell"],
                    "flat_prob": probs["flat"],
                    "mid": mid,
                    "bid": bid,
                    "ask": ask,
                    "spread_bps": spread_bps,
                    "latency_ms": latency_ms,
                    "latencyMs": latency_ms,
                    "quote": {"mid": mid, "bid": bid, "ask": ask},
                    "position": pos,
                    "pnl": pnl_now,
                    "pnl_delta": round(pnl_now - pnl, 2),
                    "late": False,
                    "hold": False,
                    "abstain": False,
                    "filled": True,
                    "fill": fill_info,
                    "size_at_price": size_at_price,
                    "friction_usd": friction,
                    "verdict": verdict,
                    "thesis": (thesis_text or "")[:300] if thesis_text else None,
                    "status": "filled",
                    "brain_mode": brain_mode,
                    "capped": False,
                    "advisory": advisory,
                    "policy_label": policy_label,
                    "low_confidence": False,
                    "routed": thesis.get("routed"),
                    "sim": True,
                    "dry_run": True,
                    "simulated": True,
                }
                self.decisions.append(fill_event)
            else:
                # Fills are synchronous — no resting paper limit that can fill later.
                # Clear any prior pending; cancel-on-error still expires queue signals.
                self._set_pending_intent(ticker, None)

            self._cycle_count += 1
            if self._cycle_count % 10 == 0:
                append_journal(
                    "paper_loop_summary",
                    {
                        "cycles": self._cycle_count,
                        "last_ticker": ticker,
                        "pnl": pnl_now,
                        "rr": self._rr,
                        "session_totals": self.session_totals(),
                    },
                )
        except Exception as exc:  # noqa: BLE001
            latency_ms = int((time.perf_counter() - t0) * 1000)
            probs = probs_from_side_conf("flat", 0.0)
            saved = self.decisions.append(
                {
                    "event": "intent",
                    "ticker": ticker,
                    "side": "hold",
                    "decision": "hold",
                    "action": "Hold",
                    "intended_side": "flat",
                    "confidence": 0,
                    "probs": probs,
                    "buy_prob": probs["buy"],
                    "sell_prob": probs["sell"],
                    "flat_prob": probs["flat"],
                    "mid": mid,
                    "bid": bid,
                    "ask": ask,
                    "latency_ms": latency_ms,
                    "quote": {"mid": mid, "bid": bid, "ask": ask},
                    "position": None,
                    "pnl": pnl,
                    "pnl_delta": 0,
                    "late": False,
                    "data_error": True,
                    "hold": True,
                    "abstain": True,
                    "filled": False,
                    "status": "error",
                    "error": str(exc),
                    "rth_ok": rth_ok,
                    "sim": True,
                    "dry_run": True,
                    "simulated": True,
                }
            )
            self._last_decision_ts = saved.get("ts")
            append_journal("paper_loop_tick_error", {"ticker": ticker, "error": str(exc)})
            _cancel_symbol(ticker, f"tick_error:{exc}")
        finally:
            with self._lock:
                self._decision_in_flight = False
                self._decision_started_at = 0.0

        return interval

    def _emit_skip(
        self,
        action: str,
        detail: dict,
        append_journal: Callable,
        *,
        throttle_key: str,
        every_sec: float = 60.0,
    ) -> None:
        now = time.time()
        last = self._skip_last.get(throttle_key, 0)
        if now - last < every_sec:
            return
        self._skip_last[throttle_key] = now
        probs = probs_from_side_conf("flat", 1.0)
        self.decisions.append(
            {
                "event": action,
                "ticker": None,
                "side": "hold",
                "decision": "hold",
                "action": "Hold",
                "hold": True,
                "abstain": True,
                "late": False,
                "filled": False,
                "confidence": 0,
                "probs": probs,
                "sim": True,
                "dry_run": True,
                "detail": detail,
                **detail,
            }
        )
        append_journal(action, detail)
