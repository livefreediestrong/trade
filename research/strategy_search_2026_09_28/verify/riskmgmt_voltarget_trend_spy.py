"""Independent re-implementation (verifier) of riskmgmt_voltarget_trend_spy.

Spec summary:
  * t = last SPY row of calendar month m (only if SPY has a later row); F = next row.
  * SMA10(t) = mean of month-end closes of months m-9..m (all 10 must exist).
  * UP iff Close(t) > SMA10(t) (strict).
  * sigma = sqrt(252 * mean(r_d^2)) over the 126 daily returns ending at t (uncentered).
  * w = min(1, 0.12/sigma); k = floor(5w + 1e-9).
  * If UP and k>=1: k identical SPY trades, entry Open(F), exit Open(X) where X is the
    first SPY row of month m+2; if no such row, exit at last Close on the last row.
"""
import math

import numpy as np
import pandas as pd


def strategy(data):
    spy = data["SPY"]
    idx = spy.index
    opens = spy["Open"].to_numpy(dtype=float)
    closes = spy["Close"].to_numpy(dtype=float)
    n = len(idx)

    # month period of each row
    per = idx.to_period("M")
    per_arr = np.asarray(per)
    # last row index of each month (positional), in chronological order
    last_pos = {}
    first_pos = {}
    for i, p in enumerate(per_arr):
        last_pos[p] = i
        if p not in first_pos:
            first_pos[p] = i
    months = sorted(last_pos.keys())
    month_end_close = {p: closes[last_pos[p]] for p in months}

    # daily simple returns on consecutive rows
    r = np.full(n, np.nan)
    r[1:] = closes[1:] / closes[:-1] - 1.0

    trades = []
    for p in months:
        t = last_pos[p]
        if t + 1 >= n:
            continue  # no later row -> no decision
        # trend filter: 10 month-ends m-9..m, all must exist
        needed = [p - j for j in range(10)]
        if any(q not in month_end_close for q in needed):
            continue
        sma10 = float(np.mean([month_end_close[q] for q in needed]))
        up = closes[t] > sma10
        # volatility: 126 returns ending at t -> rows t-125..t, needs closes t-126..t
        if t - 126 < 0:
            continue
        rr = r[t - 125:t + 1]
        assert len(rr) == 126 and not np.isnan(rr).any()
        sigma = math.sqrt(252.0 * float(np.mean(rr * rr)))
        w = min(1.0, 0.12 / sigma) if sigma > 0 else 1.0
        k = int(math.floor(5.0 * w + 1e-9))
        if not up or k < 1:
            continue
        F = t + 1
        m2 = p + 2
        if m2 in first_pos:
            X = first_pos[m2]
            exit_date, exit_px = idx[X], float(opens[X])
        else:
            exit_date, exit_px = idx[n - 1], float(closes[n - 1])
        for _ in range(k):
            trades.append({
                "ticker": "SPY",
                "entry_date": idx[F],
                "entry_px": float(opens[F]),
                "exit_date": exit_date,
                "exit_px": exit_px,
                "rank": 0,
            })
    return trades
