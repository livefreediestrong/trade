"""Independent re-implementation of trend_turtle_s2_55_20 (Turtle System 2, long-only).

Written from the pre-registered spec only. Per-ticker, on each stock's own rows.
"""
import numpy as np
import pandas as pd

ENTRY_LB = 55
EXIT_LB = 20
N_LEN = 20
STRENGTH_LB = 63
WARMUP = 63


def _indicators(df):
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    n = len(df)
    # True range for i >= 1
    tr = np.full(n, np.nan)
    if n > 1:
        pc = c[:-1]
        tr[1:] = np.maximum.reduce([h[1:] - l[1:], h[1:] - pc, pc - l[1:]])
    # Wilder-style N: seed at row 20 with mean(TR_1..TR_20), then recursive
    N = np.full(n, np.nan)
    if n > N_LEN:
        N[N_LEN] = tr[1:N_LEN + 1].mean()
        for i in range(N_LEN + 1, n):
            N[i] = (19.0 * N[i - 1] + tr[i]) / 20.0
    # channels excluding the current row
    hh55 = pd.Series(h).rolling(ENTRY_LB, min_periods=ENTRY_LB).max().shift(1).to_numpy()
    ll20 = pd.Series(l).rolling(EXIT_LB, min_periods=EXIT_LB).min().shift(1).to_numpy()
    return o, h, l, c, N, hh55, ll20


def _ticker_trades(sym, df):
    o, h, l, c, N, hh55, ll20 = _indicators(df)
    idx = df.index
    n = len(df)
    out = []
    long = False
    entry_i = None
    stop2n = None
    cur = None
    d = 0
    while d < n:
        # 1) manage an open position on row d (stop active from entry row inclusive)
        if long:
            S = stop2n
            if not np.isnan(ll20[d]):
                S = max(stop2n, ll20[d])
            if l[d] <= S:
                cur["exit_date"] = idx[d]
                cur["exit_px"] = float(min(o[d], S))
                out.append(cur)
                long = False
                cur = None
        # 2) signal at close of row d if flat
        if (not long) and d >= WARMUP and d + 1 < n:
            if not np.isnan(hh55[d]) and h[d] > hh55[d] and not np.isnan(N[d]) and N[d] > 0:
                e = d + 1
                if not (o[e] <= ll20[e]):
                    Ne = N[d]
                    stop2n = o[e] - 2.0 * Ne
                    cur = {
                        "ticker": sym,
                        "entry_date": idx[e],
                        "entry_px": float(o[e]),
                        "rank": float(-(c[d] - c[d - STRENGTH_LB]) / Ne),
                    }
                    long = True
                    entry_i = e
        d += 1
    if long:
        cur["exit_date"] = idx[n - 1]
        cur["exit_px"] = float(c[n - 1])
        out.append(cur)
    return out


def strategy(data):
    trades = []
    for sym in sorted(data):
        if sym in ("SPY", "QQQ"):
            continue
        df = data[sym][["Open", "High", "Low", "Close"]].dropna()
        if len(df) <= WARMUP + 1:
            continue
        trades.extend(_ticker_trades(sym, df))
    return trades
