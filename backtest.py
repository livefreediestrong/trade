"""Test the screener's buy rule on past data — does it have an edge?

Replays, day by day, the PASS rule the desk uses live (first-hour volume spike +
price above its 10/20/50-day averages) on ~2 years of hourly bars:

  * decide at 10:30 ET using ONLY data available by then (no look-ahead);
  * enter at the 10:30 price (+ slip), exit at the preset stop, the preset target,
    or the day's close (whichever comes first, checked bar by bar; if a bar touches
    both, assume the stop — the pessimistic choice);
  * subtract slip + fees;
  * split days into a LEARNING period (first 60%) and a CHECK period (last 40%)
    that the rule was never tuned on, and compare against a no-filter baseline
    (buy every stock every day at 10:30) so "edge" means better than random.

Not included (stated in the report): the sector-money-flow check and the AI brain.
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    "stop_pct": 0.8,        # 0.8% × stop_r (mid preset stop_r = 1)
    "target_r": 2.5,        # target = stop × target_r (mid preset)
    "slip_bps": 5.0,
    "fee_bps": 1.0,
    "position_usd": 200.0,  # 2% of a $10,000 practice account (mid preset)
    "vol_threshold_aligned": 1.5,
    "vol_threshold_other": 2.0,
    "learn_frac": 0.6,
}


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    idx = df.index
    try:
        idx = idx.tz_convert("America/New_York")
    except Exception:
        try:
            idx = idx.tz_localize("UTC").tz_convert("America/New_York")
        except Exception:
            pass
    df.index = idx
    df["day"] = [ts.strftime("%Y-%m-%d") for ts in df.index]
    df["hhmm"] = [ts.strftime("%H:%M") for ts in df.index]
    return df


def simulate_ticker(df60: pd.DataFrame, params: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-day trades for one ticker: rule trades + baseline trades."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    df = _prep(df60)
    days = sorted(df["day"].unique())
    closes: list[float] = []      # completed daily closes (known before today)
    first_hours: list[float] = []  # completed first-hour volumes
    out: list[dict[str, Any]] = []
    stop_pct = p["stop_pct"] / 100.0
    tgt_pct = stop_pct * p["target_r"]
    cost_frac = (2 * p["slip_bps"] + 2 * p["fee_bps"]) / 10_000.0
    for d in days:
        bars = df[df["day"] == d].sort_index()
        bars = bars[(bars["hhmm"] >= "09:30") & (bars["hhmm"] < "16:00")]
        if len(bars) < 3:
            continue
        fh = bars.iloc[0]  # 09:30-10:30 bar
        entry_px = float(fh["Close"])  # known at 10:30
        fh_vol = float(fh["Volume"])
        signal = None
        if len(closes) >= 50 and len(first_hours) >= 5 and entry_px > 0:
            s10 = float(np.mean(closes[-10:]))
            s20 = float(np.mean(closes[-20:]))
            s50 = float(np.mean(closes[-50:]))
            aligned = entry_px > s10 and entry_px > s20 and entry_px > s50
            prev = float(np.mean(first_hours[-5:]))
            rel = fh_vol / prev if prev > 0 else 0.0
            thr = p["vol_threshold_aligned"] if aligned else p["vol_threshold_other"]
            signal = bool(aligned and rel >= thr)
            # Walk the rest of the day for the exit
            stop = entry_px * (1 - stop_pct)
            target = entry_px * (1 + tgt_pct)
            exit_px, why = float(bars.iloc[-1]["Close"]), "close"
            for _, b in bars.iloc[1:].iterrows():
                lo, hi = float(b["Low"]), float(b["High"])
                if lo <= stop:
                    exit_px, why = stop, "stop"
                    break
                if hi >= target:
                    exit_px, why = target, "target"
                    break
            ret = (exit_px / entry_px - 1) - cost_frac
            out.append({
                "day": d, "rule": signal, "ret": ret, "exit": why,
                "pnl_usd": round(ret * p["position_usd"], 4),
            })
        closes.append(float(bars.iloc[-1]["Close"]))
        first_hours.append(fh_vol)
    return out


def _stats(trades: list[dict[str, Any]], position_usd: float) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"trades": 0}
    rets = np.array([t["ret"] for t in trades], dtype=float)
    pnl = rets * position_usd
    wins = int((rets > 0).sum())
    gross_win = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    eq = np.cumsum(pnl)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq])) - np.concatenate([[0.0], eq])).max())
    return {
        "trades": n,
        "win_rate": round(wins / n, 3),
        "avg_per_trade_usd": round(float(pnl.mean()), 3),
        "total_usd": round(float(pnl.sum()), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_drawdown_usd": round(dd, 2),
        "avg_return_pct": round(float(rets.mean()) * 100, 3),
    }


def evaluate(all_trades: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    days = sorted({t["day"] for t in all_trades})
    if not days:
        return {"ok": False, "error": "No usable history (need 50+ days per stock)."}
    cut = days[int(len(days) * p["learn_frac"])] if len(days) > 5 else days[-1]
    learn = [t for t in all_trades if t["day"] < cut]
    check = [t for t in all_trades if t["day"] >= cut]
    pu = p["position_usd"]
    res = {
        "ok": True,
        "period": {"start": days[0], "split": cut, "end": days[-1]},
        "learn": {"rule": _stats([t for t in learn if t["rule"]], pu), "baseline": _stats(learn, pu)},
        "check": {"rule": _stats([t for t in check if t["rule"]], pu), "baseline": _stats(check, pu)},
        "params": p,
        "not_included": ["sector money-flow check", "the AI brain's own judgment", "news / earnings"],
    }
    res["verdict"], res["explanation"], res["warnings"] = _verdict(res)
    return res


def _edge(block: dict[str, Any]) -> Optional[float]:
    r, b = block["rule"], block["baseline"]
    if not r.get("trades") or not b.get("trades"):
        return None
    return r["avg_per_trade_usd"] - b["avg_per_trade_usd"]


def _verdict(res: dict[str, Any]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    lr, cr = res["learn"]["rule"], res["check"]["rule"]
    for name, s in (("learning", lr), ("check", cr)):
        if s.get("trades", 0) >= 30 and (s.get("win_rate", 0) > 0.8 or (s.get("profit_factor") or 0) > 4):
            warnings.append(f"Results in the {name} period look too good to be true — treat with suspicion.")
    if cr.get("trades", 0) < 20:
        return ("not_enough", f"Only {cr.get('trades', 0)} rule trades in the check period — too few to judge. "
                "Add more stocks to the test.", warnings)
    e_learn, e_check = _edge(res["learn"]), _edge(res["check"])
    pos_check = (cr.get("avg_per_trade_usd") or 0) > 0
    if pos_check and (e_check or 0) > 0 and (e_learn or 0) > 0:
        return ("edge", "The rule made money after costs in the period it had never seen AND beat buying "
                "without the filter in both periods. That's encouraging — keep paper trading to confirm.", warnings)
    if (e_learn or 0) > 0 and not ((e_check or 0) > 0):
        return ("overfit", "The rule looked good in the learning period but not in the check period — "
                "likely luck, not a real edge.", warnings)
    if (e_check or 0) > 0 and not pos_check:
        return ("weak", "The filter picks better trades than random, but after costs they still lose money. "
                "Costs or exits need work before this can pay.", warnings)
    return ("no_edge", "The rule didn't beat buying at random after costs. The screener alone has no proven "
            "edge on this history — rely on paper results before trusting it.", warnings)


# ---------------------------------------------------------------------------
# Runner (background job with progress) — fetches data with yfinance
# ---------------------------------------------------------------------------
_state: dict[str, Any] = {"running": False, "progress": 0, "total": 0, "message": ""}
_lock = threading.Lock()


def status() -> dict[str, Any]:
    with _lock:
        return dict(_state)


def _fetch(ticker: str) -> Optional[pd.DataFrame]:
    import yfinance as yf

    try:
        df = yf.Ticker(ticker).history(period="730d", interval="60m", auto_adjust=False)
        return df if df is not None and len(df) else None
    except Exception:
        return None


def run(tickers: list[str], out_path: Path, params: Optional[dict[str, Any]] = None,
        fetch: Callable[[str], Optional[pd.DataFrame]] = _fetch) -> dict[str, Any]:
    with _lock:
        if _state.get("running"):
            return {"ok": False, "error": "A test is already running."}
        _state.update(running=True, progress=0, total=len(tickers), message="Downloading prices…", started=time.time())
    all_trades: list[dict[str, Any]] = []
    used, skipped = [], []
    try:
        for i, t in enumerate(tickers):
            with _lock:
                _state.update(progress=i, message=f"Testing {t} ({i + 1} of {len(tickers)})…")
            df = fetch(t)
            if df is None or len(df) < 400:
                skipped.append(t)
                continue
            trades = simulate_ticker(df, params or {})
            for tr in trades:
                tr["ticker"] = t
            all_trades.extend(trades)
            used.append(t)
        res = evaluate(all_trades, params or {})
        res.update(tickers=used, skipped=skipped, finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        try:
            out_path.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        except OSError:
            pass
        return res
    finally:
        with _lock:
            _state.update(running=False, progress=len(tickers), message="Done")


def last_result(out_path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(out_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
