"""Clenow 'Stocks on the Move' regression-momentum ranking, SPY regime filter, 5 slots, weekly (Wednesday).

Timing: trade day W = every SPY session that is a Wednesday; decision day D = the SPY session
immediately before W. All indicators use rows dated <= D only (trailing windows over each stock's
own rows). Entries and exits fill at the Open on W (an exit whose stock has no row on W fills at
that stock's next available Open).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

REG_WIN = 90        # regression window (rows)
ANN = 250           # annualization exponent
SMA_WIN = 100       # stock trend filter
MIN_ROWS = 100      # rows through D needed to be valid
GAP_WIN = 90        # number of daily returns in the gap window
GAP_MAX = 0.15
SPY_SMA = 200
SLOTS = 5
EXCLUDE = ("SPY", "QQQ")


def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Per-stock indicators on the stock's OWN rows; each value at row t uses rows <= t only."""
    close = df["Close"].to_numpy(dtype=float)
    n = len(close)
    score = np.full(n, np.nan)
    if n >= REG_WIN:
        y = np.log(close)
        win = sliding_window_view(y, REG_WIN)            # row k of win = y[k .. k+89], ends at k+89
        x = np.arange(REG_WIN, dtype=float)
        xc = x - x.mean()
        yc = win - win.mean(axis=1, keepdims=True)
        sxx = (xc ** 2).sum()
        sxy = (yc * xc).sum(axis=1)
        syy = (yc ** 2).sum(axis=1)
        b = sxy / sxx
        with np.errstate(invalid="ignore", divide="ignore"):
            r2 = np.where(syy > 0, sxy ** 2 / (sxx * syy), 0.0)   # constant window: R^2 := 0 (slope is 0 anyway)
        score[REG_WIN - 1:] = (np.exp(b * ANN) - 1.0) * r2
    s = pd.Series(close, index=df.index)
    sma = s.rolling(SMA_WIN, min_periods=SMA_WIN).mean()
    absret = (s / s.shift(1) - 1.0).abs()
    gapmax = absret.rolling(GAP_WIN, min_periods=GAP_WIN).max()
    out = pd.DataFrame({
        "score": score,
        "trend_ok": (s > sma).to_numpy(),
        "gap_bad": (gapmax > GAP_MAX).to_numpy(),
        "nrows": np.arange(1, n + 1),
    }, index=df.index)
    return out


def strategy(data):
    spy = data["SPY"]
    cal = spy.index
    spy_close = spy["Close"]
    spy_sma = spy_close.rolling(SPY_SMA, min_periods=SPY_SMA).mean()

    tickers = sorted(s for s in data if s not in EXCLUDE)
    ind = {s: _indicators(data[s]) for s in tickers}

    # Aligned (calendar x ticker) matrices; NaN / False where the stock has no row.
    score_m = pd.DataFrame({s: ind[s]["score"] for s in tickers}).reindex(cal)
    trend_m = pd.DataFrame({s: ind[s]["trend_ok"] for s in tickers}).reindex(cal).fillna(False).astype(bool)
    gap_m = pd.DataFrame({s: ind[s]["gap_bad"] for s in tickers}).reindex(cal).fillna(False).astype(bool)
    nrows_m = pd.DataFrame({s: ind[s]["nrows"] for s in tickers}).reindex(cal)
    has_row = pd.DataFrame({s: pd.Series(True, index=data[s].index) for s in tickers}).reindex(cal).fillna(False).astype(bool)

    opens = {s: data[s]["Open"] for s in tickers}
    closes = {s: data[s]["Close"] for s in tickers}
    idx = {s: data[s].index for s in tickers}

    positions = []   # dicts: ticker, entry_date, entry_px, rank, exit_date, exit_px

    for pos_w in range(1, len(cal)):
        W = cal[pos_w]
        if W.weekday() != 2:
            continue
        pos_d = pos_w - 1
        D = cal[pos_d]
        if pos_d < SPY_SMA - 1:          # no trading before SPY has 200 rows through D
            continue

        # ---- cross-section at D ----
        valid = has_row.iloc[pos_d] & (nrows_m.iloc[pos_d] >= MIN_ROWS)
        vt = [s for s in tickers if valid[s]]
        N = len(vt)
        sc = score_m.iloc[pos_d]
        ranked = sorted(vt, key=lambda s: (-sc[s], s))           # score desc, ticker asc
        rank_of = {s: i + 1 for i, s in enumerate(ranked)}
        cutoff = -(-N // 5)                                     # ceil(0.20 * N)
        trend_d = trend_m.iloc[pos_d]
        gap_d = gap_m.iloc[pos_d]
        regime_on = bool(spy_close.iloc[pos_d] > spy_sma.iloc[pos_d])

        # ---- SELL (always runs first) ----
        for p in positions:
            if p["exit_date"] is not None:
                continue
            s = p["ticker"]
            if not has_row.iat[pos_d, tickers.index(s)]:
                continue                                          # no row on D: keep, re-check next week
            top20 = rank_of.get(s, 10 ** 9) <= cutoff
            if (not top20) or (not trend_d[s]) or gap_d[s]:
                later = idx[s][idx[s] >= W]
                if len(later):
                    ed = later[0]
                    p["exit_date"], p["exit_px"] = ed, float(opens[s].loc[ed])
                else:                                             # no later row: END rule (Close of last row)
                    ed = idx[s][-1]
                    p["exit_date"], p["exit_px"] = ed, float(closes[s].loc[ed])

        # ---- BUY (only if regime_on) ----
        if not regime_on:
            continue
        held = {p["ticker"] for p in positions if p["exit_date"] is None or p["exit_date"] > W}
        free = SLOTS - len(held)
        if free <= 0:
            continue
        for s in ranked[:cutoff]:
            if free <= 0:
                break
            if s in held or (not trend_d[s]) or gap_d[s]:
                continue
            if W not in opens[s].index:
                continue
            positions.append({"ticker": s, "entry_date": W, "entry_px": float(opens[s].loc[W]),
                              "rank": rank_of[s], "exit_date": None, "exit_px": None})
            held.add(s)
            free -= 1

    # ---- END: close open positions at the Close of their last row ----
    for p in positions:
        if p["exit_date"] is None:
            s = p["ticker"]
            ed = idx[s][-1]
            p["exit_date"], p["exit_px"] = ed, float(closes[s].loc[ed])

    return [{"ticker": p["ticker"], "entry_date": p["entry_date"], "entry_px": p["entry_px"],
             "exit_date": p["exit_date"], "exit_px": p["exit_px"], "rank": p["rank"]} for p in positions]
