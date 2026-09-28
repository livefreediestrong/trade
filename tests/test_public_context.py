import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest
import public_context as pc
import market_events as me

NOW = datetime(2026, 9, 25, 22, tzinfo=timezone.utc)


def test_sofr_effective_date_is_not_publication_and_future_rows_excluded():
    payload = {"refRates": [{"type":"SOFR", "effectiveDate":d, "percentRate":3.88, "revisionIndicator":"R"} for d in ["2026-09-24", "2026-09-26"]]}
    rows = pc.parse("sofr", json.dumps(payload).encode(), NOW)
    assert len(rows) == 1 and rows[0]["observation_date"] == "2026-09-24"
    assert rows[0]["published_at"] is None and rows[0]["publisher_revision"] == "R"
    assert rows[0]["unit"] == "percent"


def test_vintage_changed_value_keeps_old_observation_and_first_seen():
    a = pc.retain_versions([pc.observation("SOFR","2026-09-24",3.8,"percent")], [], NOW)
    original = copy.deepcopy(a)
    same = pc.retain_versions([pc.observation("SOFR","2026-09-24",3.8,"percent")], a, NOW+timedelta(hours=1))
    assert same[0]["first_seen_at"] == a[0]["first_seen_at"]
    b = pc.retain_versions([pc.observation("SOFR","2026-09-24",3.9,"percent")], same, NOW+timedelta(hours=2))
    assert a == original and b[0]["revisions"][0]["value"] == 3.8
    assert b[0]["first_seen_at"] != a[0]["first_seen_at"] and b[0]["vintage_id"] != a[0]["vintage_id"]


def test_nasdaq_quote_resumption_is_not_trade_resumption():
    body = b'<rss xmlns:n="http://www.nasdaqtrader.com/"><channel><item><n:IssueSymbol>TEST</n:IssueSymbol><n:HaltDate>09/25/2026</n:HaltDate><n:HaltTime>10:00:00.000</n:HaltTime><n:ResumptionDate>09/25/2026</n:ResumptionDate><n:ResumptionQuoteTime>10:05:00</n:ResumptionQuoteTime><n:ReasonCode>T1</n:ReasonCode></item></channel></rss>'
    row = pc.parse("halts", body, NOW)[0]
    assert row["resumption_at"] is None and row["halt_at"] == "2026-09-25T14:00:00+00:00"
    assert row["published_at"] is None


def test_vix_and_treasury_preserve_units():
    rows = pc.parse("vix", b'DATE,OPEN,HIGH,LOW,CLOSE\n09/24/2026,1,2,1,19.5\n', NOW)
    assert rows[0]["value"] == 19.5 and rows[0]["unit"] == "index points"
    body = b'<root xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"><m:properties><d:NEW_DATE>2026-09-24T00:00:00</d:NEW_DATE><d:BC_10YEAR>4.1</d:BC_10YEAR></m:properties></root>'
    rows = pc.parse("treasury",body,NOW)
    assert rows[0]["series"] == "Treasury 10 year" and rows[0]["unit"] == "percent"
    with pytest.raises(ValueError):pc.parse("treasury",b'<!DOCTYPE root><root/>',NOW)
    with pytest.raises(ValueError):pc.observation("x","2026-09-24",float('nan'),"percent")


def test_failed_refresh_retains_rows_and_backs_off(tmp_path, monkeypatch):
    def load(path, default):return json.loads(path.read_text()) if path.exists() else default
    desk = NS(DATA_DIR=tmp_path,_load_json=load,_save_json=lambda p,v:p.write_text(json.dumps(v)))
    svc = pc.PublicContext(desk)
    monkeypatch.setattr(pc,"SOURCES",{"sofr":pc.SOURCES["sofr"]})
    monkeypatch.setattr(me,"_fetch",lambda *a,**k:b'{"refRates":[{"type":"SOFR","effectiveDate":"2026-09-24","percentRate":3.8}]}')
    svc.refresh(NOW)
    assert svc.snapshot(NOW)["sources"][0]["fresh"]
    def failed(*a,**k):raise TimeoutError("provider late")
    monkeypatch.setattr(me,"_fetch",failed)
    svc.refresh(NOW+timedelta(hours=2))
    snap=svc.snapshot(NOW+timedelta(hours=2))
    assert not snap["sources"][0]["fresh"] and snap["sources"][0]["rows"][0]["value"] == 3.8
    before=svc.path.read_bytes();svc.refresh(NOW+timedelta(hours=2,minutes=2));assert svc.path.read_bytes()==before


def test_bea_calendar_utc_dst_and_cancelled():
    body='BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:20261030T123000Z\nSUMMARY:Personal Income and Outlays\nEND:VEVENT\nBEGIN:VEVENT\nDTSTART:20261030T123000Z\nSUMMARY:Canceled GDP\nSTATUS:CANCELLED\nEND:VEVENT\nEND:VCALENDAR'
    rows=me.parse_bea_calendar(body)
    assert len(rows)==1 and rows[0]["impact"]=="high"
    assert datetime.fromisoformat(rows[0]["start"]).astimezone(me.ET).hour==8


def test_census_visible_dates_override_hidden_sort_keys_and_skip_suspended():
    body='<table id="calendar"><tr><td>Advance Monthly Sales for Retail</td><td>October 16, 2026</td><td>8:30 AM</td><td>September 2026</td><td>A202001011000</td></tr><tr><td>Suspended release</td><td>Suspended</td><td>10:00 AM</td><td>August 2026</td><td>A202609261000</td></tr></table>'
    rows=me.parse_census_calendar(body)
    assert len(rows)==1 and rows[0]["start"]=='2026-10-16T12:30:00+00:00'
    assert rows[0]["observation_period"]=='September 2026' and rows[0]["impact"]=='high'


def test_next_session_brief_is_dated_and_does_not_invent_gaps():
    r=pc.session_brief(NOW,[{"start":"2026-09-28T12:30:00+00:00","impact":"high","title":"Release"}],[])
    assert r["next_session"]=='2026-09-28T09:30:00-04:00'
    assert len(r["events"])==1 and 'cannot measure' in r["note"]
