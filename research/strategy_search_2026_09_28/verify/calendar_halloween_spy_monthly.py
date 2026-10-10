"""Independent re-implementation: Halloween / Sell-in-May on SPY, one trade per held month.

Held months P in {Nov..Apr}. Trade: entry at SPY Close on ME(P-1), exit at SPY Close on ME(P),
where ME(P) = last row of SPY's index in month P, defined only if a later month exists in the index.
Market-on-close at calendar-fixed dates; no price-based decisions.
"""
import pandas as pd

HELD = {11, 12, 1, 2, 3, 4}


def strategy(data):
    spy = data["SPY"]
    idx = spy.index
    close = spy["Close"]
    months = idx.to_period("M")
    last_month = months.max()
    # Month-end = last trading date of each month present in the index; exclude the final
    # (possibly incomplete) month, since no later row exists to confirm its end.
    me = {}
    for p, d in zip(months, idx):
        me[p] = d  # ascending index, so the last assignment wins
    me = {p: d for p, d in me.items() if p < last_month}

    trades = []
    for p in sorted(me):
        if p.month not in HELD:
            continue
        prev = p - 1
        if prev not in me:  # month P-1 missing entirely (or not complete) -> no trade
            continue
        e, x = me[prev], me[p]
        epx, xpx = float(close.loc[e]), float(close.loc[x])
        if not (epx > 0 and xpx > 0):
            continue
        trades.append({"ticker": "SPY", "entry_date": e, "entry_px": epx,
                       "exit_date": x, "exit_px": xpx, "rank": 0})
    return trades
