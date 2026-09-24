"""
Lean volume-screener analysis for the day-trade signal desk.

Ported helpers from volume-screener app.py — no news/insider/sentiment/tier
composite. Prefer yfinance, then data_sources.get_daily_with_fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import data_sources as ds
from market_radar import RadarTuning

SECTOR_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Industrials": "XLI",
    "Energy": "XLE",
    "Basic Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


def compute_rsi(close, period=14):
    """Wilder's RSI on a daily close series. Returns the latest value or None."""
    if close is None or len(close) < period + 1:
        return None
    delta = close.diff().dropna()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return None if pd.isna(val) else float(val)


def entry_quality(close, current_price=None):
    """How 'late' is right-now as an entry?"""
    if close is None or len(close) < 21:
        return None
    last = float(current_price) if current_price else float(close.iloc[-1])

    sma20 = float(close.rolling(20).mean().iloc[-1])
    extension_pct = (last / sma20 - 1) * 100 if sma20 > 0 else 0.0

    recent = close.iloc[-30:] if len(close) >= 30 else close
    hi = float(recent.max())
    lo = float(recent.min())
    pos_in_30d = ((last - lo) / (hi - lo) * 100) if hi > lo else 50.0

    ret_5d = (last / float(close.iloc[-6]) - 1) * 100 if len(close) >= 6 else 0.0
    rsi_14 = compute_rsi(close, 14)

    pts_extension = min(max(extension_pct, 0) / 12.0, 1.0) * 30
    pts_high = min(max(pos_in_30d - 50, 0) / 50.0, 1.0) * 25
    pts_run5d = min(max(ret_5d, 0) / 20.0, 1.0) * 25
    pts_rsi = 0
    if rsi_14 is not None:
        if rsi_14 >= 80:
            pts_rsi = 20
        elif rsi_14 >= 70:
            pts_rsi = 12
        elif rsi_14 >= 60:
            pts_rsi = 5
    lateness = round(min(pts_extension + pts_high + pts_run5d + pts_rsi, 100), 1)

    if lateness >= 75:
        label = "chasing"
        suggestion = (
            "Price is extended relative to this history. Check a fresh quote, "
            "liquidity and your risk limits before evaluating an entry."
        )
    elif lateness >= 50:
        label = "late"
        suggestion = (
            "Price has already moved substantially. This historical measure "
            "does not establish whether the move will continue."
        )
    elif lateness >= 25:
        label = "fair"
        suggestion = (
            "Moderate historical extension. Entry quality still depends on "
            "current data, costs and risk checks."
        )
    else:
        label = "early"
        suggestion = (
            "Low historical extension; this does not establish upside or authorize position size."
        )

    warnings = []
    if extension_pct >= 12:
        warnings.append(
            f"Price is {extension_pct:.1f}% above the 20-day SMA — overextended"
        )
    if pos_in_30d >= 95:
        warnings.append(
            f"Trading at {pos_in_30d:.0f}% of the 30-day high-low range — "
            "at or near the top"
        )
    if ret_5d >= 20:
        warnings.append(
            f"Up {ret_5d:.1f}% in 5 days — parabolic, prone to mean reversion"
        )
    if rsi_14 is not None and rsi_14 >= 80:
        warnings.append(f"RSI(14) is {rsi_14:.0f} — extreme overbought territory")
    elif rsi_14 is not None and rsi_14 >= 70:
        warnings.append(f"RSI(14) is {rsi_14:.0f} — overbought")

    return {
        "lateness": lateness,
        "label": label,
        "extension_pct": round(extension_pct, 2),
        "pos_in_30d": round(pos_in_30d, 1),
        "ret_5d": round(ret_5d, 2),
        "rsi_14": round(rsi_14, 1) if rsi_14 is not None else None,
        "warnings": warnings,
        "suggestion": suggestion,
    }


def chaikin_money_flow(df, period=20):
    if df is None or len(df) < period:
        return None
    high, low, close, vol = df["High"], df["Low"], df["Close"], df["Volume"]
    rng = (high - low).replace(0, np.nan)
    mfm = ((close - low) - (high - close)) / rng
    mfv = (mfm * vol).fillna(0)
    denom = vol.rolling(period).sum().replace(0, np.nan)
    cmf = mfv.rolling(period).sum() / denom
    val = cmf.iloc[-1]
    return None if pd.isna(val) else float(val)


def first_hour_volumes(intraday):
    """Return {date_str: first_bar_volume} grouped by trading day."""
    out = {}
    if intraday is None or intraday.empty or not isinstance(intraday.index, pd.DatetimeIndex):
        return out
    for date, group in intraday.groupby(intraday.index.strftime("%Y-%m-%d")):
        if not group.empty:
            out[date] = int(group["Volume"].iloc[0])
    return out


def _fetch_earnings(ticker: str, yf_ticker, info: dict | None = None):
    # Funds and indices have no company earnings calendar. Reuse metadata already
    # fetched by the scan; don't make another failing quoteSummary request.
    fund_symbols = {"SPY", "QQQ", "QQQM", "DIA", "IWM", "VTI", "VOO", *SECTOR_ETF.values()}
    quote_type = str((info or {}).get("quoteType") or "").upper()
    if ticker.upper() in fund_symbols or quote_type in ("ETF", "MUTUALFUND", "INDEX", "CRYPTOCURRENCY", "CURRENCY"):
        return None, None
    earnings = ds.finnhub_earnings(ticker)
    if earnings:
        return earnings, "Finnhub"
    earn = ds.yahoo_next_earnings(ticker)
    if earn:
        return earn, "Yahoo earnings"
    try:
        cal = yf_ticker.calendar
        next_dt = None
        if isinstance(cal, dict):
            v = cal.get("Earnings Date")
            if isinstance(v, list) and v:
                next_dt = v[0]
            elif v:
                next_dt = v
        elif cal is not None and hasattr(cal, "empty") and not cal.empty:
            try:
                next_dt = cal.iloc[0].get("Earnings Date")
            except Exception:
                next_dt = None
        if next_dt is None:
            try:
                future = yf_ticker.get_earnings_dates(limit=4)
                if future is not None and not future.empty:
                    today_ts = pd.Timestamp.now(tz=future.index.tz)
                    upcoming = future.index[future.index >= today_ts]
                    if len(upcoming):
                        next_dt = upcoming.min()
            except Exception:
                pass
        if next_dt is not None:
            next_ts = pd.Timestamp(next_dt)
            next_d = (
                next_ts.tz_localize(None).date()
                if next_ts.tz is not None
                else next_ts.date()
            )
            days_away = (next_d - datetime.utcnow().date()).days
            return {
                "date": next_d.strftime("%Y-%m-%d"),
                "days_away": int(days_away),
                "is_soon": 0 <= days_away <= 7,
            }, "yfinance calendar"
    except Exception:
        pass
    return None, None


def _sector_daily(etf: str):
    try:
        import yfinance as yf

        s = yf.Ticker(etf).history(period="3mo", interval="1d")
        if s is not None and not s.empty and len(s) >= 2:
            return s
    except Exception:
        pass
    df, _src = ds.get_daily_with_fallback(etf, days=120)
    return df


def _rth_now() -> bool:
    try:
        import paper_loop as _pl

        return bool(_pl.is_rth())
    except Exception:
        return False


def atr(daily: "pd.DataFrame", period: int = 14) -> Optional[float]:
    """Average True Range — a stock's typical daily move in dollars."""
    try:
        h, l, c = daily["High"], daily["Low"], daily["Close"]
        prev = c.shift(1)
        tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        v = float(tr.rolling(period).mean().iloc[-1])
        return v if np.isfinite(v) and v > 0 else None
    except Exception:
        return None


def intraday_signals(
    bars5: "pd.DataFrame",
    *,
    atr_usd: Optional[float],
    price: Optional[float] = None,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Short-term signals from 5-minute bars (today + prior days).

    - rel_vol_5m: last completed 5-min bar vs. the median of the SAME time slot
      on prior days (fair at any time of day, including 9:30-10:30).
    - vwap / vwap_slope_pct: today's volume-weighted average price and how it
      moved over the last 30 minutes.
    - vwap_dist_atr / hod_dist_atr: distance from VWAP / from the day's high,
      measured in typical daily moves (ATR).
    - spread_atr: bid-ask spread as a fraction of ATR (too wide = costly to trade).
    """
    # A current last price cannot establish that a separate history feed is current.
    out: dict[str, Any] = {
        "source": "Yahoo 5m", "available": False, "fresh": False,
        "as_of": None, "age_sec": None, "max_age_sec": 600,
        "unavailable_reason": "missing_bars",
    }
    if bars5 is None or len(bars5) == 0:
        return out
    try:
        import paper_loop as _pl

        stamp = pd.Timestamp(now or datetime.now(ZoneInfo("America/New_York")))
        if stamp.tzinfo is None:
            out["unavailable_reason"] = "missing_clock_timezone"
            return out
        stamp = stamp.tz_convert("America/New_York")
        df = bars5.dropna(subset=["High", "Low", "Close", "Volume"]).sort_index().copy()
        if df.empty:
            return out
        if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is None:
            out["unavailable_reason"] = "missing_bar_timezone"
            return out
        df.index = df.index.tz_convert("America/New_York")
        df = df[~df.index.duplicated(keep="last")]
        latest = df.index[-1]
        age = (stamp - latest).total_seconds()
        out.update(as_of=latest.isoformat(), age_sec=round(age, 1),
                   as_of_session=latest.strftime("%Y-%m-%d"))
        if latest.date() != stamp.date():
            out["unavailable_reason"] = "stale_session"
            return out
        if age < -15 or age > out["max_age_sec"]:
            out["unavailable_reason"] = "future_bars" if age < -15 else "stale_bars"
            return out
        if not _pl.is_rth(stamp.to_pydatetime()):
            out["unavailable_reason"] = "market_closed"
            return out
        # Exclude any extended-hours observations before calculating session VWAP.
        minutes = df.index.hour * 60 + df.index.minute
        df = df[(minutes >= 570) & (minutes < 960)]
        df = df[[ts <= stamp and _pl.is_rth(ts.to_pydatetime()) for ts in df.index]]
        numeric = df[["High", "Low", "Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
        valid = np.isfinite(numeric).all(axis=1) & (numeric["Low"] > 0) & (numeric["Volume"] >= 0)
        valid &= (numeric["High"] >= numeric["Close"]) & (numeric["Low"] <= numeric["Close"])
        if not valid.all():
            out["unavailable_reason"] = "erroneous_bars"
            return out
        idx = df.index
        df["day"] = [ts.strftime("%Y-%m-%d") for ts in idx]
        df["slot"] = [ts.strftime("%H:%M") for ts in idx]
        today = stamp.strftime("%Y-%m-%d")
        tdf = df[df["day"] == today]
        if tdf.empty:
            out["unavailable_reason"] = "missing_session_bars"
            return out
        out.update(available=True, fresh=True, unavailable_reason=None)
        completed = tdf[tdf.index + pd.Timedelta(minutes=5) <= stamp]
        if not completed.empty:
            last = completed.iloc[-1]
            out["completed_bar_at"] = completed.index[-1].isoformat()
            if len(completed) >= 4:
                returns = np.log(completed["Close"].astype(float)).diff().dropna().tail(20)
                out["realized_volatility_pct"] = round(float(np.sqrt((returns ** 2).sum()) * 100), 6)
                out["realized_volatility_bars"] = len(returns)
                out["session_dollar_volume"] = round(float((completed["Close"] * completed["Volume"]).sum()), 2)
            prior = df[(df["day"] != today) & (df["slot"] == last["slot"])]["Volume"]
            if len(prior) >= 2 and float(prior.median()) > 0:
                out["rel_vol_5m"] = round(float(last["Volume"]) / float(prior.median()), 2)
        if len(tdf) >= 1:
            typical = (tdf["High"] + tdf["Low"] + tdf["Close"]) / 3
            cumv = tdf["Volume"].cumsum()
            vwap_series = (typical * tdf["Volume"]).cumsum() / cumv.replace(0, np.nan)
            vwap = float(vwap_series.iloc[-1])
            if np.isfinite(vwap):
                out["vwap"] = round(vwap, 4)
                if len(vwap_series) > 6 and np.isfinite(vwap_series.iloc[-7]):
                    out["vwap_slope_pct"] = round((vwap / float(vwap_series.iloc[-7]) - 1) * 100, 3)
                px = float(price) if price else float(tdf["Close"].iloc[-1])
                hod = float(tdf["High"].max())
                if atr_usd:
                    out["vwap_dist_atr"] = round((px - vwap) / atr_usd, 2)
                    out["hod_dist_atr"] = round((hod - px) / atr_usd, 2)
        # Quotes outside market hours are stale; a "spread" over 2% of price is bad data.
        if atr_usd and bid and ask and ask > bid > 0 and (ask - bid) / ask < 0.02:
            out["spread_atr"] = round((ask - bid) / atr_usd, 3)
    except Exception as exc:  # noqa: BLE001
        out.update(available=False, fresh=False, unavailable_reason="invalid_bars")
        for key in ("rel_vol_5m", "vwap", "vwap_slope_pct", "vwap_dist_atr", "hod_dist_atr", "spread_atr"):
            out.pop(key, None)
        out["error"] = str(exc)[:120]
    return out


def intraday_adjust(verdict: str, sig: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Downgrade (never upgrade) a verdict using the intraday signals.

    Returns (verdict, research_flags, plain_notes)."""
    flags: list[str] = []
    notes: list[str] = []
    v = verdict
    if not sig.get("fresh"):
        flags.append("intraday_stale" if sig.get("as_of") else "intraday_unavailable")
        reason = str(sig.get("unavailable_reason") or "missing_bars").replace("_", " ")
        notes.append(f"Current five-minute indicators unavailable ({reason}); wait for fresh session bars.")
        return ("WATCH" if v == "PASS" else v), flags, notes
    spread = sig.get("spread_atr")
    if spread is not None and spread > 0.08:
        flags.append("wide_spread")
        notes.append("The gap between buy and sell prices is wide for this stock — costly to trade.")
        if v == "PASS":
            v = "WATCH"
    dist = sig.get("vwap_dist_atr")
    slope = sig.get("vwap_slope_pct")
    if dist is not None and slope is not None and dist < 0 and slope < 0:
        flags.append("below_falling_vwap")
        notes.append("Price is below the day's average trade price, and that average is falling.")
        if v == "PASS":
            v = "WATCH"
    if dist is not None and dist > 1.5:
        flags.append("stretched_above_vwap")
        notes.append("Price is far above the day's average — buying now risks chasing.")
        if v == "PASS":
            v = "WATCH"
    rv5 = sig.get("rel_vol_5m")
    if rv5 is not None and rv5 >= 3:
        flags.append("volume_surge_5m")
        notes.append(f"Trading in the last 5 minutes was {rv5:.1f}× normal for this time of day.")
    return v, flags, notes


def _validated_daily(daily, now):
    """Daily indices label exchange sessions, independently of a quote's age."""
    import paper_loop
    if daily is None or not isinstance(daily.index, pd.DatetimeIndex) or daily.index.hasnans:
        raise ValueError("Daily history has no verifiable session dates")
    frame = daily.copy()
    dates = frame.index.tz_convert(paper_loop.NY_TZ).date if frame.index.tz is not None else frame.index.date
    if any(day > now.date() for day in dates):
        raise ValueError("Daily history contains a future session")
    frame.index = pd.DatetimeIndex(dates)
    frame = frame.sort_index(kind="stable").loc[lambda rows: ~rows.index.duplicated(keep="last")]
    frame = frame.loc[[paper_loop.session_close_time(day) is not None for day in frame.index.date]]
    frame = frame.dropna(subset=["Close"])
    if len(frame) < 2 or not np.isfinite(frame["Close"]).all() or (frame["Close"] <= 0).any():
        raise ValueError("Not enough valid daily price history")
    expected = now.date()
    close = paper_loop.session_close_time(expected)
    if close is None or now.time().replace(tzinfo=None) < close:
        expected -= timedelta(days=1)
    while paper_loop.session_close_time(expected) is None:
        expected -= timedelta(days=1)
    completed = frame.loc[frame.index.date <= expected]
    if completed.empty or completed.index[-1].date() != expected:
        raise ValueError("Daily history is behind the latest completed session")
    if frame.index[-1].date() > expected and now.time().replace(tzinfo=None) < paper_loop.RTH_OPEN:
        raise ValueError("Daily history contains a session that has not opened")
    return frame, {"as_of_session": frame.index[-1].date().isoformat(),
                   "completed_session": expected.isoformat(),
                   "forming": frame.index[-1].date() > expected, "fresh": True}


def analyze_ticker(ticker: str) -> dict[str, Any]:
    """Lean screener analysis for one symbol. Never raises."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return {"ticker": "", "error": "Empty ticker"}

    sources_used: list[str] = []
    try:
        import yfinance as yf
    except Exception as exc:
        return {"ticker": ticker, "error": f"yfinance unavailable: {exc}"}

    t = yf.Ticker(ticker)
    daily = None
    try:
        daily = t.history(period="3mo", interval="1d")
    except Exception:
        daily = None
    if daily is not None and not daily.empty:
        sources_used.append("Yahoo (yfinance)")
    else:
        df, src = ds.get_daily_with_fallback(ticker, days=120)
        if df is None:
            return {
                "ticker": ticker,
                "error": f"No data for '{ticker}' from Yahoo or NASDAQ.",
            }
        daily = df
        sources_used.append(f"{src} (fallback)")

    try:
        daily, daily_status = _validated_daily(daily, datetime.now(ZoneInfo("America/New_York")))
    except (ValueError, TypeError, KeyError) as exc:
        return {"ticker": ticker, "error": str(exc), "daily_history": {"fresh": False},
                "research_flags": ["daily_history_unavailable"]}
    last_close = float(daily["Close"].iloc[-1])
    if not np.isfinite(last_close) or last_close <= 0:
        return {"ticker": ticker, "error": f"No valid last price for '{ticker}'."}
    # All price-dependent checks must use the same provider observation.
    # Updating the price after selecting a verdict can turn an SMA failure into PASS.
    try:
        price_quote = ds.latest_quote(ticker)
    except Exception:
        price_quote = ds.quote_snapshot(None, None, "unavailable")
    if price_quote.get("price"):
        sources_used.append(price_quote["source"])
        last_close = price_quote["price"]
    price_day = daily.index[-1].date()
    if price_quote.get("price") and price_quote.get("market_time"):
        try:
            stamp = pd.Timestamp(price_quote["market_time"])
            if stamp.tzinfo is not None:
                price_day = stamp.tz_convert("America/New_York").date()
        except (ValueError, TypeError):
            pass
    previous = daily.loc[daily.index.date < price_day, "Close"]
    if previous.empty:
        return {"ticker": ticker, "error": "Previous completed close unavailable"}
    prev_close = float(previous.iloc[-1])
    change_dollar = last_close - prev_close
    change_pct = (change_dollar / prev_close) * 100 if prev_close else 0.0

    sma10 = float(daily["Close"].rolling(10).mean().iloc[-1])
    sma20 = float(daily["Close"].rolling(20).mean().iloc[-1])
    sma50 = float(daily["Close"].rolling(50).mean().iloc[-1])
    above_10 = last_close > sma10
    above_20 = last_close > sma20
    above_50 = last_close > sma50
    sma_aligned = above_10 and above_20 and above_50

    avg_vol_30d = float(daily["Volume"].iloc[-30:].mean())

    intraday = None
    try:
        intraday = t.history(period="10d", interval="60m")
    except Exception:
        intraday = None
    intraday_available = intraday is not None and not intraday.empty
    if not intraday_available:
        intraday = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    fh_by_day = first_hour_volumes(intraday)
    sorted_days = sorted(fh_by_day.keys())

    if sorted_days:
        latest_day = sorted_days[-1]
        first_hour_today = fh_by_day[latest_day]
        prev_days = sorted_days[:-1][-5:]
        prev_first_hour_avg = (
            float(np.mean([fh_by_day[d] for d in prev_days])) if prev_days else 0.0
        )
    else:
        latest_day = None
        first_hour_today = 0
        prev_first_hour_avg = 0.0

    try:
        _now_et = datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        _now_et = None
    # During 9:30–10:30 today's first-hour bar is still filling. Compare like with
    # like by projecting it to a full hour (otherwise nearly everything reads AVOID).
    first_hour_partial = False
    if (
        _now_et is not None
        and latest_day == _now_et.strftime("%Y-%m-%d")
        and first_hour_today
    ):
        mins = (_now_et.hour * 60 + _now_et.minute) - (9 * 60 + 30)
        if 0 <= mins < 60:
            first_hour_partial = True
            first_hour_today = first_hour_today * (60.0 / max(mins, 5))
    rel_vol = first_hour_today / prev_first_hour_avg if prev_first_hour_avg else 0.0
    # Soften volume starvation for liquid names:
    # threshold 1.5 when price already above key SMAs, else keep 2.0.
    # (Mega-caps / liquid list: prefer 1.5x OR cumulative RTH rel vol when easy.)
    vol_threshold = 1.5 if sma_aligned else 2.0
    volume_strong = rel_vol >= vol_threshold

    try:
        market_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    except Exception:
        market_today = datetime.utcnow().strftime("%Y-%m-%d")
    session_is_today = latest_day == market_today

    info: dict = {}
    try:
        info = t.info or {}
    except Exception:
        info = {}
    sector = info.get("sector") or "Unknown"

    sector_etf = SECTOR_ETF.get(sector)
    sector_data = None
    sector_error = None
    if sector_etf:
        s = _sector_daily(sector_etf)
        try:
            s, sector_status = _validated_daily(s, datetime.now(ZoneInfo("America/New_York")))
        except (ValueError, TypeError, KeyError) as exc:
            s, sector_error = None, str(exc)
        if s is not None and not s.empty and len(s) >= 2:
            today_pct = (s["Close"].iloc[-1] / s["Close"].iloc[-2] - 1) * 100
            five_d = (
                (s["Close"].iloc[-1] / s["Close"].iloc[-6] - 1) * 100
                if len(s) >= 6
                else 0.0
            )
            twenty_d = (
                (s["Close"].iloc[-1] / s["Close"].iloc[-21] - 1) * 100
                if len(s) >= 21
                else 0.0
            )
            cmf = chaikin_money_flow(s, 20)
            green_days_5 = 0
            if len(s) >= 6:
                green_days_5 = int((s["Close"].iloc[-6:].pct_change().dropna() > 0).sum())
            sector_data = {
                **sector_status,
                "etf": sector_etf,
                "today_pct": round(float(today_pct), 2),
                "five_d_pct": round(float(five_d), 2),
                "twenty_d_pct": round(float(twenty_d), 2),
                "cmf": round(cmf, 4) if cmf is not None else None,
                "green_days_5": green_days_5,
            }

    sector_ok = (
        sector_data is not None
        and sector_data["today_pct"] > 0
        and sector_data.get("green_days_5", 0) >= 3
        and (sector_data["cmf"] or 0) > 0
    )
    sector_dying = (
        sector_data is not None
        and sector_data["today_pct"] < 0
        and sector_data.get("green_days_5", 5) <= 1
        and (sector_data["cmf"] or 0) < 0
    )

    # Halt / gap hard gate: large overnight gap or detectable halt → force AVOID.
    gap_pct = float(change_pct) if change_pct is not None else 0.0
    halt_or_gap = False
    research_flags: list[str] = []
    if sector_error:
        research_flags.append("sector_history_unavailable")
    if not price_quote.get("fresh"):
        research_flags.append("stale_or_unverified_quote")
    if change_pct <= RadarTuning.DISTRIBUTION_PCT and rel_vol >= RadarTuning.DISTRIBUTION_REL_VOLUME:
        research_flags.append("distribution_day")
    if (
        change_pct >= RadarTuning.PARABOLIC_DAY_PCT
        or (len(daily) >= 6 and (last_close / float(daily["Close"].iloc[-6]) - 1) * 100 >= 80)
        or (len(daily) >= 21 and (last_close / float(daily["Close"].iloc[-21]) - 1) * 100 >= 150)
    ):
        research_flags.append("parabolic_move")
    market_state = str(info.get("marketState") or "")
    if "HALT" in market_state.upper():
        halt_or_gap = True
        research_flags.append("halt_detected")
    # yfinance never reports HALT in marketState. Practical proxy: during regular
    # hours, a last trade more than 10 minutes old on a stock we're scanning.
    try:
        import paper_loop as _pl

        rmt = info.get("regularMarketTime")
        if rmt and _pl.is_rth() and market_state.upper() in ("REGULAR", ""):
            age_min = (datetime.now().timestamp() - float(rmt)) / 60.0
            if age_min > 10:
                halt_or_gap = True
                research_flags.append("halt_detected")
                market_state = f"no trades for {int(age_min)} min"
    except Exception:
        pass
    if abs(gap_pct) > 8.0:
        halt_or_gap = True
        research_flags.append("gap_gt_8pct")

    if halt_or_gap:
        verdict = "AVOID"
        if "halt_detected" in research_flags:
            verdict_text = (
                f"Hard AVOID: market state looks halted ({market_state or 'HALT'}). "
                "Research only — do not chase."
            )
        else:
            verdict_text = (
                f"Hard AVOID: gap {gap_pct:+.1f}% vs prior close exceeds 8%. "
                "Gap/halts need research — skip autopilot."
            )
    elif volume_strong and sma_aligned and sector_ok and not session_is_today:
        # The volume spike is from an earlier session (pre-market scan, stale feed).
        verdict = "WATCH"
        verdict_text = (
            f"Setup looked clean, but the volume spike is from {latest_day}, not today. "
            "Wait for today's first hour."
        )
    elif volume_strong and sma_aligned and sector_ok:
        verdict = "PASS"
        verdict_text = (
            "Volume spike + price above all SMAs + sector flowing in. "
            "Clean setup by the playbook."
        )
    elif volume_strong and sector_dying:
        verdict = "AVOID"
        verdict_text = (
            "Volume is hot but the sector is rolling over. "
            "Don't fight a dying sector."
        )
    elif not volume_strong:
        # Missing/stale intraday → WATCH not AVOID (don't starve solely for weak volume data).
        # If session_is_today is False, don't mark AVOID solely for weak/missing intraday volume.
        if (not intraday_available) or (not session_is_today):
            verdict = "WATCH"
            if not intraday_available:
                verdict_text = (
                    "Intraday volume data unavailable (Yahoo may be rate-limiting). "
                    "Treating as WATCH — re-run shortly."
                )
            else:
                verdict_text = (
                    f"Intraday session not today (latest={latest_day}). "
                    f"Rel vol {rel_vol:.2f}x — WATCH, not AVOID."
                )
        else:
            verdict = "AVOID"
            verdict_text = (
                f"Relative volume only {rel_vol:.2f}x (need ≥{vol_threshold:.1f}x). "
                "No unusual interest yet — skip."
            )
    elif volume_strong and sma_aligned:
        verdict = "WATCH"
        verdict_text = (
            "Volume + structure look good but sector confirmation is weak. "
            "Size down or wait."
        )
    else:
        verdict = "WATCH"
        verdict_text = "Mixed signals. Not a clean go."

    if research_flags and verdict == "PASS":
        verdict = "WATCH"
        verdict_text = (
            f"{verdict_text} Research warning: {', '.join(research_flags)}. "
            "Do not chase; require a fresh setup."
        )

    daily_close = daily["Close"].dropna() if "Close" in daily.columns else None
    live_price = price_quote.get("price") if price_quote.get("fresh") else None
    entry = (
        entry_quality(daily_close, current_price=live_price)
        if daily_close is not None
        else None
    )

    # Short-term (5-minute) signals: time-of-day relative volume, VWAP, ATR distances.
    try:
        bars5 = t.history(period="5d", interval="5m")
    except Exception:
        bars5 = None
    atr_usd = atr(daily)
    intraday_sig = intraday_signals(
        bars5,
        atr_usd=atr_usd,
        price=live_price or last_close,
        bid=info.get("bid") if _rth_now() else None,
        ask=info.get("ask") if _rth_now() else None,
    )
    if atr_usd:
        intraday_sig["atr_usd"] = round(atr_usd, 4)
    new_v, iflags, inotes = intraday_adjust(verdict, intraday_sig)
    if new_v != verdict:
        verdict_text = f"{verdict_text} Downgraded to WATCH: {' '.join(inotes)}"
        verdict = new_v
    elif not intraday_sig.get("fresh"):
        verdict_text = f"{verdict_text} {' '.join(inotes)}"
    for f in iflags:
        if f not in research_flags:
            research_flags.append(f)
    intraday_sig["notes"] = inotes

    earnings, earn_src = _fetch_earnings(ticker, t, info)
    if earn_src and earn_src not in sources_used:
        sources_used.append(earn_src)

    sources_used = list(dict.fromkeys(sources_used))

    return {
        "ticker": ticker,
        "price": round(last_close, 2),
        "quote": price_quote,
        "as_of": price_quote.get("market_time"),
        "daily_history": daily_status,
        "sector_history_error": sector_error,
        "change_dollar": round(change_dollar, 2),
        "change_pct": round(change_pct, 2),
        "sources": sources_used,
        "earnings": earnings,
        "sma": {
            "sma10": round(sma10, 2),
            "sma20": round(sma20, 2),
            "sma50": round(sma50, 2),
            "above_10": above_10,
            "above_20": above_20,
            "above_50": above_50,
        },
        "volume": {
            "avg_30d": int(avg_vol_30d) if avg_vol_30d == avg_vol_30d else 0,
            "first_hour_today": int(first_hour_today),
            "prev_first_hour_avg": int(prev_first_hour_avg),
            "rel_vol": round(rel_vol, 2),
            "first_hour_partial": first_hour_partial,
            "as_of_session": latest_day,
            "intraday_available": intraday_available,
            "session_is_today": session_is_today,
        },
        "sector_name": sector,
        "sector": sector_data,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "entry_quality": entry,
        "checks": {
            "volume_2x": volume_strong,
            "above_sma_10_20_50": sma_aligned,
            "sector_participating": sector_ok,
            "sector_dying": sector_dying,
            "halt_or_gap": halt_or_gap,
        },
        "gap_pct": round(gap_pct, 2),
        "prev_close": round(float(prev_close), 2),
        "research_flags": research_flags,
        "intraday": intraday_sig,
        "research_flag": research_flags[0] if research_flags else None,
    }
