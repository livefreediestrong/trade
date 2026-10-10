"""Independent re-implementation: Heston-Sadka lag-12 same-calendar-month seasonality, top decile.

Written from the pre-registered spec only (verification run).
"""
from __future__ import annotations

import math

import pandas as pd


def _month_ends(T: pd.DatetimeIndex) -> dict:
    """Period('M') -> last date of T in that month, only for complete months
    (T contains a date in a later month)."""
    per = T.to_period("M")
    s = pd.Series(T, index=per)
    last = s.groupby(level=0).max()
    max_p = per.max()
    return {p: d for p, d in last.items() if p < max_p}


def strategy(data):
    T = data["SPY"].index
    ME = _month_ends(T)
    stocks = sorted(s for s in data if s not in ("SPY", "QQQ"))
    closes = {s: data[s]["Close"] for s in stocks}

    trades = []
    for P in sorted(ME):
        need = [P - 13, P - 12, P - 1, P]
        if not all(q in ME for q in need):
            continue
        d13, d12, d1, d0 = ME[P - 13], ME[P - 12], ME[P - 1], ME[P]
        elig = []
        for s in stocks:
            c = closes[s]
            try:
                c13, c12, c1 = c.at[d13], c.at[d12], c.at[d1]
            except KeyError:
                continue
            if not (pd.notna(c13) and pd.notna(c12) and pd.notna(c1)):
                continue
            if not (c13 > 0 and c12 > 0 and c1 > 0):
                continue
            R = float(c12) / float(c13) - 1.0
            elig.append((s, R, float(c1)))
        if not elig:
            continue
        elig.sort(key=lambda x: (-x[1], x[0]))
        k = max(1, math.ceil(len(elig) / 10))
        for rank, (s, R, c1) in enumerate(elig[:k], start=1):
            c = closes[s]
            idx = c.index
            if d0 in idx:
                xd, xp = d0, float(c.at[d0])
            else:
                after_exit = idx[idx > d0]
                if len(after_exit):
                    xd = after_exit[0]
                    xp = float(c.at[xd])
                else:
                    after_entry = idx[idx > d1]
                    if len(after_entry):
                        xd = idx[-1]
                        xp = float(c.iloc[-1])
                    else:
                        xd, xp = d1, c1
            trades.append({"ticker": s, "entry_date": d1, "entry_px": c1,
                           "exit_date": xd, "exit_px": xp, "rank": rank})
    return trades
