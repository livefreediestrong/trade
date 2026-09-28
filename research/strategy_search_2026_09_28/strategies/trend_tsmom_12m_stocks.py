"""trend_tsmom_12m_stocks -- time-series momentum on individual stocks (MOP 2012, k=12, h=1).

At each stock's own month-end row t (a row whose NEXT row is in a later calendar month;
the stock's last row is never a month-end), compute
    r12 = Close[month-end of month m] / Close[month-end of month m-12] - 1
and the ex-ante volatility
    sigma_t = sqrt(261) * Close.pct_change().ewm(com=60, min_periods=60).std()  at row t.
If r12 > 0 and sigma_t is not NaN: buy at the Open of row t+1, sell at the Open of the row
after the stock's NEXT month-end row (or at the Close of the stock's last row if there is
no further month-end). rank = sigma_t (lowest volatility gets slot priority).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EXCLUDE = ("SPY", "QQQ")
LOOKBACK_MONTHS = 12
EWM_COM = 60
EWM_MIN_PERIODS = 60
ANNUALIZE = 261


def _stock_trades(ticker: str, df: pd.DataFrame) -> list[dict]:
    n = len(df)
    if n < 3:
        return []
    idx = df.index
    opens = df["Open"].to_numpy(dtype=float)
    closes = df["Close"].to_numpy(dtype=float)

    # calendar-month id of each row (uses only the DATE of the next row, never its prices)
    month_id = (idx.year * 12 + idx.month - 1).to_numpy()
    is_me = np.zeros(n, dtype=bool)
    is_me[:-1] = month_id[1:] > month_id[:-1]      # last row is never a month-end
    me_rows = np.flatnonzero(is_me)
    if len(me_rows) == 0:
        return []

    # month-end close by calendar month
    me_close = {int(month_id[r]): closes[r] for r in me_rows}

    # causal EWMA volatility ending at each row (pandas defaults: adjust=True, bias=False)
    sigma = (math.sqrt(ANNUALIZE)
             * df["Close"].pct_change().ewm(com=EWM_COM, min_periods=EWM_MIN_PERIODS).std()).to_numpy()

    trades = []
    for j, t in enumerate(me_rows):
        m = int(month_id[t])
        p_prev = me_close.get(m - LOOKBACK_MONTHS)
        if p_prev is None:
            continue
        s = sigma[t]
        if not np.isfinite(s):
            continue
        r12 = closes[t] / p_prev - 1.0
        if not (r12 > 0):
            continue
        e = t + 1                                   # first row of the next month
        if j + 1 < len(me_rows):
            x = me_rows[j + 1] + 1                  # row after the NEXT month-end
            exit_date, exit_px = idx[x], float(opens[x])
        else:                                       # no further month-end: close at last Close
            exit_date, exit_px = idx[n - 1], float(closes[n - 1])
        trades.append({
            "ticker": ticker,
            "entry_date": idx[e],
            "entry_px": float(opens[e]),
            "exit_date": exit_date,
            "exit_px": exit_px,
            "rank": float(s),
        })
    return trades


def strategy(data):
    trades = []
    for ticker in sorted(data):
        if ticker in EXCLUDE:
            continue
        trades.extend(_stock_trades(ticker, data[ticker]))
    return trades
