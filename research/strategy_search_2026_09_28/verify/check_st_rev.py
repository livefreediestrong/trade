import importlib.util, random
import numpy as np, pandas as pd
import harness as H
spec = importlib.util.spec_from_file_location("cand", "strategies/meanrev_st_reversal_monthly.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
data = H.load()
trades = mod.strategy(data)
spy = data["SPY"].index

# --- independent month-end calendar (groupby last date per month, drop the final month if it ends at last row)
g = pd.Series(spy, index=spy).groupby([spy.year, spy.month]).max()
me2 = pd.DatetimeIndex(g.values)
me2 = me2[me2 != spy[-1]]
me = mod.month_ends(spy)
print("month-ends equal:", me.equals(me2), len(me), me[:3].date.tolist(), me[-3:].date.tolist(), "last spy", spy[-1].date())

# --- independent full rebuild with a Close panel (different code path)
stocks = [s for s in data if s not in ("SPY", "QQQ")]
panel = pd.DataFrame({s: data[s]["Close"] for s in stocks}).reindex(spy)
exp = {}
for m in range(1, len(me)):
    a, b = panel.loc[me[m-1]], panel.loc[me[m]]
    r = (b / a - 1).dropna()
    k = int(np.floor(0.30 * len(r) + 1e-9))
    df = r.rename("r").reset_index().rename(columns={"index": "t"}).sort_values(["r", "t"]).head(k)
    for _, row in df.iterrows():
        exp[(me[m], row["t"])] = row["r"]
got = {}
for t in trades:
    # recover formation date: last month-end strictly before entry
    fm = me[me < t["entry_date"]][-1]
    got[(fm, t["ticker"])] = t
print("expected selections", len(exp), "trades", len(trades))
missing = set(exp) - set(got); extra = set(got) - set(exp)
print("selected but no trade:", len(missing), sorted(missing)[:10])
print("trade not in selection:", len(extra))

# --- per-trade timing checks
bad = 0
for t in trades:
    df = data[t["ticker"]]; idx = df.index
    fm = me[me < t["entry_date"]][-1]; mi = me.get_loc(fm)
    nxt = me[mi+1] if mi+1 < len(me) else None
    # entry = first row after formation date, at Open
    e_exp = idx[idx > fm][0]
    ok = t["entry_date"] == e_exp and t["entry_px"] == float(df.at[e_exp, "Open"])
    ok &= abs(t["rank"] - exp[(fm, t["ticker"])]) < 1e-12
    if nxt is not None:
        ok &= t["entry_date"] <= nxt
        after = idx[idx > nxt]
        if len(after):
            ok &= t["exit_date"] == after[0] and t["exit_px"] == float(df.at[after[0], "Open"])
        else:
            ok &= t["exit_date"] == idx[-1] and t["exit_px"] == float(df["Close"].iloc[-1])
    else:
        ok &= t["exit_date"] == idx[-1] and t["exit_px"] == float(df["Close"].iloc[-1])
    bad += not ok
print("timing mismatches:", bad)

# --- by-hand prints: first trade and 4 random trades
random.seed(7)
st = sorted(trades, key=lambda t: (t["entry_date"], t["rank"], t["ticker"]))
for t in [st[0]] + random.sample(trades, 4):
    df = data[t["ticker"]]
    fm = me[me < t["entry_date"]][-1]; mi = me.get_loc(fm); prev = me[mi-1]; nxt = me[mi+1] if mi+1 < len(me) else None
    print("\n", t["ticker"], "formation", prev.date(), "->", fm.date(), "next ME", nxt.date() if nxt is not None else None)
    print("  Close(prev ME)=%.6g Close(ME)=%.6g r=%.5f rank=%.5f" % (df.at[prev, "Close"], df.at[fm, "Close"], df.at[fm, "Close"]/df.at[prev, "Close"]-1, t["rank"]))
    lo = df.index.get_loc(fm)
    print(df.iloc[lo:lo+2][["Open", "Close"]].to_string())
    if nxt is not None:
        lo2 = df.index.get_indexer([nxt])[0]
        print(df.iloc[lo2:lo2+2][["Open", "Close"]].to_string())
    print("  trade:", t["entry_date"].date(), t["entry_px"], "->", t["exit_date"].date(), t["exit_px"])

# --- counts / edge cases
per_month = pd.Series([t["entry_date"].strftime("%Y-%m") for t in trades]).value_counts().sort_index()
print("\ntrades per month min/median/max", per_month.min(), per_month.median(), per_month.max())
gaps = [t for t in trades if t["entry_date"] != spy[spy > me[me < t["entry_date"]][-1]][0]]
print("entries not on next SPY day:", len(gaps), [(t["ticker"], t["entry_date"].date()) for t in gaps[:5]])
last = [t for t in trades if t["entry_date"] > me[-1]]
print("last-month trades:", len(last), sorted({(t["exit_date"].date(), "close") for t in last}))
early_exit = [t for t in trades if t["entry_date"] <= me[-1] and t["exit_date"] == data[t["ticker"]].index[-1] and t["exit_px"] == float(data[t["ticker"]]["Close"].iloc[-1])]
print("non-final-month trades exiting at final close:", len(early_exit))
same_consec = sum(1 for a in trades for b in [] )
