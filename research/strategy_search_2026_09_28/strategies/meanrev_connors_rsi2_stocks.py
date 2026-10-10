"""Connors & Alvarez (2008) 2-period RSI pullback in a 200-day uptrend, individual stocks.

Pre-registered spec: meanrev_connors_rsi2_stocks.
  Universe: every symbol except SPY and QQQ, each on its own rows (no reindex / ffill).
  Entry signal on row t (while flat): SMA200_t not NaN, Close_t > SMA200_t, RSI2_t < 5.
      Fill: Open of row t+1. A signal on the last row makes no trade.
  Exit signal: first row u >= t+1 with Close_u > SMA5_u. Fill: Open of row u+1.
      If u is the last row, or no exit signal occurs, exit at the Close of the last row.
  One position per ticker; the next entry signal may come on row u+1 or later.
  rank = RSI2_t (lower preferred).
All indicators end at row t (trailing windows / recursive smoothing only).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EXCLUDE = {"SPY", "QQQ"}
SMA_TREND = 200
SMA_EXIT = 5
RSI_ALPHA = 0.5          # Wilder smoothing for period 2 -> alpha = 1/2
RSI_ENTRY = 5.0


def _rsi2(close: pd.Series) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    ag = gain.ewm(alpha=RSI_ALPHA, adjust=False).mean()
    al = loss.ewm(alpha=RSI_ALPHA, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + ag / al)
    rsi = rsi.where(al != 0, 100.0)          # RSI2 = 100 when AL == 0
    rsi[d.isna()] = np.nan                   # row 0 has no diff -> undefined
    return rsi


def _ticker_trades(sym: str, df: pd.DataFrame) -> list[dict]:
    n = len(df)
    if n < SMA_TREND + 1:
        return []
    close = df["Close"].astype(float)
    opn = df["Open"].astype(float).to_numpy()
    c = close.to_numpy()
    idx = df.index

    sma200 = close.rolling(SMA_TREND).mean().to_numpy()
    sma5 = close.rolling(SMA_EXIT).mean().to_numpy()
    rsi = _rsi2(close).to_numpy()

    with np.errstate(invalid="ignore"):
        entry_sig = (~np.isnan(sma200)) & (c > sma200) & (rsi < RSI_ENTRY)
        exit_sig = (~np.isnan(sma5)) & (c > sma5)

    # next_exit[k] = smallest u >= k with exit_sig[u], else n (none)
    next_exit = np.full(n + 1, n, dtype=np.int64)
    for k in range(n - 1, -1, -1):
        next_exit[k] = k if exit_sig[k] else next_exit[k + 1]

    trades = []
    t = 0
    while t < n - 1:                       # a signal on the last row makes no trade
        if not entry_sig[t]:
            t += 1
            continue
        e = t + 1                           # entry row
        u = int(next_exit[e])               # first exit-signal row u >= t+1
        if u >= n - 1:                      # u is the last row, or no exit signal at all
            x_date, x_px = idx[n - 1], float(c[n - 1])
        else:
            x_date, x_px = idx[u + 1], float(opn[u + 1])
        trades.append({
            "ticker": sym,
            "entry_date": idx[e],
            "entry_px": float(opn[e]),
            "exit_date": x_date,
            "exit_px": x_px,
            "rank": float(rsi[t]),
        })
        if u >= n:                          # position held to the end of the data
            break
        t = u + 1                           # next entry signal can come on row u+1 or later
    return trades


def strategy(data):
    trades = []
    for sym in sorted(data):
        if sym in EXCLUDE:
            continue
        df = data[sym]
        if df is None or df.empty:
            continue
        trades.extend(_ticker_trades(sym, df))
    return trades
