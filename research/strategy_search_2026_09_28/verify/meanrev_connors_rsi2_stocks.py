"""Independent re-implementation of meanrev_connors_rsi2_stocks (Connors RSI(2) < 5 above SMA200,
exit on first close above SMA5). Written from the pre-registered spec only.

Timing: signal on row t uses Close/indicators ending at row t; entry fills at Open[t+1].
Exit signal on row u >= t+1 (C_u > SMA5_u) fills at Open[u+1]; if u is the last row or no
exit signal occurs, exit at Close of the ticker's last row.
"""
import numpy as np
import pandas as pd

EXCLUDE = {"SPY", "QQQ"}


def _rsi2(close: pd.Series) -> pd.Series:
    d = close.diff()
    gain = d.clip(lower=0)
    loss = (-d).clip(lower=0)
    ag = gain.ewm(alpha=0.5, adjust=False).mean()
    al = loss.ewm(alpha=0.5, adjust=False).mean()
    rsi = 100.0 - 100.0 / (1.0 + ag / al)
    rsi = rsi.where(al != 0, 100.0)
    rsi[d.isna()] = np.nan  # row 0 has no diff
    return rsi


def _ticker_trades(sym: str, df: pd.DataFrame) -> list:
    df = df.sort_index()
    c = df["Close"].astype(float)
    o = df["Open"].astype(float).to_numpy()
    cv = c.to_numpy()
    sma200 = c.rolling(200, min_periods=200).mean().to_numpy()
    sma5 = c.rolling(5, min_periods=5).mean().to_numpy()
    rsi = _rsi2(c).to_numpy()
    idx = df.index
    n = len(df)

    with np.errstate(invalid="ignore"):
        entry_sig = (~np.isnan(sma200)) & (cv > sma200) & (rsi < 5)
        exit_sig = cv > sma5  # NaN comparisons are False

    trades = []
    t = 0
    while t < n:
        if not entry_sig[t]:
            t += 1
            continue
        if t + 1 >= n:  # signal on last row: no trade
            break
        e = t + 1
        # first exit signal u >= e
        u = None
        for k in range(e, n):
            if exit_sig[k]:
                u = k
                break
        if u is None or u + 1 >= n:
            x_date, x_px = idx[n - 1], cv[n - 1]
            nxt = n  # no more trades possible
        else:
            x_date, x_px = idx[u + 1], o[u + 1]
            nxt = u + 1
        trades.append({
            "ticker": sym,
            "entry_date": idx[e],
            "entry_px": float(o[e]),
            "exit_date": x_date,
            "exit_px": float(x_px),
            "rank": float(rsi[t]),
        })
        t = nxt
    return trades


def strategy(data):
    out = []
    for sym, df in data.items():
        if sym in EXCLUDE:
            continue
        out.extend(_ticker_trades(sym, df))
    return out
