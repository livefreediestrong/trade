import json, pickle, sys, urllib.request
import yfinance as yf
out = sys.argv[1]
s = json.load(urllib.request.urlopen("http://127.0.0.1:5056/api/live-agent", timeout=30))
syms = sorted(set(s["policy"]["symbols"]) | {"SPY", "QQQ", "META", "INTC"})
yf_syms = [x.replace(".", "-") for x in syms]
raw = yf.download(yf_syms, period="10y", interval="1d", auto_adjust=True, group_by="ticker", threads=True, progress=False)
data = {}
for sym, ys in zip(syms, yf_syms):
    try:
        df = raw[ys][["Open", "High", "Low", "Close", "Volume"]].dropna()
    except KeyError:
        continue
    df = df[(df["Close"] > 0) & (df["Open"] > 0)]
    if len(df) >= 300:
        df.index = df.index.tz_localize(None) if df.index.tz is not None else df.index
        data[sym] = df
pickle.dump(data, open(out, "wb"))
print("requested", len(syms), "usable", len(data), "SPY rows", len(data["SPY"]), data["SPY"].index[0].date(), "->", data["SPY"].index[-1].date())
