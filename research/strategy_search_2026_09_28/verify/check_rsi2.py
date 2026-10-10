"""Independent pure-python re-implementation + hand checks for meanrev_connors_rsi2_stocks."""
import importlib.util, random, math
import numpy as np, pandas as pd
import harness as H
spec = importlib.util.spec_from_file_location("cand", "strategies/meanrev_connors_rsi2_stocks.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
trades = mod.strategy(data)

def ref(sym, df):
    C = [float(x) for x in df["Close"]]; O = [float(x) for x in df["Open"]]; n = len(C)
    # Wilder RSI(2), loop form, seeded on first diff
    ag = al = None; rsi = [math.nan] * n
    for i in range(1, n):
        d = C[i] - C[i-1]; g = max(d, 0.0); l = max(-d, 0.0)
        if ag is None: ag, al = g, l
        else: ag = 0.5 * g + 0.5 * ag; al = 0.5 * l + 0.5 * al
        rsi[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    def sma(i, k): return sum(C[i-k+1:i+1]) / k if i >= k - 1 else math.nan
    out = []; t = 0
    while t < n - 1:
        s200 = sma(t, 200)
        if not (not math.isnan(s200) and C[t] > s200 and rsi[t] < 5):
            t += 1; continue
        u = None
        for j in range(t + 1, n):
            if C[j] > sma(j, 5): u = j; break
        if u is None or u == n - 1:
            out.append((sym, df.index[t+1], O[t+1], df.index[n-1], C[n-1], rsi[t]))
            break
        out.append((sym, df.index[t+1], O[t+1], df.index[u+1], O[u+1], rsi[t]))
        t = u + 1
    return out

random.seed(7)
syms = [s for s in data if s not in ("SPY", "QQQ")]
check = random.sample(syms, 25) + ["AAPL"] if "AAPL" in data else random.sample(syms, 25)
mine = {}
for t in trades: mine.setdefault(t["ticker"], []).append((t["ticker"], t["entry_date"], t["entry_px"], t["exit_date"], t["exit_px"], t["rank"]))
mism = 0
for s in check:
    r = ref(s, data[s]); m = mine.get(s, [])
    ok = len(r) == len(m) and all(a[0]==b[0] and a[1]==b[1] and a[3]==b[3] and abs(a[2]-b[2])<1e-9 and abs(a[4]-b[4])<1e-9 and abs(a[5]-b[5])<1e-6 for a, b in zip(r, m))
    if not ok: mism += 1; print("MISMATCH", s, len(r), len(m))
print("reference check:", len(check), "tickers,", mism, "mismatches")
print("SPY/QQQ trades:", sum(t["ticker"] in ("SPY","QQQ") for t in trades))

# timing checks on every trade
bad = 0
for t in trades:
    df = data[t["ticker"]]; ix = df.index
    e = ix.get_loc(t["entry_date"]); x = ix.get_loc(t["exit_date"])
    if df["Open"].iloc[e] != t["entry_px"]: bad += 1
    if not (x > e or (x == e and x == len(ix)-1)):
        pass
    if x == len(ix)-1 and t["exit_px"] == df["Close"].iloc[x]: continue
    if df["Open"].iloc[x] != t["exit_px"] or x < e + 1: bad += 1
print("timing/price violations:", bad)
same_day = sum(t["entry_date"] == t["exit_date"] for t in trades)
print("same-day entry==exit trades:", same_day)

# hand-print first 5 trades and 5 random ones with the underlying rows
def show(t):
    df = data[t["ticker"]]; ix = df.index; c = df["Close"]
    e = ix.get_loc(t["entry_date"]); s = e - 1
    sma200 = c.rolling(200).mean(); sma5 = c.rolling(5).mean(); rsi = mod._rsi2(c)
    x = ix.get_loc(t["exit_date"])
    print(f"\n{t['ticker']} signal {ix[s].date()} C={c.iloc[s]:.4f} SMA200={sma200.iloc[s]:.4f} RSI2={rsi.iloc[s]:.3f} rank={t['rank']:.3f}")
    print(f"  entry {ix[e].date()} Open={df['Open'].iloc[e]:.4f} vs trade {t['entry_px']:.4f}")
    for j in range(e, min(x + 1, len(ix))):
        print(f"   row {ix[j].date()} C={c.iloc[j]:.4f} SMA5={sma5.iloc[j]:.4f} C>SMA5={c.iloc[j] > sma5.iloc[j]}")
    print(f"  exit {ix[x].date()} Open={df['Open'].iloc[x]:.4f} Close={c.iloc[x]:.4f} vs trade {t['exit_px']:.4f}")
for t in sorted(trades, key=lambda t: t["entry_date"])[:5]: show(t)
for t in random.sample(trades, 5): show(t)

# end-of-data trades and extremes
last = [t for t in trades if t["exit_date"] == data[t["ticker"]].index[-1]]
print("\ntrades exiting at final row:", len(last), [(t["ticker"], str(t["entry_date"].date()), str(t["exit_date"].date())) for t in last[:10]])
r = np.array([t["exit_px"]/t["entry_px"]-1 for t in trades])
order = np.argsort(r)
print("worst:", [(trades[i]["ticker"], str(trades[i]["entry_date"].date()), round(r[i],3)) for i in order[:5]])
print("best:", [(trades[i]["ticker"], str(trades[i]["entry_date"].date()), round(r[i],3)) for i in order[-5:]])
hold = [(data[t["ticker"]].index.get_loc(t["exit_date"]) - data[t["ticker"]].index.get_loc(t["entry_date"])) for t in trades]
print("hold rows median/mean/max:", np.median(hold), round(np.mean(hold),2), max(hold))
