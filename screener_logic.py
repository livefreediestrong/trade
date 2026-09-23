"""
Lean volume-screener analysis for the day-trade signal desk.

Ported helpers from volume-screener app.py — no news/insider/sentiment/tier
composite. Prefer yfinance, then data_sources.get_daily_with_fallback.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import data_sources as ds

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
            "Right at the top. Wait for a real pullback (5%+) or take a "
            "partial entry only — full size here invites whipsaw."
        )
    elif lateness >= 50:
        label = "late"
        suggestion = (
            "The easy money is made. Either size down significantly or "
            "wait for a flag/consolidation."
        )
    elif lateness >= 25:
        label = "fair"
        suggestion = (
            "Reasonable entry — not bargain-hunting territory but not "
            "extended either."
        )
    else:
        label = "early"
        suggestion = (
            "Plenty of room. If the setup checks out, fine to enter at full size."
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


def _fetch_earnings(ticker: str, yf_ticker):
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
    out: dict[str, Any] = {}
    if bars5 is None or len(bars5) == 0:
        return out
    try:
        df = bars5.dropna(subset=["Close", "Volume"]).copy()
        idx = df.index
        try:
            idx = idx.tz_convert("America/New_York")
        except Exception:
            pass
        df["day"] = [ts.strftime("%Y-%m-%d") for ts in idx]
        df["slot"] = [ts.strftime("%H:%M") for ts in idx]
        days = sorted(df["day"].unique())
        today = days[-1]
        tdf = df[df["day"] == today]
        if len(tdf) >= 2:
            last = tdf.iloc[-2]  # last COMPLETED bar
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
        out["error"] = str(exc)[:120]
    return out


def intraday_adjust(verdict: str, sig: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Downgrade (never upgrade) a verdict using the intraday signals.

    Returns (verdict, research_flags, plain_notes)."""
    flags: list[str] = []
    notes: list[str] = []
    v = verdict
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

    # yfinance sometimes returns a NaN close on the newest row; NaN would slip past
    # every comparison (gap gate included) and still yield WATCH.
    try:
        daily = daily.dropna(subset=["Close"])
    except Exception:
        pass
    if daily is None or len(daily) < 2:
        return {"ticker": ticker, "error": f"Not enough clean price history for '{ticker}'."}
    last_close = float(daily["Close"].iloc[-1])
    if not np.isfinite(last_close) or last_close <= 0:
        return {"ticker": ticker, "error": f"No valid last price for '{ticker}'."}
    prev_close = float(daily["Close"].iloc[-2]) if len(daily) > 1 else last_close
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
    if sector_etf:
        s = _sector_daily(sector_etf)
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
            sector_data = {
                "etf": sector_etf,
                "today_pct": round(float(today_pct), 2),
                "five_d_pct": round(float(five_d), 2),
                "twenty_d_pct": round(float(twenty_d), 2),
                "cmf": round(cmf, 4) if cmf is not None else None,
            }

    sector_ok = (
        sector_data is not None
        and sector_data["today_pct"] > 0
        and (sector_data["cmf"] or 0) > 0
    )
    sector_dying = (
        sector_data is not None
        and sector_data["today_pct"] < 0
        and (sector_data["cmf"] or 0) < 0
    )

    # Halt / gap hard gate: large overnight gap or detectable halt → force AVOID.
    gap_pct = float(change_pct) if change_pct is not None else 0.0
    halt_or_gap = False
    research_flags: list[str] = []
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

    # Optional live quote polish
    fh_quote = ds.finnhub_quote(ticker)
    if fh_quote:
        if "Finnhub" not in sources_used:
            sources_used.append("Finnhub")
        last_close = fh_quote["current"]
        change_dollar = fh_quote["change_dollar"]
        change_pct = fh_quote["change_pct"]
        # Recompute halt/gap AFTER Finnhub (gap may only appear with live quote)
        gap_pct = float(change_pct) if change_pct is not None else gap_pct
        if abs(gap_pct) > 8.0 and "gap_gt_8pct" not in research_flags:
            halt_or_gap = True
            research_flags.append("gap_gt_8pct")
            verdict = "AVOID"
            verdict_text = (
                f"Hard AVOID: gap {gap_pct:+.1f}% vs prior close exceeds 8% (Finnhub). "
                "Gap/halts need research — skip autopilot."
            )

    daily_close = daily["Close"].dropna() if "Close" in daily.columns else None
    live_price = fh_quote["current"] if fh_quote else None
    entry = (
        entry_quality(daily_close, current_price=live_price)
        if daily_close is not None
        else None
    )

    # Short-term (5-minute) signals: time-of-day relative volume, VWAP, ATR distances.
    intraday_sig: dict[str, Any] = {}
    try:
        bars5 = t.history(period="5d", interval="5m")
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
        if not halt_or_gap:
            new_v, iflags, inotes = intraday_adjust(verdict, intraday_sig)
            if new_v != verdict:
                verdict_text = f"{verdict_text} Downgraded to WATCH: {' '.join(inotes)}"
                verdict = new_v
            for f in iflags:
                if f not in research_flags:
                    research_flags.append(f)
            intraday_sig["notes"] = inotes
    except Exception:
        pass

    earnings, earn_src = _fetch_earnings(ticker, t)
    if earn_src and earn_src not in sources_used:
        sources_used.append(earn_src)

    sources_used = list(dict.fromkeys(sources_used))

    return {
        "ticker": ticker,
        "price": round(last_close, 2),
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
