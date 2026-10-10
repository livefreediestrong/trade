"""Audit helper: trade-level diff of original vs independent Turtle S2, plus data/trade sanity checks."""
import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import harness as H  # noqa: E402


def ld(p, name):
    s = importlib.util.spec_from_file_location(name, HERE / p)
    m = importlib.util.module_from_spec(s)
    s.loader.exec_module(m)
    return m


data = H.load()
A = ld("strategies/trend_turtle_s2_55_20.py", "orig").strategy(data)
B = ld("verify/trend_turtle_s2_55_20.py", "mine").strategy(data)


def key(t):
    return (t["ticker"], t["entry_date"], t["exit_date"], round(t["entry_px"], 8), round(t["exit_px"], 8), round(t["rank"], 6))


sa, sb = sorted(map(key, A)), sorted(map(key, B))
print("trade-level identical:", sa == sb, len(sa), len(sb))

bad = 0
for s, df in data.items():
    b = ((df.Open > df.High * 1.0001) | (df.Open < df.Low * 0.9999) | (df.Close > df.High * 1.0001)
         | (df.Close < df.Low * 0.9999) | (df.Low <= 0)).sum()
    if b:
        print("OHLC inconsistent", s, int(b))
        bad += b
print("bad rows total", bad)

lows = {s: df["Low"] for s, df in data.items()}
highs = {s: df["High"] for s, df in data.items()}
opens = {s: df["Open"] for s, df in data.items()}
out = sum(1 for t in A if t["exit_px"] < lows[t["ticker"]][t["exit_date"]] - 1e-9
          or t["exit_px"] > highs[t["ticker"]][t["exit_date"]] + 1e-9)
print("exit px outside day range:", out)
r = np.array([t["exit_px"] / t["entry_px"] - 1 for t in A])
print("ret quantiles [0,1,5,25,50,75,95,99,100]%:", np.percentile(r, [0, 1, 5, 25, 50, 75, 95, 99, 100]).round(3))
for t in sorted(A, key=lambda t: -(t["exit_px"] / t["entry_px"]))[:8]:
    print("  top", t["ticker"], t["entry_date"].date(), t["exit_date"].date(), round(t["exit_px"] / t["entry_px"] - 1, 2))
print("mean bps excl top 1%:", round(r[r < np.percentile(r, 99)].mean() * 1e4, 1), " median bps:", round(np.median(r) * 1e4, 1))
print("same-day round trips", sum(t["entry_date"] == t["exit_date"] for t in A))
print("closed at last bar", sum(t["exit_date"] == data[t["ticker"]].index[-1] for t in A))
print("exits below the open (gap/intraday stop):", sum(1 for t in A if t["exit_px"] < opens[t["ticker"]][t["exit_date"]] - 1e-12))
print("exits AT the open (gap below stop):", sum(1 for t in A if abs(t["exit_px"] - opens[t["ticker"]][t["exit_date"]]) < 1e-12
                                                  and t["exit_date"] != data[t["ticker"]].index[-1]))
