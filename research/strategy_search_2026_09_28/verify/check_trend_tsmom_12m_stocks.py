"""Independent re-derivation of trend_tsmom_12m_stocks trades (groupby month + manual EWMA)."""
import importlib.util, math, random, sys
from pathlib import Path
import numpy as np, pandas as pd
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import harness as H

spec = importlib.util.spec_from_file_location("cand", HERE / "strategies/trend_tsmom_12m_stocks.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
trades = mod.strategy(data)
print("trades", len(trades))

def manual_sigma(close, t, com=60, minp=60):
    r = close.pct_change().iloc[: t + 1].dropna().to_numpy()   # causal: rows <= t only
    if len(r) < minp:
        return float("nan")
    a = 1 / (1 + com)
    w = (1 - a) ** np.arange(len(r) - 1, -1, -1)
    mu = (w * r).sum() / w.sum()
    var = (w * (r - mu) ** 2).sum() / w.sum()
    var *= w.sum() ** 2 / (w.sum() ** 2 - (w ** 2).sum())
    return math.sqrt(261) * math.sqrt(var)

# independent month-end identification: last row of each calendar-month group, drop final group
def indep(ticker, df, check_sigma=False):
    per = df.index.to_period("M")
    pos = pd.Series(np.arange(len(df)), index=df.index)
    last_rows = pos.groupby(per).max()
    last_rows = last_rows[last_rows != len(df) - 1]           # last row never a month-end
    me = {p: r for p, r in last_rows.items()}
    sig = math.sqrt(261) * df["Close"].pct_change().ewm(com=60, min_periods=60).std()
    out = []
    plist = list(last_rows.index)
    for k, p in enumerate(plist):
        t = me[p]
        prev = p - 12
        if prev not in me:
            continue
        s = manual_sigma(df["Close"], t) if check_sigma else sig.iloc[t]
        if not np.isfinite(s):
            continue
        r12 = df["Close"].iloc[t] / df["Close"].iloc[me[prev]] - 1
        if r12 <= 0:
            continue
        e = t + 1
        if k + 1 < len(plist):
            x = me[plist[k + 1]] + 1
            xd, xp = df.index[x], df["Open"].iloc[x]
        else:
            xd, xp = df.index[-1], df["Close"].iloc[-1]
        out.append((ticker, df.index[e], round(float(df["Open"].iloc[e]), 8), xd, round(float(xp), 8), s))
    return out

ref = []
for tk in sorted(data):
    if tk in ("SPY", "QQQ"):
        continue
    ref += indep(tk, data[tk])
mine = [(t["ticker"], t["entry_date"], round(t["entry_px"], 8), t["exit_date"], round(t["exit_px"], 8), t["rank"]) for t in trades]
A = {x[:5]: x[5] for x in mine}; B = {x[:5]: x[5] for x in ref}
print("independent trades", len(ref), "set equal", set(A) == set(B), "only mine", len(set(A) - set(B)), "only ref", len(set(B) - set(A)))
print("max rank diff", max(abs(A[k] - B[k]) for k in A))

# manual EWMA check on a random sample of trades
random.seed(7)
sample = random.sample(trades, 15)
for t in sample:
    df = data[t["ticker"]]
    e = df.index.get_loc(t["entry_date"]); tt = e - 1
    ms = manual_sigma(df["Close"], tt)
    per = df.index.to_period("M")
    m = per[tt]
    prev_rows = np.flatnonzero(per == m - 12)
    p_prev = df["Close"].iloc[prev_rows[-1]]
    r12 = df["Close"].iloc[tt] / p_prev - 1
    x = df.index.get_loc(t["exit_date"])
    print(f"{t['ticker']:6s} signal {df.index[tt].date()} (next row {df.index[tt+1].date()}) r12={r12:+.3f} "
          f"prevME {df.index[prev_rows[-1]].date()} | entry {t['entry_date'].date()} open={df['Open'].iloc[e]:.4f} vs {t['entry_px']:.4f} "
          f"| exit {t['exit_date'].date()} prev-row {df.index[x-1].date()} px_open={df['Open'].iloc[x]:.4f} vs {t['exit_px']:.4f} "
          f"| sigma {ms:.5f} vs rank {t['rank']:.5f}")
