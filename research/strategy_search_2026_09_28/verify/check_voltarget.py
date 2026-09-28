import importlib.util, random, math
import numpy as np, pandas as pd
import harness as H
spec = importlib.util.spec_from_file_location("cand", "strategies/riskmgmt_voltarget_trend_spy.py"); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load(); spy = data["SPY"]
trades = mod.strategy(data)
# group by entry date
g = {}
for t in trades: g.setdefault(t["entry_date"], []).append(t)
# independent recomputation using groupby on year/month
c = spy["Close"]
ym = [(d.year, d.month) for d in spy.index]
me = c.groupby(pd.Index(ym, tupleize_cols=False)).last()
keys = list(me.index)
ret = c.pct_change()
indep = {}
for i, key in enumerate(keys):
    rows = [j for j, k in enumerate(ym) if k == key]
    t = rows[-1]
    if t + 1 >= len(spy): continue
    if i < 9: continue
    sma = me.iloc[i-9:i+1].mean()
    if t < 126: continue
    sig = math.sqrt(252 * (ret.iloc[t-125:t+1] ** 2).mean())
    k = int(math.floor(5 * min(1, 0.12 / sig) + 1e-9))
    up = c.iloc[t] > sma
    indep[spy.index[t+1]] = (up, k, sig, sma, c.iloc[t], spy.index[t])
mism = 0
for F, (up, k, sig, sma, ct, tdate) in indep.items():
    got = len(g.get(F, []))
    exp = k if (up and k >= 1) else 0
    if got != exp: mism += 1; print("MISMATCH", F, got, exp)
print("decisions", len(indep), "mismatches", mism, "trade-months", len(g))
print("k distribution among traded months:", pd.Series([len(v) for v in g.values()]).value_counts().sort_index().to_dict())
print("months with trend up:", sum(v[0] for v in indep.values()), "of", len(indep))
avg_exp = np.mean([len(g.get(F, []))/5 for F in indep]); print("avg exposure", round(avg_exp,3))
# timing check on first and random trade-months
sel = sorted(g)[:3] + random.Random(7).sample(sorted(g), 5) + sorted(g)[-2:]
for F in sel:
    t0 = g[F][0]; up, k, sig, sma, ct, tdate = indep[F]
    iF = spy.index.get_loc(F); iX = spy.index.get_loc(t0["exit_date"])
    print(f"t={tdate.date()} C(t)={ct:.2f} SMA10={sma:.2f} sig={sig:.4f} k={k} | F={F.date()} Open(F)={spy['Open'].iloc[iF]:.4f} entry_px={t0['entry_px']:.4f} "
          f"| X={t0['exit_date'].date()} exit_px={t0['exit_px']:.4f} Open(X)={spy['Open'].iloc[iX]:.4f} Close(X)={spy['Close'].iloc[iX]:.4f} "
          f"| t is last of month: {spy.index[iF-1]==tdate and spy.index[iF].month!=tdate.month} X first of m+2: {spy.index[iX-1].month != spy.index[iX].month}")
    assert all(x == t0 for x in g[F]) and len(g[F]) == k
