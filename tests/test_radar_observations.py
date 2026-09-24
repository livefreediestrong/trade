"""Provider observation time stays separate from radar retrieval time."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import requests
import market_radar as radar
import polygon_client

NOW = datetime(2026, 9, 23, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(radar, 'datetime', Clock)


@pytest.mark.parametrize('stamp', [None, 946684800, (NOW+timedelta(minutes=5)).timestamp()])
def test_missing_stale_future_radar_observations_are_not_ranked(stamp):
    assert radar._row_from_quote('AAPL', price=100, pct=5, volume=1_000_000, market_time=stamp) is None


def test_yahoo_stale_screener_continues_to_fresh_fallback(monkeypatch):
    raw = {'finance':{'result':[{'quotes':[{'symbol':'AAPL','regularMarketPrice':100,
           'regularMarketChangePercent':5,'regularMarketVolume':1_000_000,'regularMarketTime':946684800}]}]}}
    monkeypatch.setattr(requests, 'get', lambda *a, **k: SimpleNamespace(status_code=200, json=lambda:raw))
    monkeypatch.setattr(radar, 'fetch_polygon_snapshot_movers', lambda: [])
    row = radar._row_from_quote('MSFT', price=100, pct=3, volume=1_000_000, market_time=NOW.timestamp())
    calls = []
    monkeypatch.setattr(radar, 'fetch_batch_quote_movers', lambda **kwargs: calls.append(kwargs) or [row])
    result = radar.scan_movers(prefer_alpaca=False)
    assert calls and result['source'] == 'batch_quotes'
    assert result['movers'][0]['market_time'] == NOW.isoformat()
    assert result['movers'][0]['fresh']


def test_off_hours_last_close_is_labeled_and_older_session_rejected():
    weekend = datetime(2026, 9, 26, 16, tzinfo=timezone.utc)
    friday_close = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)
    result = radar._market_observation(100, friday_close, 'fixture', now=weekend)
    assert result['observation_status'] == 'prior_session' and not result['fresh']
    assert radar._market_observation(100, friday_close-timedelta(days=1), 'fixture', now=weekend) is None


@pytest.mark.parametrize('batch', [False, True])
def test_polygon_retains_time_of_selected_price(monkeypatch, batch):
    stamp = int(NOW.timestamp()*1_000_000_000)
    ticker = {'ticker':'AAPL', 'lastTrade':{'p':100, 't':stamp}, 'day':{'c':98,'v':1_000_000},
              'prevDay':{'c':95}, 'todaysChangePerc':5, 'updated':stamp+999}
    monkeypatch.setattr(polygon_client, 'is_configured', lambda: True)
    monkeypatch.setattr(polygon_client, '_get', lambda *a, **k: {'tickers':[ticker]} if batch else {'ticker':ticker})
    row = polygon_client.snapshots(['AAPL'])[0] if batch else polygon_client.snapshot('AAPL')
    assert row['market_time'] == stamp and row['market_time_unit'] == 'ns'
    monkeypatch.setattr(polygon_client, 'fetch_radar_movers', lambda *a: [row])
    output = radar.fetch_polygon_snapshot_movers()
    assert output[0]['market_time'] == NOW.isoformat()
    ticker['lastTrade'].pop('t')
    missing = polygon_client.snapshots(['AAPL'])[0] if batch else polygon_client.snapshot('AAPL')
    monkeypatch.setattr(polygon_client, 'fetch_radar_movers', lambda *a: [missing])
    assert radar.fetch_polygon_snapshot_movers() == []
