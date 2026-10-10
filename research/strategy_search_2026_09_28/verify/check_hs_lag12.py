"""Independent re-derivation + hand checks for calendar_seasonality_heston_sadka_lag12."""
import importlib.util, math, random, sys
import pandas as pd
sys.path.insert(0, ".")
import harness as H

spec = importlib.util.spec_from_file_location("cand", "strategies/calendar_seasonality_heston_sadka_lag12.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
trades = mod.strategy(data)

# ---- independent calendar: year/month groupby, drop the final month ----
T = data["SPY"].index
ym = [(d.year, d.month) for d in T]
me = {}
for d, k in zip(T, ym):
    me[k] = d  # ascending -> last date per month wins
me.pop(ym[-1])  # final month is incomplete
def shift(k, n):
    y, m = k; t = y * 12 + (m - 1) + n; return (t // 12, t % 12 + 1)
print("final SPY date", T[-1].date(), "| last complete month-end", max(me.values()).date())
print("ME(2016-09)", me[(2016, 9)].date(), "ME(2016-10)", me[(2016, 10)].date())

indep = []
months_n = {}
for k in sorted(me):
    ks = [shift(k, -13), shift(k, -12), shift(k, -1), k]
    if not all(x in me for x in ks):
        continue
    a, b, e, x = (me[z] for z in ks)
    rows = []
    for s, df in data.items():
        if s in ("SPY", "QQQ"):
            continue
        c = df["Close"]
        if all(d in c.index and c[d] > 0 for d in (a, b, e)):
            rows.append((s, c[b] / c[a] - 1, float(c[e])))
    rows.sort(key=lambda r: (-r[1], r[0]))
    kk = max(1, math.ceil(len(rows) / 10)) if rows else 0
    months_n[k] = (len(rows), kk)
    for rnk, (s, R, epx) in enumerate(rows[:kk], 1):
        c = data[s]["Close"]
        if x in c.index:
            xd = x
        elif (c.index > x).any():
            xd = c.index[c.index > x][0]
        elif (c.index > e).any():
            xd = c.index[-1]
        else:
            xd = e
        indep.append((s, e, epx, xd, float(c[xd]), rnk))

mine = [(t["ticker"], t["entry_date"], t["entry_px"], t["exit_date"], t["exit_px"], t["rank"]) for t in trades]
print("trades module", len(mine), "independent", len(indep), "identical:", sorted(mine, key=str) == sorted(indep, key=str))
print("holding months", len(months_n), "first", min(months_n), "last", max(months_n))
ns = list(months_n.values())
print("N eligible range", min(n for n, _ in ns), max(n for n, _ in ns), "| k range", min(k for _, k in ns), max(k for _, k in ns))
print("per-month trade count == k for every month:",
      all(sum(1 for t in trades if (t["entry_date"].year, t["entry_date"].month) == shift(m, -1)) == kk for m, (_, kk) in months_n.items()))

# ---- fallback-exit usage ----
fb = [t for t in trades if t["exit_date"] != me[(t["exit_date"].year, t["exit_date"].month)] or
      t["exit_date"] - t["entry_date"] > pd.Timedelta(days=40)]
print("trades not exiting on a SPY month-end or with >40d hold:", len(fb), fb[:5])
flat = [t for t in trades if t["exit_date"] == t["entry_date"]]
print("flat trades:", len(flat))
# entry/exit are always the next consecutive month-ends
ok = all(shift((t["entry_date"].year, t["entry_date"].month), 1) == (t["exit_date"].year, t["exit_date"].month) for t in trades)
print("every exit is in the month after entry:", ok)

# ---- hand checks: first trade and random trades, printed with raw rows ----
def show(t):
    s = t["ticker"]; c = data[s]["Close"]
    e = t["entry_date"]; P = (e.year, e.month); P = shift(P, 1)
    a, b = me[shift(P, -13)], me[shift(P, -12)]
    print(f"\n{s} rank {t['rank']} holding {P}: signal window {a.date()} -> {b.date()} "
          f"closes {c[a]:.4f} -> {c[b]:.4f} R={c[b]/c[a]-1:+.4f}")
    print(f"  entry {e.date()} px {t['entry_px']:.4f} (Close row {c[e]:.4f}; Open {data[s]['Open'][e]:.4f})"
          f" | exit {t['exit_date'].date()} px {t['exit_px']:.4f} (Close row {c[t['exit_date']]:.4f}) | ME(P) {me[P].date()}")
    # confirm rank against all eligible names that month
    elig = []
    for s2, df in data.items():
        if s2 in ("SPY", "QQQ"): continue
        c2 = df["Close"]
        if all(d in c2.index and c2[d] > 0 for d in (a, b, e)):
            elig.append((-(c2[b] / c2[a] - 1), s2))
    elig.sort()
    pos = [x[1] for x in elig].index(s) + 1
    print(f"  eligible N={len(elig)} k={math.ceil(len(elig)/10)} position of {s} in sort = {pos}; next name out = {elig[math.ceil(len(elig)/10)]}")

srt = sorted(trades, key=lambda t: (t["entry_date"], t["rank"]))
show(srt[0]); show(srt[1])
random.seed(7)
for t in random.sample(trades, 4):
    show(t)
