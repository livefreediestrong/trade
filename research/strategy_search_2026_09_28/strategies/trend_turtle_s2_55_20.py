"""trend_turtle_s2_55_20 -- Turtle System 2 long-only breakout (pre-registered spec).

Per ticker (all symbols except SPY and QQQ), on the stock's own rows i = 0..n-1:
  TR_i  = max(H_i - L_i, H_i - C_{i-1}, C_{i-1} - L_i)            (i >= 1)
  N_20  = mean(TR_1..TR_20);  N_i = (19*N_{i-1} + TR_i)/20         (i > 20)
  HH55_i = max(High rows i-55..i-1)   (excludes row i)
  LL20_i = min(Low  rows i-20..i-1)   (excludes row i)
Entry: at close of row t (t >= 63), flat, High_t > HH55_t -> buy Open_{t+1}
       (skip if Open_{t+1} <= LL20_{t+1}); N_e = N_t; stop2N = entry - 2*N_e;
       rank = -(Close_t - Close_{t-63}) / N_t.
Exit:  from the entry row inclusive, S_d = max(stop2N, LL20_d); first row with
       Low_d <= S_d exits at min(Open_d, S_d). Otherwise exit at last Close.
No pyramiding; after an exit on row d the ticker may signal at the close of row d.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ENTRY_LB = 55
EXIT_LB = 20
N_LEN = 20
STRENGTH_LB = 63
WARMUP = 63
EXCLUDE = ("SPY", "QQQ")


def _wilder_n(tr: np.ndarray) -> np.ndarray:
    """N_20 = mean(TR_1..TR_20); N_i = (19*N_{i-1} + TR_i)/20 for i > 20. NaN before row 20."""
    n = len(tr)
    out = np.full(n, np.nan)
    if n <= N_LEN:
        return out
    out[N_LEN] = tr[1:N_LEN + 1].mean()
    for i in range(N_LEN + 1, n):
        out[i] = ((N_LEN - 1) * out[i - 1] + tr[i]) / N_LEN
    return out


def _ticker_trades(sym: str, df: pd.DataFrame) -> list[dict]:
    df = df[["Open", "High", "Low", "Close"]]
    n = len(df)
    if n <= WARMUP + 1:
        return []
    idx = df.index
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    lo = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)

    prev_c = np.r_[np.nan, c[:-1]]
    tr = np.maximum.reduce([h - lo, h - prev_c, prev_c - lo])  # NaN at row 0 (unused)
    nn = _wilder_n(tr)
    hh55 = pd.Series(h).rolling(ENTRY_LB).max().shift(1).to_numpy()   # rows i-55..i-1
    ll20 = pd.Series(lo).rolling(EXIT_LB).min().shift(1).to_numpy()   # rows i-20..i-1

    trades = []
    pos = None  # dict with entry row, entry px, stop2N, rank
    for i in range(n):
        # 1) stop check on row i (active from the entry row inclusive)
        if pos is not None:
            s = max(pos["stop2N"], ll20[i])
            if lo[i] <= s:
                trades.append({
                    "ticker": sym,
                    "entry_date": idx[pos["e"]],
                    "entry_px": float(o[pos["e"]]),
                    "exit_date": idx[i],
                    "exit_px": float(min(o[i], s)),
                    "rank": pos["rank"],
                })
                pos = None
        # 2) signal at the close of row i (flat, including after an intraday exit today)
        if pos is None and i >= WARMUP and i + 1 < n and h[i] > hh55[i]:
            e = i + 1
            if o[e] <= ll20[e]:
                continue  # would be stopped at the open; stays flat, may signal at close of e
            n_e = nn[i]
            rank = -(c[i] - c[i - STRENGTH_LB]) / n_e if n_e > 0 else 0.0
            pos = {"e": e, "stop2N": o[e] - 2.0 * n_e, "rank": float(rank)}
    if pos is not None:  # data ended before the stop was hit: exit at last Close
        trades.append({
            "ticker": sym,
            "entry_date": idx[pos["e"]],
            "entry_px": float(o[pos["e"]]),
            "exit_date": idx[n - 1],
            "exit_px": float(c[n - 1]),
            "rank": pos["rank"],
        })
    return trades


def strategy(data) -> list[dict]:
    out = []
    for sym in sorted(data):
        if sym in EXCLUDE:
            continue
        out.extend(_ticker_trades(sym, data[sym]))
    return out
