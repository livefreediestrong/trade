"""Session equity curve, marked horizon outcomes, and paper stop/TP brackets.

Paper-only helpers used by app.py + paper_loop. No live broker.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

EQUITY_CURVE_MAX = 240
EQUITY_CURVE_MIN_GAP_SEC = 25.0
OUTCOME_FLAT_BPS = 12.0


def _ensure_equity_curve_day(ledger: dict[str, Any], today_str: str) -> None:
    if ledger.get("equity_curve_day") != today_str:
        ledger["equity_curve_day"] = today_str
        ledger["equity_curve"] = []


def append_equity_curve_point(
    ledger: dict[str, Any],
    *,
    today_str: str,
    now_iso: str,
    equity_fn: Callable[[dict[str, Any], dict[str, float] | None], float],
    pnl_fn: Callable[[dict[str, Any], dict[str, float] | None], float],
    force: bool = False,
    marks: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Append one equity/pnl sample. Throttled unless force (e.g. on fill)."""
    _ensure_equity_curve_day(ledger, today_str)
    equity = float(equity_fn(ledger, marks))
    pnl = float(pnl_fn(ledger, marks))
    ledger["equity"] = equity
    curve = ledger.setdefault("equity_curve", [])
    if curve and not force:
        try:
            last_ts = datetime.fromisoformat(str(curve[-1].get("ts", "")).replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            gap = (now_dt - last_ts).total_seconds()
            last_eq = float(curve[-1].get("equity") or 0)
            last_pnl = float(curve[-1].get("pnl") or 0)
            if gap < EQUITY_CURVE_MIN_GAP_SEC and abs(equity - last_eq) < 0.05 and abs(pnl - last_pnl) < 0.05:
                return None
            if abs(equity - last_eq) < 0.005 and abs(pnl - last_pnl) < 0.005:
                return None
        except Exception:
            pass
    pt = {"ts": now_iso, "equity": round(equity, 2), "pnl": round(pnl, 2)}
    curve.append(pt)
    while len(curve) > EQUITY_CURVE_MAX:
        curve.pop(0)
    return pt


def scoreboard_butler_line(
    *,
    pnl: float,
    down_from_peak: float,
    target: float | None,
    fills_count: int,
    win_rate: float | None,
    decided: int,
) -> str:
    if fills_count <= 0 and abs(pnl) < 0.01:
        return "Quiet so far — watching the tape for you"
    if down_from_peak >= 1.0:
        if target and target > 0 and pnl < target:
            return f"Down ${down_from_peak:,.0f} from peak — still under your goal"
        return f"Down ${down_from_peak:,.0f} from peak — paper only, stay calm"
    if pnl >= 1.0:
        if target and target > 0:
            rem = target - pnl
            if rem > 0:
                return f"Up ${pnl:,.0f} — ${rem:,.0f} left to your goal"
            return f"Up ${pnl:,.0f} — goal touched on paper"
        return f"Up ${pnl:,.0f} on paper — nice and steady"
    if pnl <= -1.0:
        return f"Down ${abs(pnl):,.0f} today — exits still allowed"
    if decided and win_rate is not None:
        pct = int(round(win_rate * 100))
        return f"{fills_count} fill{'s' if fills_count != 1 else ''} · ~{pct}% helped when closed"
    if fills_count:
        return f"{fills_count} paper fill{'s' if fills_count != 1 else ''} — curve warming up"
    return "Holding the line — no drama"


def build_day_scoreboard(
    ledger: dict[str, Any],
    cfg: dict[str, Any] | None,
    *,
    today_str: str,
    pnl_fn: Callable[[dict[str, Any], dict[str, float] | None], float],
    daily_stats_fn: Callable[[dict[str, Any]], dict[str, Any]],
    ny_tz=None,
) -> dict[str, Any]:
    cfg = cfg or {}
    _ensure_equity_curve_day(ledger, today_str)
    curve = list(ledger.get("equity_curve") or [])
    stats = daily_stats_fn(ledger)
    pnl = float(pnl_fn(ledger, None))
    equity = float(ledger.get("equity") or 0)
    peak = None
    max_dd = 0.0
    for pt in curve:
        try:
            eq = float(pt.get("equity") or 0)
        except (TypeError, ValueError):
            continue
        if peak is None or eq > peak:
            peak = eq
        if peak is not None:
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd
    fills_today: list[dict[str, Any]] = []
    for f in ledger.get("fills") or []:
        ts = str(f.get("ts") or "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if ny_tz is not None:
                dt = dt.astimezone(ny_tz)
            if dt.strftime("%Y-%m-%d") == today_str:
                fills_today.append(f)
        except Exception:
            if ts.startswith(today_str):
                fills_today.append(f)
    fills_count = int(stats.get("trades") or 0) or len(fills_today)
    wins = flats = losses = 0
    for f in fills_today:
        if f.get("realized_pnl") is None:
            continue
        try:
            rp = float(f.get("realized_pnl") or 0)
        except (TypeError, ValueError):
            continue
        if abs(rp) < 0.01:
            flats += 1
        elif rp > 0:
            wins += 1
        else:
            losses += 1
    decided = wins + losses + flats
    win_rate = round(wins / decided, 3) if decided else None
    flatish_rate = round((wins + flats) / decided, 3) if decided else None
    peak_eq = peak if peak is not None else equity
    down_from_peak = round(max(0.0, float(peak_eq) - equity), 2)
    target = cfg.get("daily_profit_target_usd")
    try:
        target_f = float(target) if target not in (None, "") else None
    except (TypeError, ValueError):
        target_f = None
    butler = scoreboard_butler_line(
        pnl=pnl,
        down_from_peak=down_from_peak,
        target=target_f,
        fills_count=fills_count,
        win_rate=win_rate,
        decided=decided,
    )
    return {
        "equity_curve": curve,
        "equity": round(equity, 2),
        "pnl": round(pnl, 2),
        "max_drawdown": round(max_dd, 2),
        "down_from_peak": down_from_peak,
        "fills_count": fills_count,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": win_rate,
        "flatish_rate": flatish_rate,
        "butler": butler,
        "day": today_str,
    }


def parse_pct_or_price(raw: Any) -> tuple[Optional[str], Optional[float]]:
    """Parse user stop/TP input.

    Percent requires an explicit ``%`` suffix (``1%``, ``0.8%``).
    Bare numbers are always prices — never treat ``$24`` / ``24`` as 24%.
    Values ``> 30`` without ``%`` are also prices (belt-and-suspenders).
    """
    if raw is None or raw == "" or raw is False:
        return None, None
    if isinstance(raw, (int, float)):
        v = float(raw)
        if v != v or v == 0:
            return None, None
        return "price", abs(v)
    s = str(raw).strip().lower().replace("$", "").replace(",", "")
    if s in ("", "off", "none", "no", "-"):
        return None, None
    if s.endswith("%"):
        try:
            return "pct", abs(float(s[:-1].strip()))
        except ValueError:
            return None, None
    try:
        v = float(s)
    except ValueError:
        return None, None
    if v == 0 or v != v:
        return None, None
    return "price", abs(v)


def resolve_exit_prices(
    *,
    entry_px: float,
    side: str,
    preset: dict[str, Any],
    signal: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve paper stop/TP from fill price + risk preset geometry.

    Screener absolute ``signal.stop`` / ``signal.target`` are ignored unless the
    user typed values (``body``), or approve stamped ``stop_loss`` / ``take_profit``
    onto the signal. Blank → ``0.8% * stop_r`` stop and ``stop * target_r`` TP,
    matching screener ``price * 0.008 * stop_r`` geometry.
    """
    signal = signal or {}
    body = body or {}

    def _flag_off(src: dict[str, Any]) -> bool:
        return bool(
            src.get("bracket") is False or src.get("no_bracket") or src.get("bracket_off")
        )

    if _flag_off(body) or _flag_off(signal):
        return {"stop_price": None, "take_profit_price": None, "bracket": False}

    # User overrides only — never merge stale screener absolute stop/target.
    src: dict[str, Any] = {}
    user_keys = (
        "stop_loss",
        "take_profit",
        "stop_loss_pct",
        "take_profit_pct",
        "stop_pct",
        "target_pct",
        "use_bracket_defaults",
        "bracket",
        "no_bracket",
        "bracket_off",
    )
    for k in user_keys:
        if k in body:
            src[k] = body[k]
        elif k in signal and k not in ("stop", "target"):
            # stop_loss / take_profit stamped by approve body copy are OK
            if k in (
                "stop_loss",
                "take_profit",
                "stop_loss_pct",
                "take_profit_pct",
                "stop_pct",
                "target_pct",
                "use_bracket_defaults",
                "bracket",
                "no_bracket",
                "bracket_off",
            ):
                src[k] = signal[k]
    # Typed absolute prices only from body (modal), never from signal.stop/target
    if "stop" in body:
        src["stop"] = body["stop"]
    if "target" in body:
        src["target"] = body["target"]

    side_l = (side or "buy").lower()
    is_buy = side_l in ("buy", "long")
    # Align with screener: stop_dist = price * 0.008 * stop_r; target = stop_dist * target_r
    stop_r = float(preset.get("stop_r") or 1.0)
    target_r = float(preset.get("target_r") or 2.0)
    default_stop_pct = 0.8 * stop_r
    default_tp_pct = default_stop_pct * target_r

    stop_raw = src.get("stop_loss", src.get("stop"))
    tp_raw = src.get("take_profit", src.get("target"))
    stop_pct_raw = src.get("stop_loss_pct", src.get("stop_pct"))
    tp_pct_raw = src.get("take_profit_pct", src.get("target_pct"))

    stop_price = None
    tp_price = None

    if stop_pct_raw is not None and stop_pct_raw != "":
        try:
            pct = abs(float(stop_pct_raw))
            stop_price = (
                round(entry_px * (1 - pct / 100.0), 4)
                if is_buy
                else round(entry_px * (1 + pct / 100.0), 4)
            )
        except (TypeError, ValueError):
            stop_price = None
    elif stop_raw is not None and stop_raw != "":
        kind, val = parse_pct_or_price(stop_raw)
        if kind == "pct" and val is not None:
            stop_price = (
                round(entry_px * (1 - val / 100.0), 4)
                if is_buy
                else round(entry_px * (1 + val / 100.0), 4)
            )
        elif kind == "price" and val is not None:
            stop_price = round(val, 4)

    if tp_pct_raw is not None and tp_pct_raw != "":
        try:
            pct = abs(float(tp_pct_raw))
            tp_price = (
                round(entry_px * (1 + pct / 100.0), 4)
                if is_buy
                else round(entry_px * (1 - pct / 100.0), 4)
            )
        except (TypeError, ValueError):
            tp_price = None
    elif tp_raw is not None and tp_raw != "":
        kind, val = parse_pct_or_price(tp_raw)
        if kind == "pct" and val is not None:
            tp_price = (
                round(entry_px * (1 + val / 100.0), 4)
                if is_buy
                else round(entry_px * (1 - val / 100.0), 4)
            )
        elif kind == "price" and val is not None:
            tp_price = round(val, 4)

    specified_keys = (
        "stop_loss",
        "stop",
        "stop_loss_pct",
        "stop_pct",
        "take_profit",
        "target",
        "take_profit_pct",
        "target_pct",
    )
    specified_any = any(k in src for k in specified_keys)
    use_defaults = src.get("use_bracket_defaults", True)

    if use_defaults and not specified_any:
        stop_price = (
            round(entry_px * (1 - default_stop_pct / 100.0), 4)
            if is_buy
            else round(entry_px * (1 + default_stop_pct / 100.0), 4)
        )
        tp_price = (
            round(entry_px * (1 + default_tp_pct / 100.0), 4)
            if is_buy
            else round(entry_px * (1 - default_tp_pct / 100.0), 4)
        )
    elif use_defaults and specified_any:
        if stop_price is None and not any(
            k in src for k in ("stop_loss", "stop", "stop_loss_pct", "stop_pct")
        ):
            stop_price = (
                round(entry_px * (1 - default_stop_pct / 100.0), 4)
                if is_buy
                else round(entry_px * (1 + default_stop_pct / 100.0), 4)
            )
        if tp_price is None and not any(
            k in src for k in ("take_profit", "target", "take_profit_pct", "target_pct")
        ):
            tp_price = (
                round(entry_px * (1 + default_tp_pct / 100.0), 4)
                if is_buy
                else round(entry_px * (1 - default_tp_pct / 100.0), 4)
            )

    # Sanity: finite, positive, and on the correct side of entry. A long's stop must
    # be below entry and its target above (short: reversed). Otherwise the next
    # housekeeping pass would fire an instant "stop" or a losing "take profit".
    import math as _math

    rejected: list[str] = []

    def _ok(px: Optional[float]) -> bool:
        return px is not None and _math.isfinite(px) and px > 0

    if stop_price is not None:
        if not _ok(stop_price) or (is_buy and stop_price >= entry_px) or (not is_buy and stop_price <= entry_px):
            rejected.append(
                f"stop {stop_price} ignored — must be {'below' if is_buy else 'above'} entry {round(entry_px, 4)}"
            )
            stop_price = None
    if tp_price is not None:
        if not _ok(tp_price) or (is_buy and tp_price <= entry_px) or (not is_buy and tp_price >= entry_px):
            rejected.append(
                f"take-profit {tp_price} ignored — must be {'above' if is_buy else 'below'} entry {round(entry_px, 4)}"
            )
            tp_price = None

    # Trailing stop: "3" or "3%" = keep the stop 3% below the best price seen (long).
    trail_pct = None
    raw_trail = body.get("trail_pct", signal.get("trail_pct"))
    if raw_trail not in (None, "", False):
        try:
            tv = float(str(raw_trail).strip().rstrip("%"))
        except (TypeError, ValueError):
            tv = None
        if tv is not None and _math.isfinite(tv) and 0.1 <= tv <= 50:
            trail_pct = round(tv, 3)
            trail_stop = round(entry_px * (1 - tv / 100.0), 4) if is_buy else round(entry_px * (1 + tv / 100.0), 4)
            # The trailing stop replaces the preset default stop. A stop the user typed
            # themselves still wins if it is tighter.
            user_stop = any(k in src for k in ("stop_loss", "stop", "stop_loss_pct", "stop_pct"))
            if (
                stop_price is None
                or not user_stop
                or (is_buy and trail_stop > stop_price)
                or (not is_buy and trail_stop < stop_price)
            ):
                stop_price = trail_stop
        else:
            rejected.append(f"trailing stop {raw_trail} ignored — use a percent between 0.1 and 50")

    bracket_on = stop_price is not None or tp_price is not None
    return {
        "stop_price": stop_price,
        "take_profit_price": tp_price,
        "trail_pct": trail_pct,
        "bracket": bracket_on,
        "rejected": rejected,
        "stop_pct_default": default_stop_pct,
        "target_pct_default": default_tp_pct,
    }


def attach_exit_intents(
    positions: list[dict[str, Any]],
    *,
    ticker: str,
    pos_side: str,
    exits: dict[str, Any],
    now_iso: str,
) -> None:
    if not exits or not exits.get("bracket"):
        return
    t = ticker.upper()
    side_l = (pos_side or "long").lower()
    for p in positions:
        if str(p.get("ticker") or "").upper() != t:
            continue
        if (p.get("side") or "long").lower() != side_l:
            continue
        if exits.get("stop_price") is not None:
            p["stop_price"] = exits["stop_price"]
        else:
            p.pop("stop_price", None)
        if exits.get("take_profit_price") is not None:
            p["take_profit_price"] = exits["take_profit_price"]
        else:
            p.pop("take_profit_price", None)
        if exits.get("trail_pct"):
            p["trail_pct"] = exits["trail_pct"]
            try:
                p["trail_high"] = max(float(p.get("trail_high") or 0), float(p.get("avg_price") or 0))
            except (TypeError, ValueError):
                p["trail_high"] = p.get("avg_price")
        else:
            p.pop("trail_pct", None)
            p.pop("trail_high", None)
        p["exit_bracket"] = True
        p["exit_intents_ts"] = now_iso
        break


def ratchet_trailing_stop(pos: dict[str, Any], mark: float) -> float | None:
    """Move a trailing stop in the trade's favour only. Returns the new stop (or None).

    Long: remember the highest price seen; stop = high × (1 − trail%). Never lowers.
    """
    try:
        pct = float(pos.get("trail_pct") or 0)
        mark = float(mark)
    except (TypeError, ValueError):
        return None
    if pct <= 0 or mark <= 0:
        return None
    side = (pos.get("side") or "long").lower()
    cur = pos.get("stop_price")
    try:
        cur_f = float(cur) if cur is not None else None
    except (TypeError, ValueError):
        cur_f = None
    if side in ("long", "buy"):
        high = max(float(pos.get("trail_high") or 0), mark)
        pos["trail_high"] = high
        new = round(high * (1 - pct / 100.0), 4)
        if cur_f is None or new > cur_f:
            pos["stop_price"] = new
            return new
    else:
        low = min(float(pos.get("trail_high") or mark), mark)
        pos["trail_high"] = low
        new = round(low * (1 + pct / 100.0), 4)
        if cur_f is None or new < cur_f:
            pos["stop_price"] = new
            return new
    return None


def classify_horizon_outcome(
    *,
    intended_side: str,
    mid_at: float,
    mid_now: float,
    flat_bps: float = OUTCOME_FLAT_BPS,
    path_prices: list[float] | None = None,
    slip_bps: float = 0.0,
    fee_bps: float = 0.0,
) -> dict[str, Any]:
    intended = (intended_side or "flat").lower().strip()
    if intended in ("hold",):
        intended = "flat"
    if intended in ("long", "higher", "buying"):
        intended = "buy"
    if intended in ("short", "lower", "selling"):
        intended = "sell"
    if not all(math.isfinite(v) for v in (mid_at, mid_now, slip_bps, fee_bps)) or mid_at <= 0 or mid_now <= 0:
        return {"outcome": None, "error": "no_mid"}
    move_bps = ((mid_now - mid_at) / mid_at) * 10_000.0
    abs_bps = abs(move_bps)
    if intended == "flat":
        label = "helped" if abs_bps <= flat_bps else "hurt"
    elif intended == "buy":
        if abs_bps <= flat_bps:
            label = "flat"
        else:
            label = "helped" if move_bps > 0 else "hurt"
    elif intended == "sell":
        if abs_bps <= flat_bps:
            label = "flat"
        else:
            label = "helped" if move_bps < 0 else "hurt"
    else:
        label = "flat" if abs_bps <= flat_bps else None
    path = []
    for value in path_prices or []:
        try:
            price = float(value)
            if math.isfinite(price) and price > 0:
                path.append(price)
        except (TypeError, ValueError):
            continue
    if not path:
        path = [mid_at, mid_now]
    direction = 1 if intended == "buy" else -1 if intended == "sell" else 0
    favorable = max(((price - mid_at) / mid_at) * 10_000 * direction for price in path) if direction else 0.0
    adverse = min(((price - mid_at) / mid_at) * 10_000 * direction for price in path) if direction else 0.0
    round_trip_cost_bps = max(0.0, float(slip_bps or 0) * 2 + float(fee_bps or 0) * 2)
    executable_move_bps = move_bps * direction - round_trip_cost_bps if direction else 0.0
    return {
        "outcome": label,
        "directional_outcome": label,
        "net_outcome": ("helped" if executable_move_bps > 1e-9 else "hurt" if executable_move_bps < -1e-9 else "flat") if direction else None,
        "move_bps": round(move_bps, 2),
        "mid_at": round(mid_at, 4),
        "mid_now": round(mid_now, 4),
        "intended_side": intended,
        "flat_bps": flat_bps,
        "mfe_bps": round(favorable, 2) if path_prices else None,
        "mae_bps": round(adverse, 2) if path_prices else None,
        "path_samples": len(path_prices or []),
        "path_available": bool(path_prices),
        "round_trip_cost_bps": round(round_trip_cost_bps, 2),
        "executable_move_bps": round(executable_move_bps, 2) if direction else None,
        "paper_only": True,
    }


def schedule_decision_outcome(
    event: dict[str, Any],
    *,
    default_horizon_min: int = 20,
) -> dict[str, Any] | None:
    if not event or event.get("error") or event.get("llm_error"):
        return None
    ek = str(event.get("event") or "").lower()
    if ek not in ("intent", "decision"):
        return None
    if event.get("outcome") or event.get("outcome_pending"):
        return None
    intended = (
        event.get("intended_side")
        or event.get("model_side")
        or event.get("horizon")
        or event.get("decision")
        or event.get("side")
        or "flat"
    )
    intended_s = str(intended).lower()
    if intended_s in ("higher",):
        intended_s = "buy"
    elif intended_s in ("lower",):
        intended_s = "sell"
    mid = event.get("mid")
    if mid is None and isinstance(event.get("quote"), dict):
        mid = event["quote"].get("mid")
    try:
        mid_f = float(mid) if mid is not None else None
    except (TypeError, ValueError):
        mid_f = None
    if mid_f is None or not math.isfinite(mid_f) or mid_f <= 0:
        return None
    try:
        hz = int(event.get("horizon_min") or default_horizon_min)
    except (TypeError, ValueError):
        hz = default_horizon_min
    hz = max(5, min(120, hz))
    try:
        base = datetime.fromisoformat(str(event.get("ts") or "").replace("Z", "+00:00"))
    except ValueError:
        base = datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    due = base + timedelta(minutes=hz)
    event["outcome_pending"] = True
    event["outcome_due_ts"] = due.isoformat()
    event["mid_at_decision"] = round(mid_f, 4)
    event["outcome_intended_side"] = intended_s
    event["horizon_min"] = hz
    return event


def outcome_butler_note(outcome: str | None, ticker: str | None = None) -> str:
    """Short Simple butler gloss for a stamped outcome."""
    t = (ticker or "").upper()
    label = (outcome or "").lower()
    who = f"{t} " if t else ""
    if label == "helped":
        return f"{who}call helped over the horizon — paper only"
    if label == "hurt":
        return f"{who}call hurt over the horizon — noted, no drama"
    if label == "flat":
        return f"{who}call was flat-ish — nothing loud"
    return ""
