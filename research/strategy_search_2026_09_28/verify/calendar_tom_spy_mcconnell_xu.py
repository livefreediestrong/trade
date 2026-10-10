"""Independent re-implementation: turn-of-the-month window (-1..+3) on SPY.

Entry: MOC on trading day -2 (second-to-last trading day of month m).
Exit:  MOC on trading day +3 (third trading day of month m+1).
Held close-to-close returns: days -1, +1, +2, +3.
Only the trading calendar (SPY's index) is used to choose dates; prices are
read only to fill the trade at the chosen dates.
"""
import pandas as pd


def strategy(data):
    spy = data["SPY"]
    idx = pd.DatetimeIndex(spy.index).sort_values()
    close = spy["Close"]
    months = idx.to_period("M")
    # group trading dates by calendar month (ascending)
    by_month = {}
    for d, m in zip(idx, months):
        by_month.setdefault(m, []).append(d)
    last_month = months.max()
    trades = []
    for m in sorted(by_month):
        # complete month: T contains a date in a later calendar month
        if not (m < last_month):
            continue
        d_m = by_month[m]
        d_next = by_month.get(m + 1, [])
        if len(d_m) < 2 or len(d_next) < 3:
            continue
        entry = d_m[-2]
        exit_ = d_next[2]
        trades.append({
            "ticker": "SPY",
            "entry_date": entry,
            "entry_px": float(close.loc[entry]),
            "exit_date": exit_,
            "exit_px": float(close.loc[exit_]),
            "rank": 0,
        })
    return trades
