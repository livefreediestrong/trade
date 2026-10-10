"""riskmgmt_voltarget_trend_spy

Volatility-targeted SPY (12% target, 126-day uncentered realized vol, weight capped at 1.0)
with a Faber (2007) 10-month SMA trend filter; monthly rebalance.

Decision on t = last SPY row of calendar month m (only if SPY has a later row).
  * SMA10(t) = mean of month-end closes ME_j, j = m-9..m (all 10 months must exist in SPY's index).
  * Trend UP iff Close(t) > SMA10(t) (strict).
  * sigma_hat(t) = sqrt(252 * mean(r_d^2)) over the 126 daily returns ending at t (rows t-125..t).
  * w = min(1, 0.12 / sigma_hat), k = floor(5*w + 1e-9).
  * If UP and k >= 1: k identical SPY trades, entry Open(F) (F = next SPY row = first day of m+1),
    exit Open(X) (X = first SPY row of month m+2); if no X, exit at the last SPY Close on that row.
Costs are applied by the harness.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TARGET_VOL = 0.12
VOL_WINDOW = 126
SMA_MONTHS = 10
SLOTS = 5


def strategy(data):
    spy = data["SPY"]
    idx = spy.index
    close = spy["Close"].to_numpy(dtype=float)
    opn = spy["Open"].to_numpy(dtype=float)
    n = len(idx)

    # Calendar-month period of every SPY row.
    per = idx.to_period("M")
    months = pd.PeriodIndex(sorted(set(per)), freq="M")

    # Last and first row position of each calendar month present in SPY's index.
    pos = np.arange(n)
    last_pos = pd.Series(pos, index=per).groupby(level=0).max()
    first_pos = pd.Series(pos, index=per).groupby(level=0).min()

    # Daily simple returns on consecutive rows; r[i] = close[i]/close[i-1]-1 (r[0] undefined).
    r = np.full(n, np.nan)
    r[1:] = close[1:] / close[:-1] - 1.0
    r2 = r * r

    trades = []
    for m in months:
        t = int(last_pos[m])
        # Decision only if SPY has a later row (so t is truly the month's last trading day).
        if t + 1 >= n:
            continue
        F = t + 1  # first trading day of month m+1

        # TREND FILTER: 10 month-end closes for months m-9..m, all must exist.
        needed = [m - j for j in range(SMA_MONTHS - 1, -1, -1)]
        if any(p not in last_pos.index for p in needed):
            continue
        me = [close[int(last_pos[p])] for p in needed]
        sma10 = float(np.mean(me))
        trend_up = close[t] > sma10  # strict; equality counts as not up

        # VOLATILITY SCALING: 126 returns ending at t need 127 closes (rows t-126..t).
        if t < VOL_WINDOW:
            continue
        window = r2[t - VOL_WINDOW + 1 : t + 1]
        sigma_hat = math.sqrt(252.0 * window.sum() / VOL_WINDOW)
        w = min(1.0, TARGET_VOL / sigma_hat) if sigma_hat > 0 else 1.0
        k = int(math.floor(SLOTS * w + 1e-9))

        if not trend_up or k < 1:
            continue

        # Exit: first SPY trading day of calendar month m+2 (next decision's fill day).
        m2 = m + 2
        if m2 in first_pos.index:
            X = int(first_pos[m2])
            exit_date, exit_px = idx[X], float(opn[X])
        else:
            exit_date, exit_px = idx[n - 1], float(close[n - 1])

        for _ in range(k):
            trades.append({
                "ticker": "SPY",
                "entry_date": idx[F],
                "entry_px": float(opn[F]),
                "exit_date": exit_date,
                "exit_px": exit_px,
                "rank": 0,
            })
    return trades
