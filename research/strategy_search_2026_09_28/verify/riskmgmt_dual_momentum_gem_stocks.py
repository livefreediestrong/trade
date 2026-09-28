"""Independent re-implementation of riskmgmt_dual_momentum_gem_stocks (verification).

GEM-style dual momentum on stocks:
  * decision day t = last SPY trading day of calendar month m (only if SPY has a later row)
  * fill day F = next SPY row (first trading day of month m+1)
  * t12 = last SPY trading day of calendar month m-12
  * SPY 12m return <= 0 -> cash (no trades)
  * eligible: not SPY/QQQ, row exactly on t, first row <= t12
  * R_i = Close_i(t) / Close_i as-of t12 - 1; sort desc, ties ticker asc
  * n = ceil(N/10) (exact integer arithmetic); drop R_i <= 0 (no replacement)
  * entry Open(F) if row on F; exit Open(X), X = first SPY day of month m+2,
    or first row after X; if no X in SPY or no stock row >= X, last Close.
"""
from __future__ import annotations

import pandas as pd

EXCLUDE = ("SPY", "QQQ")


def strategy(data):
    spy = data["SPY"]
    idx = spy.index
    spy_close = spy["Close"]

    # calendar months present in SPY's index, in order
    periods = idx.to_period("M")
    month_keys = pd.Index(periods.unique())  # sorted ascending (index ascending)
    # last and first SPY trading day for every calendar month
    s = pd.Series(idx, index=periods)
    last_day = s.groupby(level=0).max()
    first_day = s.groupby(level=0).min()

    # per-symbol helpers
    syms = [k for k in data if k not in EXCLUDE]
    first_row = {k: data[k].index[0] for k in syms}

    trades = []
    for m in month_keys:
        t = last_day[m]
        pos_t = idx.get_loc(t)
        if pos_t + 1 >= len(idx):
            continue  # no later SPY row -> not a valid decision day
        F = idx[pos_t + 1]
        m12 = m - 12
        if m12 not in last_day.index:
            continue
        t12 = last_day[m12]

        # STEP 1 absolute momentum on SPY
        r_spy = spy_close.loc[t] / spy_close.loc[t12] - 1.0
        if not (r_spy > 0):
            continue

        # STEP 2 relative momentum
        cands = []
        for k in syms:
            df = data[k]
            if first_row[k] > t12:
                continue
            if t not in df.index:
                continue
            c_t = df.at[t, "Close"]
            # as-of close on or before t12
            pos12 = df.index.searchsorted(t12, side="right") - 1
            if pos12 < 0:
                continue
            c_12 = df["Close"].iloc[pos12]
            r = c_t / c_12 - 1.0
            cands.append((k, float(r)))
        N = len(cands)
        if N == 0:
            continue
        cands.sort(key=lambda x: (-x[1], x[0]))
        n = -(-N // 10)  # ceil(N / 10)
        top = [(rank + 1, k, r) for rank, (k, r) in enumerate(cands[:n]) if r > 0]

        # exit day X = first SPY trading day of month m+2
        m2 = m + 2
        X = first_day[m2] if m2 in first_day.index else None

        for rank, k, r in top:
            df = data[k]
            if F not in df.index:
                continue
            entry_px = float(df.at[F, "Open"])
            if X is not None:
                pos_x = df.index.searchsorted(X, side="left")
                if pos_x < len(df.index):
                    exit_date = df.index[pos_x]
                    exit_px = float(df["Open"].iloc[pos_x])
                else:
                    exit_date = df.index[-1]
                    exit_px = float(df["Close"].iloc[-1])
            else:
                exit_date = df.index[-1]
                exit_px = float(df["Close"].iloc[-1])
            trades.append({
                "ticker": k,
                "entry_date": F,
                "entry_px": entry_px,
                "exit_date": exit_date,
                "exit_px": exit_px,
                "rank": rank,
            })
    return trades
