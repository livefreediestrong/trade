"""Independent re-implementation of meanrev_st_reversal_monthly (verification copy).

Monthly short-term reversal, long leg: at each SPY-calendar month-end d_m (m >= 2),
rank stocks that have rows exactly on d_{m-1} and d_m by r = C(d_m)/C(d_{m-1}) - 1,
buy the bottom floor(0.30*n) at the stock's first Open after d_m (must be on/before
d_{m+1}), sell at the stock's first Open after d_{m+1} (else Close of its final row).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EXCLUDE = {"SPY", "QQQ"}


def _month_ends(idx: pd.DatetimeIndex) -> list[pd.Timestamp]:
    per = idx.to_period("M")
    # d is a month-end iff the NEXT SPY row is in a different calendar month.
    # The final row has no next row, so it is never a month-end.
    out = [idx[i] for i in range(len(idx) - 1) if per[i + 1] != per[i]]
    return out


def strategy(data):
    spy_idx = data["SPY"].index
    me = _month_ends(spy_idx)
    stocks = sorted(s for s in data if s not in EXCLUDE)

    # Pre-extract numpy arrays per stock
    arrs = {}
    for s in stocks:
        df = data[s]
        arrs[s] = (df.index.values, df["Open"].to_numpy(float), df["Close"].to_numpy(float), df.index)

    def close_on(s, d):
        idx_v, _, c, _ = arrs[s]
        dv = np.datetime64(d)
        j = np.searchsorted(idx_v, dv)
        if j < len(idx_v) and idx_v[j] == dv:
            return c[j]
        return None

    trades = []
    for m in range(1, len(me)):
        d_prev, d_cur = me[m - 1], me[m]
        d_next = me[m + 1] if m + 1 < len(me) else None
        elig = []
        for s in stocks:
            c0 = close_on(s, d_prev)
            if c0 is None:
                continue
            c1 = close_on(s, d_cur)
            if c1 is None:
                continue
            elig.append((c1 / c0 - 1.0, s))
        n = len(elig)
        k = (n * 3) // 10  # exact floor(0.30 * n)
        if k == 0:
            continue
        elig.sort(key=lambda x: (x[0], x[1]))
        for r, s in elig[:k]:
            idx_v, o, c, idx = arrs[s]
            je = np.searchsorted(idx_v, np.datetime64(d_cur), side="right")  # first row > d_cur
            if je >= len(idx_v):
                continue
            entry_date = idx[je]
            if d_next is not None and entry_date > d_next:
                continue
            entry_px = float(o[je])
            if d_next is not None:
                jx = np.searchsorted(idx_v, np.datetime64(d_next), side="right")
                if jx < len(idx_v):
                    exit_date, exit_px = idx[jx], float(o[jx])
                else:
                    exit_date, exit_px = idx[-1], float(c[-1])
            else:
                exit_date, exit_px = idx[-1], float(c[-1])
            trades.append({"ticker": s, "entry_date": entry_date, "entry_px": entry_px,
                           "exit_date": exit_date, "exit_px": exit_px, "rank": float(r)})
    return trades
