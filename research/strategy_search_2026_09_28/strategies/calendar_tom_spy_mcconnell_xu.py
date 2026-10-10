"""Turn-of-the-month window (-1 to +3) on SPY.

Pre-registered id: calendar_tom_spy_mcconnell_xu
Sources: Lakonishok & Smidt (1988); McConnell & Xu (2008); Ariel (1987).

Rules (implemented literally):
  * T = data['SPY'].index (ascending). A calendar month m is COMPLETE only if T has at
    least one date in a later calendar month (so the last month in the data never trades).
  * D_m = dates of T inside m, D_next = dates of T inside month m+1.
    Skip m if len(D_m) < 2 or len(D_next) < 3.
  * d_minus2 = D_m[-2] (second-to-last trading day of m); d_plus3 = D_next[2] (third
    trading day of m+1).
  * Trade: buy SPY market-on-close on d_minus2 (Close of d_minus2), sell market-on-close on
    d_plus3 (Close of d_plus3). Held close-to-close returns = trading days -1, +1, +2, +3.
  * Entry/exit dates are purely calendar-determined (pre-published exchange calendar);
    no price, volume or return information is used to decide anything. No filters, stops
    or sizing. One position at a time; windows never overlap. Costs are applied by the harness.
"""
from __future__ import annotations

import pandas as pd


def strategy(data):
    spy = data["SPY"]
    T = spy.index
    close = spy["Close"]

    months = T.to_period("M")
    # Group trading dates by calendar month (only rows present in SPY's index count).
    by_month: dict[pd.Period, list[pd.Timestamp]] = {}
    for d, m in zip(T, months):
        by_month.setdefault(m, []).append(d)

    last_month_in_data = months.max()
    trades = []
    for m in sorted(by_month):
        # COMPLETE month: T contains at least one date in a later calendar month.
        if not (last_month_in_data > m):
            continue
        D_m = by_month[m]
        D_next = by_month.get(m + 1, [])
        if len(D_m) < 2 or len(D_next) < 3:
            continue
        d_minus2 = D_m[-2]
        d_plus3 = D_next[2]
        entry_px = float(close.loc[d_minus2])
        exit_px = float(close.loc[d_plus3])
        trades.append({
            "ticker": "SPY",
            "entry_date": d_minus2,
            "entry_px": entry_px,
            "exit_date": d_plus3,
            "exit_px": exit_px,
            "rank": 0,
        })
    return trades
