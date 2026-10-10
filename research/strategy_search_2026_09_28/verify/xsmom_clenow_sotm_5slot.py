"""Independent re-implementation of xsmom_clenow_sotm_5slot (Clenow 'Stocks on the Move').

Written from the pre-registered spec only (verification pass).

Timing: decision day D = SPY session immediately before each Wednesday SPY session W.
All indicators use each stock's own rows dated <= D. Fills at the Open on W (exits of
stocks with no row on W fill at their next available Open).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

REG_N = 90
ANN = 250
SMA_N = 100
GAP_N = 90
GAP_TH = 0.15
MIN_ROWS = 100
SPY_SMA = 200
SLOTS = 5
TOP_FRAC = 0.20


def _stock_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Indicators on the stock's OWN rows (trailing windows ending at each row)."""
    c = df["Close"].to_numpy(dtype=float)
    n = len(c)
    out = pd.DataFrame(index=df.index)
    out["rows"] = np.arange(1, n + 1)
    score = np.full(n, np.nan)
    if n >= REG_N:
        y = np.log(c)
        W = sliding_window_view(y, REG_N)            # row k covers y[k .. k+89], ends at k+89
        x = np.arange(REG_N, dtype=float)
        xc = x - x.mean()
        sxx = float((xc ** 2).sum())
        cov = W @ xc                                  # sum xc*y  (= sum xc*(y-ybar))
        b = cov / sxx
        yc = W - W.mean(axis=1, keepdims=True)
        syy = (yc ** 2).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            r2 = cov ** 2 / (sxx * syy)
        s = (np.exp(b * ANN) - 1.0) * r2              # exp(b)^250 - 1
        score[REG_N - 1:] = s
    out["score"] = score
    close = df["Close"].astype(float)
    sma = close.rolling(SMA_N, min_periods=SMA_N).mean()
    out["trend_ok"] = (close > sma) & sma.notna()
    ret = close / close.shift(1) - 1.0
    maxabs = ret.abs().rolling(GAP_N, min_periods=GAP_N).max()
    out["gap_bad"] = (maxabs > GAP_TH).fillna(False)   # not reached when rows >= 100 (maxabs defined)
    return out


def strategy(data):
    spy = data["SPY"]
    cal = spy.index
    spy_close = spy["Close"].astype(float)
    spy_sma = spy_close.rolling(SPY_SMA, min_periods=SPY_SMA).mean()
    regime = (spy_close > spy_sma) & spy_sma.notna()

    tickers = sorted(s for s in data if s not in ("SPY", "QQQ"))
    ind = {t: _stock_indicators(data[t]) for t in tickers}
    opens = {t: data[t]["Open"].astype(float) for t in tickers}
    closes = {t: data[t]["Close"].astype(float) for t in tickers}
    idx = {t: data[t].index for t in tickers}

    # panels aligned to SPY calendar; NaN where the stock has no row that day
    score_p = pd.DataFrame({t: ind[t]["score"] for t in tickers}).reindex(cal)
    rows_p = pd.DataFrame({t: ind[t]["rows"] for t in tickers}).reindex(cal)
    trend_p = pd.DataFrame({t: ind[t]["trend_ok"] for t in tickers}).reindex(cal)
    gap_p = pd.DataFrame({t: ind[t]["gap_bad"] for t in tickers}).reindex(cal)
    has_row = pd.DataFrame({t: pd.Series(True, index=idx[t]) for t in tickers}).reindex(cal).fillna(False).astype(bool)

    tick_arr = np.array(tickers)

    def next_open_on_or_after(t, day):
        ix = idx[t]
        k = ix.searchsorted(day)
        if k >= len(ix):
            return None
        return ix[k]

    trades = []
    held = {}      # ticker -> {"entry_date", "entry_px", "rank"}   (open, no exit decided)
    pending = []   # decided exits that have been assigned an exit date (already written as trades)

    wed_positions = [i for i in range(1, len(cal)) if cal[i].dayofweek == 2]
    for i in wed_positions:
        W = cal[i]
        D = cal[i - 1]
        if i - 1 < SPY_SMA - 1:          # SPY needs 200 rows through D
            continue

        # ---- ranking at D (only stocks with a row dated D and >= 100 rows through D) ----
        hr = has_row.iloc[i - 1].to_numpy()
        rw = rows_p.iloc[i - 1].to_numpy()
        valid = hr & (np.nan_to_num(rw, nan=0) >= MIN_ROWS)
        sc = score_p.iloc[i - 1].to_numpy()
        vt = tick_arr[valid]
        vs = sc[valid]
        order = sorted(range(len(vt)), key=lambda k: (-(vs[k] if not np.isnan(vs[k]) else -np.inf), vt[k]))
        N = len(vt)
        cutoff = math.ceil(TOP_FRAC * N)
        rank = {vt[k]: r + 1 for r, k in enumerate(order)}
        trend_row = trend_p.iloc[i - 1]
        gap_row = gap_p.iloc[i - 1]

        def top20(t):
            return t in rank and rank[t] <= cutoff

        # ---- occupied slots: positions not yet exited by W (exit fill on W frees the slot) ----
        still_pending = [p for p in pending if p["exit_date"] > W]
        pending = still_pending

        # ---- SELL (every D, whatever the regime) ----
        for t in sorted(list(held)):
            if not has_row.at[D, t]:
                continue                      # keep, re-check next week
            fail = (not top20(t)) or (not bool(trend_row[t])) or bool(gap_row[t])
            if not fail:
                continue
            pos = held.pop(t)
            xd = next_open_on_or_after(t, W)
            if xd is None:                    # no Open on/after W: close at the Close of the last row
                xd_ = idx[t][-1]
                trades.append({"ticker": t, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                               "exit_date": xd_, "exit_px": float(closes[t].iloc[-1]), "rank": pos["rank"]})
                continue
            tr = {"ticker": t, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                  "exit_date": xd, "exit_px": float(opens[t].at[xd]), "rank": pos["rank"]}
            trades.append(tr)
            if xd > W:
                pending.append(tr)            # slot stays occupied until the exit fills

        # ---- BUY (only if regime_on at D) ----
        if not bool(regime.iloc[i - 1]):
            continue
        free = SLOTS - len(held) - len(pending)
        if free <= 0:
            continue
        pend_names = {p["ticker"] for p in pending}
        for k in order:
            if free <= 0:
                break
            t = vt[k]
            if rank[t] > cutoff:
                break                          # ranking is sorted; no further top20 names
            if t in held or t in pend_names:
                continue
            if not bool(trend_row[t]) or bool(gap_row[t]):
                continue
            if not has_row.at[W, t]:
                continue
            held[t] = {"entry_date": W, "entry_px": float(opens[t].at[W]), "rank": int(rank[t])}
            free -= 1

    # ---- END: close remaining at the Close of their last row ----
    for t, pos in held.items():
        last = idx[t][-1]
        trades.append({"ticker": t, "entry_date": pos["entry_date"], "entry_px": pos["entry_px"],
                       "exit_date": last, "exit_px": float(closes[t].iloc[-1]), "rank": pos["rank"]})
    return trades
