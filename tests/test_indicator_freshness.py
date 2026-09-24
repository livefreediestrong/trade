"""Market-input coherence regressions with fixed clocks and no provider calls."""
import datetime as dt
import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import llm_trader
import screener_logic as screener


NOW = pd.Timestamp("2026-09-23 13:02", tz="America/New_York")
METRICS = ("vwap", "vwap_slope_pct", "vwap_dist_atr", "hod_dist_atr", "rel_vol_5m")


def bars(index, close=110.0, volume=1000):
    return pd.DataFrame({
        "Open": close, "High": close + .1, "Low": close - .1,
        "Close": close, "Volume": volume,
    }, index=index)


@pytest.mark.parametrize("index,now,reason", [
    (pd.date_range("2026-09-18 09:30", periods=9, freq="5min", tz="America/New_York"), NOW, "stale_session"),
    (pd.DatetimeIndex([NOW - pd.Timedelta(minutes=11)]), NOW, "stale_bars"),
    (pd.DatetimeIndex([NOW + pd.Timedelta(minutes=1)]), NOW, "future_bars"),
    (pd.DatetimeIndex([NOW.tz_localize(None)]), NOW, "missing_bar_timezone"),
    (pd.date_range("2026-09-23 15:55", periods=1, freq="5min", tz="America/New_York"),
     pd.Timestamp("2026-09-23 16:01", tz="America/New_York"), "market_closed"),
])
def test_old_or_unverifiable_bars_cannot_be_current_indicators(index, now, reason):
    result = screener.intraday_signals(bars(index), atr_usd=5, price=103, now=now)
    assert not result["fresh"] and not result["available"]
    assert result["unavailable_reason"] == reason
    assert all(key not in result for key in METRICS)
    verdict, flags, notes = screener.intraday_adjust("PASS", result)
    assert verdict == "WATCH"
    assert any(flag in flags for flag in ("intraday_stale", "intraday_unavailable"))
    assert "unavailable" in notes[0]


@pytest.mark.parametrize("include_forming", [False, True])
def test_completed_bar_is_selected_by_its_end_time(include_forming):
    frames = [bars(pd.DatetimeIndex([pd.Timestamp(f"2026-09-{day} 12:55", tz="America/New_York")]))
              for day in (21, 22)]
    frames.append(bars(pd.DatetimeIndex([NOW.floor("5min") - pd.Timedelta(minutes=5)]), volume=5000))
    if include_forming:
        frames.append(bars(pd.DatetimeIndex([NOW.floor("5min")]), volume=100))
    result = screener.intraday_signals(pd.concat(frames), atr_usd=5, price=110, now=NOW)
    assert result["fresh"] and result["available"]
    assert result["rel_vol_5m"] == 5.0
    assert result["completed_bar_at"] == "2026-09-23T12:55:00-04:00"
    assert result["as_of"] == ("2026-09-23T13:00:00-04:00" if include_forming else "2026-09-23T12:55:00-04:00")


@pytest.fixture
def analysis_inputs(monkeypatch):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.to_pydatetime().astimezone(tz) if tz else NOW.to_pydatetime().replace(tzinfo=None)

    monkeypatch.setattr(screener, "datetime", Clock)
    daily = bars(pd.bdate_range(end=NOW.normalize(), periods=60), close=105.0)
    daily.iloc[-1, daily.columns.get_loc("Close")] = 110.0
    sector = daily.copy()
    sector["Close"] = np.arange(1.0, 61.0)
    hours = bars(pd.DatetimeIndex([
        pd.Timestamp("2026-09-22 09:30", tz="America/New_York"),
        pd.Timestamp("2026-09-23 09:30", tz="America/New_York"),
    ]))
    hours["Volume"] = [1000, 3000]
    fixture = SimpleNamespace(
        quote={"price": 110.0, "fresh": True, "source": "test provider", "market_time": NOW.isoformat()},
        bars5=bars(pd.date_range("2026-09-23 12:20", periods=9, freq="5min", tz="America/New_York")),
        history_error=False,
        daily=daily,
        sector=sector,
    )

    class Ticker:
        info = {"sector": "Technology", "marketState": "REGULAR", "regularMarketTime": NOW.timestamp()}

        def history(self, **kwargs):
            if kwargs["interval"] == "1d":
                return fixture.daily.copy()
            if kwargs["interval"] == "60m":
                return hours.copy()
            if fixture.history_error:
                raise TimeoutError("isolated history outage")
            return fixture.bars5.copy()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda symbol: Ticker()))
    monkeypatch.setattr(screener.ds, "latest_quote", lambda symbol: dict(fixture.quote))
    monkeypatch.setattr(screener, "_sector_daily", lambda symbol: fixture.sector.copy())
    monkeypatch.setattr(screener, "chaikin_money_flow", lambda *args: .1)
    monkeypatch.setattr(screener, "_fetch_earnings", lambda *args: (None, None))
    monkeypatch.setattr(screener, "_rth_now", lambda: True)
    return fixture


def test_current_quote_crossing_below_smas_cannot_remain_pass(analysis_inputs):
    control = screener.analyze_ticker("TEST")
    assert control["verdict"] == "PASS"
    analysis_inputs.quote["price"] = 103.0
    result = screener.analyze_ticker("TEST")
    assert result["price"] == 103.0
    assert all(result["sma"][key] > result["price"] for key in ("sma10", "sma20", "sma50"))
    assert not any(result["sma"][key] for key in ("above_10", "above_20", "above_50"))
    assert not result["checks"]["above_sma_10_20_50"]
    assert result["verdict"] != "PASS"
    assert result["change_pct"] == result["gap_pct"] == round((103 / 105 - 1) * 100, 2)


@pytest.mark.parametrize("failure", ["stale", "missing", "error"])
def test_fresh_quote_does_not_hide_an_independent_indicator_outage(analysis_inputs, failure):
    if failure == "stale":
        analysis_inputs.bars5.index = analysis_inputs.bars5.index - pd.Timedelta(days=5)
    elif failure == "missing":
        analysis_inputs.bars5 = analysis_inputs.bars5.iloc[:0]
    else:
        analysis_inputs.history_error = True
    result = screener.analyze_ticker("TEST")
    assert result["quote"]["fresh"]
    assert result["verdict"] == "WATCH"
    assert not result["intraday"]["fresh"]
    assert all(key not in result["intraday"] for key in METRICS)
    assert "unavailable" in result["verdict_text"]
    assert any(flag in result["research_flags"] for flag in ("intraday_stale", "intraday_unavailable"))
    context = json.loads(llm_trader._analysis_context_blob(result))
    assert context["quote"]["fresh"] and not context["intraday"]["fresh"]
    citation = next(c for c in llm_trader.screener_citations(result) if c["key"] == "intraday_status")
    assert citation["value"].startswith("Unavailable:")


def test_unverified_price_cannot_keep_pass_verdict(analysis_inputs):
    analysis_inputs.quote["fresh"] = False
    result = screener.analyze_ticker("TEST")
    assert result["verdict"] == "WATCH"
    assert "stale_or_unverified_quote" in result["research_flags"]


@pytest.mark.parametrize("shift", [-365, 1])
def test_current_quote_cannot_mask_stale_or_future_daily_history(analysis_inputs, shift):
    analysis_inputs.daily.index += pd.Timedelta(days=shift)
    result = screener.analyze_ticker("TEST")
    assert result["error"] and not result["daily_history"]["fresh"]
    assert result.get("verdict") != "PASS"


def test_sector_history_is_checked_independently(analysis_inputs):
    analysis_inputs.sector.index -= pd.Timedelta(days=365)
    result = screener.analyze_ticker("TEST")
    assert result["daily_history"]["fresh"] and result["quote"]["fresh"]
    assert result["sector"] is None and result["verdict"] != "PASS"
    assert "sector_history_unavailable" in result["research_flags"]


@pytest.mark.parametrize("latest", ["completed", "forming", "nan"])
def test_previous_close_uses_prior_session_for_each_daily_provider_shape(analysis_inputs, latest):
    daily = analysis_inputs.daily
    daily.loc[daily.index[-2], "Close"] = 110
    daily.loc[daily.index[-3], "Close"] = 100
    if latest == "completed":
        analysis_inputs.daily = daily.iloc[:-1]
    elif latest == "nan":
        daily.loc[daily.index[-1], "Close"] = np.nan
    result = screener.analyze_ticker("TEST")
    assert result["prev_close"] == 110 and result["gap_pct"] == result["change_pct"] == 0
    assert "gap_gt_8pct" not in result["research_flags"]


def test_daily_history_sorts_and_deduplicates_sessions(analysis_inputs):
    daily = analysis_inputs.daily
    analysis_inputs.daily = pd.concat([daily.iloc[::-1], daily.iloc[[-1]]])
    result = screener.analyze_ticker("TEST")
    assert result["daily_history"]["completed_session"] == "2026-09-22"
    assert result["prev_close"] == 105
