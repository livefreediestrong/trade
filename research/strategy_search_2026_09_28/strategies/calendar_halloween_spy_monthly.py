"""calendar_halloween_spy_monthly (pre-registered).

Halloween / Sell-in-May on SPY: hold Nov-Apr, cash May-Oct, one trade per held month.
Source: Bouman & Jacobsen (2002, AER); Andrade, Chhaochharia & Fuerst (2013, FAJ).

Rules (implemented literally):
  * T = data['SPY'].index. ME(P) = last date of T inside calendar month P.
  * ME(P) is defined only if month P is complete: T contains a date in a later month.
  * Held months P: P.month in {11, 12, 1, 2, 3, 4}.
  * For each held P with ME(P) defined and month P-1 present in T (so ME(P-1) is defined,
    since P itself is a later month with rows):
        entry_date = ME(P-1), entry_px = SPY Close on ME(P-1)   (market-on-close)
        exit_date  = ME(P),   exit_px  = SPY Close on ME(P)     (market-on-close)
  * Entry/exit dates come from the trading calendar only; no price is used to decide anything.
    MOC is allowed by the harness because the decision uses no information from that close.
"""
from __future__ import annotations

import pandas as pd

HELD_MONTHS = {11, 12, 1, 2, 3, 4}


def strategy(data):
    spy = data["SPY"]
    idx = spy.index
    close = spy["Close"]
    periods = idx.to_period("M")

    # Last trading date in each calendar month that appears in T.
    last_in_month = pd.Series(idx, index=idx).groupby(periods).max()
    last_period = periods.max()
    # Complete-month rule: a month's last row is a month-end only if T has a later month.
    month_end = {p: d for p, d in last_in_month.items() if p < last_period}

    trades = []
    for p in sorted(month_end):
        if p.month not in HELD_MONTHS:
            continue
        prev = p - 1
        if prev not in month_end:  # month P-1 missing from T (or not complete): no trade
            continue
        entry_date, exit_date = month_end[prev], month_end[p]
        entry_px, exit_px = float(close.loc[entry_date]), float(close.loc[exit_date])
        if not (entry_px > 0 and exit_px > 0):
            continue
        trades.append({
            "ticker": "SPY",
            "entry_date": entry_date,
            "entry_px": entry_px,
            "exit_date": exit_date,
            "exit_px": exit_px,
            "rank": 0,
        })
    return trades
