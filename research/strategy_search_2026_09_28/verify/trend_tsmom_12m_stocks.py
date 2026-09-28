"""Independent re-implementation of trend_tsmom_12m_stocks (Moskowitz-Ooi-Pedersen TSMOM k=12, h=1).

Written from the pre-registered spec only (without reading strategies/trend_tsmom_12m_stocks.py).

Per stock (all symbols except SPY/QQQ), on its own rows:
  * month-end row i: i < n-1 and row i+1 lies in a later calendar month (last row is never a month-end)
  * P(m) = Close at the month-end row of calendar month m
  * at month-end row t (month m): r12 = P(m) / P(m-12) - 1, requires a month-end row in month m-12
  * sigma_t = sqrt(261) * Close.pct_change().ewm(com=60, min_periods=60).std() at row t (causal)
  * if r12 > 0 and sigma_t is finite: long from Open[t+1] to Open[row after next month-end row],
    or to Close[last row] if there is no further month-end row; rank = sigma_t.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EXCLUDE = ("SPY", "QQQ")


def _trades_for(ticker: str, df: pd.DataFrame) -> list[dict]:
    df = df.sort_index()
    n = len(df)
    if n < 3:
        return []
    idx = df.index
    close = df["Close"].to_numpy(dtype=float)
    opn = df["Open"].to_numpy(dtype=float)

    # causal EWMA volatility ending at each row (row t only uses returns through t)
    sig = (np.sqrt(261.0) * df["Close"].pct_change().ewm(com=60, min_periods=60).std()).to_numpy()

    # month-end rows determined by the NEXT row's date only (never its prices)
    per = idx.to_period("M")
    per_ord = np.array([p.ordinal for p in per])  # monotonic month counter
    is_me = np.zeros(n, dtype=bool)
    is_me[:-1] = per_ord[1:] > per_ord[:-1]
    me_rows = np.flatnonzero(is_me)
    if len(me_rows) == 0:
        return []

    month_to_row = {int(per_ord[r]): int(r) for r in me_rows}
    out = []
    for k, t in enumerate(me_rows):
        m = int(per_ord[t])
        r_prev = month_to_row.get(m - 12)
        if r_prev is None:
            continue
        r12 = close[t] / close[r_prev] - 1.0
        s = sig[t]
        if not np.isfinite(s) or not (r12 > 0):
            continue
        entry_row = t + 1  # exists because t is a month-end (t < n-1)
        if k + 1 < len(me_rows):
            nxt = me_rows[k + 1]
            exit_row = nxt + 1
            exit_px = opn[exit_row]
        else:
            exit_row = n - 1
            exit_px = close[exit_row]
        out.append({
            "ticker": ticker,
            "entry_date": idx[entry_row],
            "entry_px": float(opn[entry_row]),
            "exit_date": idx[exit_row],
            "exit_px": float(exit_px),
            "rank": float(s),
        })
    return out


def strategy(data):
    trades = []
    for sym in sorted(data):
        if sym in EXCLUDE:
            continue
        trades.extend(_trades_for(sym, data[sym]))
    return trades
