"""riskmgmt_dual_momentum_gem_stocks

Dual momentum on stocks, GEM style (Antonacci 2014): SPY 12-month absolute-momentum
gate plus top-decile 12-month cross-sectional relative strength, monthly rebalance.

Timing: decide on the Close of t (last SPY trading day of month m), fill at the Open of
F (next SPY row = first trading day of month m+1), exit at the Open of X (first SPY
trading day of month m+2). Every monthly holding is a separate trade.
"""
from __future__ import annotations

import math

import pandas as pd

LOOKBACK_MONTHS = 12
DECILE = 0.10
EXCLUDE = ("SPY", "QQQ")


def _asof_close(df: pd.DataFrame, d: pd.Timestamp):
    """Close on the last row with date <= d (None if no such row)."""
    i = df.index.searchsorted(d, side="right") - 1
    if i < 0:
        return None
    return float(df["Close"].iloc[i])


def strategy(data):
    spy = data["SPY"]
    days = spy.index
    spy_close = spy["Close"]

    # Master calendar: per calendar month, first and last SPY trading day.
    periods = days.to_period("M")
    cal = pd.DataFrame({"d": days, "p": periods})
    last_day = cal.groupby("p")["d"].max()
    first_day = cal.groupby("p")["d"].min()
    last_idx = {p: days.get_loc(d) for p, d in last_day.items()}

    universe = {s: df for s, df in data.items() if s not in EXCLUDE}
    first_row = {s: df.index[0] for s, df in universe.items()}

    trades = []
    for m, t in last_day.items():
        i_t = last_idx[m]
        if i_t + 1 >= len(days):          # no later SPY row: final (possibly partial) month
            continue
        F = days[i_t + 1]                 # first trading day of month m+1
        m12 = m - LOOKBACK_MONTHS
        if m12 not in last_day.index:     # not enough history for the 12-month lookback
            continue
        t12 = last_day[m12]
        m2 = m + 2
        X = first_day[m2] if m2 in first_day.index else None   # next decision's fill day

        # STEP 1: absolute momentum on SPY.
        r_spy = float(spy_close.loc[t]) / float(spy_close.loc[t12]) - 1.0
        if r_spy <= 0:
            continue                      # cash until the next fill day

        # STEP 2: relative momentum among eligible stocks (data <= t only).
        elig = []
        for s, df in universe.items():
            if first_row[s] > t12:
                continue
            if t not in df.index:         # must have a row exactly on t
                continue
            c_t = float(df.at[t, "Close"])
            c_12 = _asof_close(df, t12)
            if c_12 is None or not (c_12 > 0):
                continue
            elig.append((s, c_t / c_12 - 1.0))
        if not elig:
            continue
        elig.sort(key=lambda x: (-x[1], x[0]))
        n = math.ceil(DECILE * len(elig))
        selected = [(rank, s, r) for rank, (s, r) in enumerate(elig[:n], start=1) if r > 0]

        # STEP 3/4: entries at Open(F), exits at Open(X) (or next available open / last close).
        for rank, s, r in selected:
            df = universe[s]
            if F not in df.index:
                continue                  # skipped, not replaced
            entry_px = float(df.at[F, "Open"])
            if X is None:
                exit_date = df.index[-1]
                exit_px = float(df["Close"].iloc[-1])
            else:
                j = df.index.searchsorted(X, side="left")
                if j < len(df):
                    exit_date = df.index[j]
                    exit_px = float(df["Open"].iloc[j])
                else:
                    exit_date = df.index[-1]
                    exit_px = float(df["Close"].iloc[-1])
            trades.append({
                "ticker": s,
                "entry_date": F,
                "entry_px": entry_px,
                "exit_date": exit_date,
                "exit_px": exit_px,
                "rank": rank,
            })
    return trades
