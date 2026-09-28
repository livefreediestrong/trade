"""Independent naive replica (slices .loc[:D], np.polyfit, np.corrcoef) + hand checks for xsmom_clenow_sotm_5slot."""
import importlib.util, math, random, sys
import numpy as np, pandas as pd
sys.path.insert(0, ".")
import harness as H

spec = importlib.util.spec_from_file_location("cand", "strategies/xsmom_clenow_sotm_5slot.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
trades = mod.strategy(data)
cal = data["SPY"].index
tick = sorted(s for s in data if s not in ("SPY", "QQQ"))

def ind_naive(s, D):
    df = data[s].loc[:D]
    if len(df) == 0 or df.index[-1] != D or len(df) < 100:
        return None
    c = df["Close"].to_numpy()
    y = np.log(c[-90:]); x = np.arange(90)
    b = np.polyfit(x, y, 1)[0]
    r2 = 0.0 if np.std(y) == 0 else np.corrcoef(x, y)[0, 1] ** 2
    score = (math.exp(b) ** 250 - 1) * r2
    trend = c[-1] > c[-100:].mean()
    rets = c[-91:][1:] / c[-91:][:-1] - 1
    gap = np.abs(rets).max() > 0.15
    return score, trend, gap

cache = {}
def xsec(D):
    if D in cache: return cache[D]
    vals = {s: ind_naive(s, D) for s in tick}
    vals = {s: v for s, v in vals.items() if v is not None}
    ranked = sorted(vals, key=lambda s: (-vals[s][0], s))
    N = len(ranked); cut = math.ceil(0.20 * N)
    out = (vals, {s: i + 1 for i, s in enumerate(ranked)}, cut, ranked)
    cache[D] = out; return out

# ---------- full naive re-simulation ----------
spy_c = data["SPY"]["Close"]
pos = []
for i in range(1, len(cal)):
    W = cal[i]
    if W.weekday() != 2: continue
    D = cal[i - 1]
    if len(spy_c.loc[:D]) < 200: continue
    vals, rank, cut, ranked = xsec(D)
    regime = spy_c.loc[D] > spy_c.loc[:D].iloc[-200:].mean()
    for p in pos:
        if p["exit_date"] is not None: continue
        s = p["ticker"]
        if D not in data[s].index: continue
        sc, tr, gp = vals[s]
        if rank[s] > cut or not tr or gp:
            nxt = data[s].loc[W:]
            if len(nxt): p["exit_date"], p["exit_px"] = nxt.index[0], float(nxt["Open"].iloc[0])
            else: p["exit_date"], p["exit_px"] = data[s].index[-1], float(data[s]["Close"].iloc[-1])
    if not regime: continue
    held = [p["ticker"] for p in pos if p["exit_date"] is None or p["exit_date"] > W]
    free = 5 - len(held)
    for s in ranked:
        if free <= 0: break
        if rank[s] > cut: break
        sc, tr, gp = vals[s]
        if s in held or not tr or gp or W not in data[s].index: continue
        pos.append({"ticker": s, "entry_date": W, "entry_px": float(data[s].loc[W, "Open"]), "rank": rank[s], "exit_date": None, "exit_px": None})
        held.append(s); free -= 1
for p in pos:
    if p["exit_date"] is None:
        s = p["ticker"]; p["exit_date"], p["exit_px"] = data[s].index[-1], float(data[s]["Close"].iloc[-1])

key = lambda t: (t["entry_date"], t["ticker"])
a = sorted(trades, key=key); b = sorted(pos, key=key)
print("module trades", len(a), "naive trades", len(b))
mism = [(x, y) for x, y in zip(a, b) if any((x[k] != y[k]) if not isinstance(x[k], float) else abs(x[k] - y[k]) > 1e-9 for k in ("ticker", "entry_date", "entry_px", "exit_date", "exit_px", "rank"))]
print("mismatches", len(mism), mism[:3])

# ---------- concurrency (harness semantics: exits on d processed before entries on d) ----------
mx = 0
for d in cal:
    n = sum(1 for t in trades if t["entry_date"] <= d < t["exit_date"])
    mx = max(mx, n)
print("max concurrent open positions", mx)

# ---------- hand checks: first 5 + 8 random ----------
random.seed(7)
sample = a[:5] + random.sample(a[5:], 8)
for t in sample:
    s, W = t["ticker"], t["entry_date"]
    iW = cal.get_loc(W); D = cal[iW - 1]
    vals, rank, cut, _ = xsec(D)
    sc, tr, gp = vals[s]
    regime = spy_c.loc[D] > spy_c.loc[:D].iloc[-200:].mean()
    print(f"\n{s} entry {W.date()} ({W.day_name()}) D={D.date()} ({D.day_name()}) open_W={data[s].loc[W,'Open']:.4f} entry_px={t['entry_px']:.4f} "
          f"rank={rank[s]}/{cut} (trade rank {t['rank']}) score={sc:.3f} trend={tr} gap={gp} regime={regime}")
    # exit side: find the decision that caused the exit
    ed = t["exit_date"]
    wk = [w for w in cal[(cal > W) & (cal <= ed)] if w.weekday() == 2]
    fired = None
    for w in wk:
        Dx = cal[cal.get_loc(w) - 1]
        if Dx not in data[s].index: continue
        v2, r2, c2, _ = xsec(Dx)
        sc2, tr2, gp2 = v2[s]
        sell = r2[s] > c2 or not tr2 or gp2
        if sell:
            fired = (w, Dx, r2[s], c2, tr2, gp2); break
    if fired:
        w, Dx, rk, c2, tr2, gp2 = fired
        print(f"   exit {ed.date()} px={t['exit_px']:.4f} open@exit={data[s].loc[ed,'Open']:.4f}; first sell signal D={Dx.date()} -> W={w.date()} rank={rk}/{c2} trend={tr2} gap={gp2}")
    else:
        print(f"   exit {ed.date()} px={t['exit_px']:.4f} close@last={data[s]['Close'].iloc[-1]:.4f} (END close, last row {data[s].index[-1].date()})")
