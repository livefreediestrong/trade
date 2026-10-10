"""Cross-sectional same-calendar-month seasonality (Heston & Sadka 2008, lag 12), top decile.

Pre-registered spec: calendar_seasonality_heston_sadka_lag12.

CALENDAR: T = data["SPY"].index. ME(P) = last date of T in calendar month P, defined only
when T has a date in a later month (the final, incomplete month is never a month-end).
HOLDING MONTH P is traded when ME(P-13), ME(P-12), ME(P-1) and ME(P) are all defined.

On the decision / entry date ME(P-1):
  * eligible = stocks (not SPY/QQQ) with a row and a positive Close on EXACTLY
    ME(P-13), ME(P-12) and ME(P-1) (no data at or after ME(P) is consulted);
  * R_s = Close[ME(P-12)] / Close[ME(P-13)] - 1  (same calendar month one year earlier);
  * sort by R_s descending, ties by ticker ascending; k = ceil(N / 10), min 1;
  * buy the top k market-on-close at ME(P-1) (signal uses closes >= 11 months old),
    rank = 1..k.
Exit market-on-close at ME(P); fallbacks: first row after ME(P); else the stock's last
row; if no row after the entry date at all, a flat trade (exit = entry).
"""
from __future__ import annotations

import math

import pandas as pd

EXCLUDE = ("SPY", "QQQ")


def _month_ends(index: pd.DatetimeIndex) -> dict:
    periods = index.to_period("M")
    last = pd.Series(index, index=periods).groupby(level=0).max()
    final_period = periods.max()
    # A month is complete only if T has a date in a later month.
    return {p: pd.Timestamp(d) for p, d in last.items() if p < final_period}


def _close_on(close: pd.Series, date: pd.Timestamp):
    """Close on exactly `date` if the row exists and is positive, else None."""
    if date not in close.index:
        return None
    v = close.loc[date]
    if pd.isna(v) or not v > 0:
        return None
    return float(v)


def strategy(data):
    T = data["SPY"].index
    ME = _month_ends(T)
    stocks = sorted(s for s in data if s not in EXCLUDE)
    closes = {s: data[s]["Close"] for s in stocks}

    trades = []
    for P in sorted(ME):
        needed = (P - 13, P - 12, P - 1, P)
        if not all(q in ME for q in needed):
            continue
        d13, d12, d1, d0 = (ME[q] for q in needed)

        # --- eligibility and signal: only rows dated ME(P-13), ME(P-12), ME(P-1) ---
        ranked = []
        for s in stocks:
            c = closes[s]
            c13 = _close_on(c, d13)
            c12 = _close_on(c, d12)
            c1 = _close_on(c, d1)
            if c13 is None or c12 is None or c1 is None:
                continue
            ranked.append((-(c12 / c13 - 1.0), s, c1))
        n = len(ranked)
        if n == 0:
            continue
        ranked.sort(key=lambda x: (x[0], x[1]))  # R desc, ticker asc
        k = max(1, math.ceil(n / 10))

        # --- trades (exit lookup happens only after the selection is fixed) ---
        for rank, (_, s, entry_px) in enumerate(ranked[:k], start=1):
            c = closes[s]
            idx = c.index
            if d0 in idx and not pd.isna(c.loc[d0]) and c.loc[d0] > 0:
                exit_date, exit_px = d0, float(c.loc[d0])
            else:
                after = idx[idx > d0]
                if len(after):
                    exit_date = after[0]
                    exit_px = float(c.loc[exit_date])
                elif (idx > d1).any():
                    exit_date = idx[-1]
                    exit_px = float(c.iloc[-1])
                else:
                    exit_date, exit_px = d1, entry_px
            trades.append({
                "ticker": s,
                "entry_date": d1,
                "entry_px": entry_px,
                "exit_date": pd.Timestamp(exit_date),
                "exit_px": exit_px,
                "rank": rank,
            })
    return trades
