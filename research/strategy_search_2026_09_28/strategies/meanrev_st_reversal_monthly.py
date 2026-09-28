"""meanrev_st_reversal_monthly -- monthly short-term reversal, long leg (Jegadeesh 1990 / French ST_Rev).

Pre-registered rules, implemented literally:
  * CALENDAR: month-ends are SPY index dates whose NEXT SPY row is in a different calendar
    month (the final SPY row is never a month-end). d_1 < d_2 < ...
  * FORMATION at d_m (m >= 2): eligible = every stock (not SPY/QQQ) with a row dated exactly
    d_{m-1} AND a row dated exactly d_m. r = Close(d_m)/Close(d_{m-1}) - 1.
    n = #eligible, k = floor(0.30 * n). Sort by (r asc, ticker asc), take first k.
  * ENTRY: Open of the stock's first row dated after d_m; no trade if none, or if that row is
    dated after d_{m+1}.
  * EXIT: Open of the stock's first row dated after d_{m+1}; if d_{m+1} does not exist or the
    stock has no row after it, Close of the stock's final row.
  * Each month's selections are separate trades (no merging). rank = r.
Only rows dated <= d_m are used to select; nothing after d_m is required for eligibility.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EXCLUDE = ("SPY", "QQQ")
LOSER_FRACTION_NUM, LOSER_FRACTION_DEN = 3, 10   # k = floor(0.30 * n), computed exactly


def month_ends(spy_index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    ym = np.asarray(spy_index.year * 12 + spy_index.month)
    is_me = np.zeros(len(spy_index), dtype=bool)
    is_me[:-1] = ym[1:] != ym[:-1]          # next SPY row in a different month; last row never
    return spy_index[is_me]


def strategy(data):
    me = month_ends(data["SPY"].index)
    stocks = sorted(s for s in data if s not in EXCLUDE)

    # Close on every month-end date for every stock (NaN where the stock has no row that day).
    # Reindexing onto exact dates never fills, so "row dated exactly d" <=> non-NaN here.
    me_close = pd.DataFrame({s: data[s]["Close"].reindex(me) for s in stocks})

    trades = []
    for m in range(1, len(me)):
        d_prev, d_m = me[m - 1], me[m]
        d_next = me[m + 1] if m + 1 < len(me) else None

        c0, c1 = me_close.loc[d_prev], me_close.loc[d_m]
        ok = c0.notna() & c1.notna()
        r = (c1[ok] / c0[ok] - 1.0)
        n = int(ok.sum())
        k = (LOSER_FRACTION_NUM * n) // LOSER_FRACTION_DEN
        if k == 0:
            continue
        ranked = sorted(zip(r.values.tolist(), r.index.tolist()))   # (r asc, ticker asc)
        for ret, s in ranked[:k]:
            df = data[s]
            idx = df.index
            pos_in = idx.searchsorted(d_m, side="right")             # first row dated after d_m
            if pos_in >= len(idx):
                continue
            entry_date = idx[pos_in]
            if d_next is not None and entry_date > d_next:
                continue
            if d_next is None:
                exit_date, exit_px = idx[-1], float(df["Close"].iloc[-1])
            else:
                pos_out = idx.searchsorted(d_next, side="right")     # first row dated after d_{m+1}
                if pos_out >= len(idx):
                    exit_date, exit_px = idx[-1], float(df["Close"].iloc[-1])
                else:
                    exit_date, exit_px = idx[pos_out], float(df["Open"].iloc[pos_out])
            trades.append({
                "ticker": s,
                "entry_date": entry_date,
                "entry_px": float(df["Open"].iloc[pos_in]),
                "exit_date": exit_date,
                "exit_px": exit_px,
                "rank": float(ret),
            })
    return trades
