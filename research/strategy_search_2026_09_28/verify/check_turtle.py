"""Independent brute-force re-implementation + hand checks for trend_turtle_s2_55_20."""
import importlib.util
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import harness as H  # noqa: E402

spec = importlib.util.spec_from_file_location("c", HERE / "strategies/trend_turtle_s2_55_20.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

data = H.load()
trades = m.strategy(data)


def n_series(df):
    """N via pandas ewm(alpha=1/20, adjust=False) seeded with SMA of TR_1..TR_20 at row 20."""
    h, l, c = df["High"], df["Low"], df["Close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, h - pc, pc - l], axis=1).max(axis=1, skipna=False)
    seed = tr.iloc[1:21].mean()
    s = tr.iloc[20:].copy()
    s.iloc[0] = seed
    n = s.ewm(alpha=1 / 20, adjust=False).mean()
    return n.reindex(df.index)


def brute(sym, df):
    O, Hh, L, C = (df[k].tolist() for k in ("Open", "High", "Low", "Close"))
    N = n_series(df).tolist()
    idx = df.index
    n = len(df)
    out, i = [], 63
    flat_from = 0  # first row whose close may signal
    while True:
        # find next signal row t >= max(63, flat_from)
        t = None
        for k in range(max(63, flat_from), n - 1):
            if Hh[k] > max(Hh[k - 55:k]):
                e = k + 1
                if O[e] <= min(L[e - 20:e]):
                    continue
                t = k
                break
        if t is None:
            break
        e = t + 1
        stop2n = O[e] - 2 * N[t]
        rank = -(C[t] - C[t - 63]) / N[t]
        ex = None
        for d in range(e, n):
            S = max(stop2n, min(L[d - 20:d]))
            if L[d] <= S:
                ex = (d, min(O[d], S))
                break
        if ex is None:
            out.append((sym, idx[e], O[e], idx[n - 1], C[n - 1], rank))
            break
        out.append((sym, idx[e], O[e], idx[ex[0]], ex[1], rank))
        flat_from = ex[0]  # may signal at the close of the exit row
    return out


mine = sorted((t["ticker"], t["entry_date"], t["entry_px"], t["exit_date"], t["exit_px"], t["rank"]) for t in trades)
ref = []
for s in sorted(data):
    if s in ("SPY", "QQQ"):
        continue
    ref.extend(brute(s, data[s]))
ref.sort()
print("module trades", len(mine), "brute trades", len(ref))
mism = 0
for a, b in zip(mine, ref):
    if a[0] != b[0] or a[1] != b[1] or a[3] != b[3] or not np.isclose(a[2], b[2]) or not np.isclose(a[4], b[4], rtol=1e-9) or not np.isclose(a[5], b[5], rtol=1e-6):
        mism += 1
        if mism <= 5:
            print("MISMATCH", a, b)
print("mismatches", mism + abs(len(mine) - len(ref)))


def show(t):
    df = data[t["ticker"]]
    idx = df.index
    e = idx.get_loc(t["entry_date"])
    x = idx.get_loc(t["exit_date"])
    s = e - 1
    N = n_series(df)
    hh55 = df["High"].iloc[s - 55:s].max()
    ll20_e = df["Low"].iloc[e - 20:e].min()
    stop2n = df["Open"].iloc[e] - 2 * N.iloc[s]
    ll20_x = df["Low"].iloc[x - 20:x].min()
    S_x = max(stop2n, ll20_x)
    print(f"\n{t['ticker']} signal {idx[s].date()} High={df['High'].iloc[s]:.4f} > HH55={hh55:.4f}: {df['High'].iloc[s] > hh55}"
          f" | entry {idx[e].date()} px={t['entry_px']:.4f} Open={df['Open'].iloc[e]:.4f} LL20_e={ll20_e:.4f}"
          f" | N_t={N.iloc[s]:.4f} stop2N={stop2n:.4f}")
    earlier = [idx[d].date() for d in range(e, x) if df["Low"].iloc[d] <= max(stop2n, df["Low"].iloc[d - 20:d].min())]
    last = x == len(df) - 1
    print(f"  exit {idx[x].date()} px={t['exit_px']:.4f} Low={df['Low'].iloc[x]:.4f} Open={df['Open'].iloc[x]:.4f} S={S_x:.4f}"
          f" min(Open,S)={min(df['Open'].iloc[x], S_x):.4f} lastrow={last} Close={df['Close'].iloc[x]:.4f} | earlier stop rows: {earlier}"
          f" | rank={t['rank']:.4f} vs {-(df['Close'].iloc[s] - df['Close'].iloc[s - 63]) / N.iloc[s]:.4f}")


first = sorted(trades, key=lambda t: (t["entry_date"], t["ticker"]))[:5]
random.seed(7)
for t in first + random.sample(trades, 6) + [t for t in trades if t["entry_date"] == t["exit_date"]][:1] + \
        [t for t in trades if t["exit_date"] == data[t["ticker"]].index[-1]][:1]:
    show(t)
