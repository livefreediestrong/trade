"""Shared, fixed test harness for the pre-registered strategy search (2026-09-28).

Every strategy is a function `strategy(data) -> list[Trade]` over DAILY bars, where
data = load() is {symbol: DataFrame[Open, High, Low, Close, Volume]} (split/dividend
adjusted, ~10 years, tz-naive DatetimeIndex, ascending). SPY and QQQ are included.

TIMING RULES (no look-ahead):
  * A decision made "on day t" may use only rows with index <= t (use .iloc[:i+1]
    or precomputed indicators that are shifted/rolling ending at t).
  * The earliest fill for a decision on day t is the OPEN of day t+1 ("next open"),
    or the CLOSE of day t ONLY if the rule is explicitly market-on-close and uses
    information available before the close (not the close itself). Default: next open.
  * Exits follow the same rule. Stops may fill at the stop price intraday on a later
    day if Low <= stop (use min(Open, stop) if the day gaps below the stop).

A Trade is a dict: {"ticker", "entry_date", "entry_px", "exit_date", "exit_px"} plus an optional
numeric "rank" (lower = preferred when more trades start on one day than there are free slots)
(dates are pandas Timestamps from the index; prices from that day's row).

Costs are applied here, never inside strategies. Pass criteria are fixed below and
apply to the FIDELITY scenario ($0 commission, 5 bps slippage per side).
"""
from __future__ import annotations

import math
import pickle
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DATA_PATH = HERE / "daily.pkl"
HOLDOUT_START = pd.Timestamp("2023-01-01")   # last ~3.75 years
SCENARIOS = {
    # per-side costs in bps
    "fidelity_0_commission": 5.0,            # slippage only; $0 commission, fractional shares
    "ibkr_110_account": 5.0 + 100.0,         # IBKR Pro Fixed on orders under $100 = 1% a side
}
PASS_RULES = (
    "Under fidelity_0_commission: (1) per-trade mean > 0 with a month-clustered one-sided "
    "lower bound > 0 at alpha = 0.05 / M (Bonferroni over M candidates); (2) mean per trade > 0 "
    "in the HOLDOUT (entries on/after 2023-01-01); (3) positive mean in >= 60% of calendar years "
    "with trades; (4) the 5-slot portfolio's Sharpe beats BOTH SPY buy-and-hold and the "
    "equal-weight buy-and-hold of the same universe over the same period."
)


def load() -> dict[str, pd.DataFrame]:
    return pickle.load(open(DATA_PATH, "rb"))


def _net(trades, per_side_bps):
    out = []
    for t in trades:
        gross = t["exit_px"] / t["entry_px"] - 1.0
        out.append(gross - 2 * per_side_bps / 1e4)
    return np.array(out)


def _clustered_lower(values, keys, alpha):
    groups = {}
    for v, k in zip(values, keys):
        groups.setdefault(k, []).append(v)
    means = np.array([np.mean(g) for g in groups.values()])
    if len(means) < 3:
        return None, len(means)
    z = NormalDist().inv_cdf(1 - alpha)
    return float(means.mean() - z * means.std(ddof=1) / math.sqrt(len(means))), len(means)


def portfolio(trades, data, per_side_bps, slots=5):
    """Equal slots: each trade gets 1/slots of equity at entry if a slot is free
    (earliest entry first, ties by ticker). Daily mark-to-market on closes."""
    if not trades:
        return None
    # Same-day competition for slots: lower "rank" first (strategies may set it), then ticker.
    trades = sorted(trades, key=lambda t: (t["entry_date"], t.get("rank", 0), t["ticker"]))
    days = data["SPY"].index
    start, end = trades[0]["entry_date"], days[-1]
    days = days[(days >= start) & (days <= end)]
    closes = {s: df["Close"] for s, df in data.items()}
    equity, cash, open_pos, curve, taken = 1.0, 1.0, [], [], 0
    cost = per_side_bps / 1e4
    i_trade = 0
    for d in days:
        # exits first (at their exit price on exit day)
        still = []
        for p in open_pos:
            if p["exit_date"] <= d:
                cash += p["units"] * p["exit_px"] * (1 - cost)
            else:
                still.append(p)
        open_pos = still
        # mark equity before entries
        mtm = cash + sum(p["units"] * float(closes[p["ticker"]].get(d, p["last"])) for p in open_pos)
        while i_trade < len(trades) and trades[i_trade]["entry_date"] <= d:
            t = trades[i_trade]
            i_trade += 1
            if t["entry_date"] < d or len(open_pos) >= slots:
                continue
            # Target an equal share, but use what cash is free: a slot freed by a losing trade
            # still takes the next trade (fix found by the independent verifier, 2026-09-28).
            alloc = min(mtm / slots, cash)
            if alloc <= 1e-9:
                continue
            units = alloc * (1 - cost) / t["entry_px"]
            cash -= alloc
            taken += 1
            if t["exit_date"] == d:  # same-day round trip
                cash += units * t["exit_px"] * (1 - cost)
                continue
            open_pos.append({**t, "units": units, "last": t["entry_px"]})
        for p in open_pos:
            px = closes[p["ticker"]].get(d)
            if px is not None and not np.isnan(px):
                p["last"] = float(px)
        equity = cash + sum(p["units"] * p["last"] for p in open_pos)
        curve.append(equity)
    curve = pd.Series(curve, index=days)
    return _curve_stats(curve) | {"trades_taken": taken, "slots": slots}


def _curve_stats(curve):
    rets = curve.pct_change().dropna()
    years = max((curve.index[-1] - curve.index[0]).days / 365.25, 1e-9)
    cagr = curve.iloc[-1] ** (1 / years) - 1 if curve.iloc[-1] > 0 else -1.0
    sharpe = float(rets.mean() / rets.std() * math.sqrt(252)) if rets.std() > 0 else 0.0
    dd = float((curve / curve.cummax() - 1).min())
    return {"start": str(curve.index[0].date()), "end": str(curve.index[-1].date()),
            "cagr": round(float(cagr), 4), "sharpe": round(sharpe, 3), "max_drawdown": round(dd, 4)}


def benchmarks(data, start):
    spy = data["SPY"]["Close"]
    spy = spy[spy.index >= start]
    out = {"spy_buy_hold": _curve_stats(spy / spy.iloc[0])}
    closes = pd.DataFrame({s: df["Close"] for s, df in data.items() if s not in ("SPY", "QQQ")})
    closes = closes[closes.index >= start]
    closes = closes.loc[:, closes.iloc[0].notna()]
    ew = (closes / closes.iloc[0]).ffill().mean(axis=1)  # equal-weight buy-and-hold, no rebalancing
    out["equal_weight_buy_hold"] = _curve_stats(ew)
    return out


def evaluate(name, trades, data, m_tests=10):
    """Returns the full report dict; `passed` applies PASS_RULES to the Fidelity scenario."""
    bad = [t for t in trades if not (t["entry_px"] > 0 and t["exit_px"] > 0 and t["exit_date"] >= t["entry_date"])]
    if bad:
        raise ValueError(f"{len(bad)} malformed trades, e.g. {bad[0]}")
    report = {"name": name, "trades": len(trades), "m_tests": m_tests, "pass_rules": PASS_RULES}
    if not trades:
        report.update(passed=False, reasons=["no trades"])
        return report
    keys = [t["entry_date"].strftime("%Y-%m") for t in trades]
    hold_days = [(t["exit_date"] - t["entry_date"]).days for t in trades]
    report["median_hold_calendar_days"] = float(np.median(hold_days))
    report["trades_per_year"] = round(len(trades) / max((max(t["entry_date"] for t in trades) - min(t["entry_date"] for t in trades)).days / 365.25, 1e-9), 1)
    report["gross_mean_bps"] = round(float(_net(trades, 0).mean() * 1e4), 1)
    for scen, bps in SCENARIOS.items():
        net = _net(trades, bps)
        lower, months = _clustered_lower(net, keys, 0.05 / m_tests)
        years = {}
        for v, t in zip(net, trades):
            years.setdefault(t["entry_date"].year, []).append(v)
        hold = np.array([v for v, t in zip(net, trades) if t["entry_date"] >= HOLDOUT_START])
        report[scen] = {
            "mean_bps": round(float(net.mean() * 1e4), 1),
            "win_rate": round(float((net > 0).mean()), 3),
            "lower_bound_bps": None if lower is None else round(lower * 1e4, 1),
            "months": months,
            "years_positive": f"{sum(np.mean(v) > 0 for v in years.values())}/{len(years)}",
            "per_year_mean_bps": {y: round(float(np.mean(v) * 1e4), 1) for y, v in sorted(years.items())},
            "holdout_trades": int(len(hold)),
            "holdout_mean_bps": round(float(hold.mean() * 1e4), 1) if len(hold) else None,
            "portfolio_5_slots": portfolio(trades, data, bps),
        }
    start = min(t["entry_date"] for t in trades)
    report["benchmarks"] = benchmarks(data, start)
    f = report["fidelity_0_commission"]
    pos, n_years = map(int, f["years_positive"].split("/"))
    port, bench = f["portfolio_5_slots"], report["benchmarks"]
    reasons = []
    if f["lower_bound_bps"] is None or f["lower_bound_bps"] <= 0:
        reasons.append(f"per-trade lower bound {f['lower_bound_bps']} bps is not above 0 (Bonferroni M={m_tests})")
    if not f["holdout_mean_bps"] or f["holdout_mean_bps"] <= 0:
        reasons.append(f"holdout mean {f['holdout_mean_bps']} bps is not positive")
    if n_years == 0 or pos / n_years < 0.6:
        reasons.append(f"positive in only {f['years_positive']} years")
    if not port or port["sharpe"] <= bench["spy_buy_hold"]["sharpe"] or port["sharpe"] <= bench["equal_weight_buy_hold"]["sharpe"]:
        reasons.append(f"portfolio Sharpe {port and port['sharpe']} does not beat SPY {bench['spy_buy_hold']['sharpe']} "
                       f"and equal-weight {bench['equal_weight_buy_hold']['sharpe']}")
    report["passed"] = not reasons
    report["reasons"] = reasons
    return report
