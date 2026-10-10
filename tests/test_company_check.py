"""Company check is research only: offline parsing, plain summaries, graceful gaps."""
from __future__ import annotations


import company_check as cc
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace as NS

BASE = "http://127.0.0.1:5056"

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
 <aff10b5One>1</aff10b5One>
 <reportingOwner>
  <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName><rptOwnerCik>1234</rptOwnerCik></reportingOwnerId>
  <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship>
 </reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction>
   <securityTitle><value>Common stock</value></securityTitle>
   <transactionDate><value>2026-09-10</value></transactionDate>
   <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>1000</value></transactionShares>
    <transactionPricePerShare><value>50.5</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
   <postTransactionAmounts><sharesOwnedFollowingTransaction><value>5000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
   <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
   <transactionDate><value>2026-09-11</value></transactionDate>
   <transactionCoding><transactionCode>F</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>200</value></transactionShares></transactionAmounts>
  </nonDerivativeTransaction>
 </nonDerivativeTable>
</ownershipDocument>"""


def test_form4_rows_carry_role_action_value_and_plan_flag():
    rows = cc.parse_form4(FORM4)
    assert [r["code"] for r in rows] == ["P", "F"]
    buy = rows[0]
    assert buy["name"] == "Doe Jane" and buy["role"] == "Chief Financial Officer"
    assert buy["action"] == "purchase (market or private)" and buy["value"] == 50500 and buy["planned"] is True
    assert rows[1]["value"] is None and rows[1]["action"] == "shares used for tax or exercise payment"


def test_insider_summary_puts_buys_first_and_explains_sales():
    rows = cc.parse_form4(FORM4)
    s = cc.summarize_insiders("ABC", rows, 1)
    assert '1 purchase and 0 sale rows' in s['summary'] and s['buys']['value'] == 50500
    assert 'own money' not in s['summary'] and 'market and private' in s['summary']
    sale = dict(rows[0], code="S", action="sale (market or private)")
    s = cc.summarize_insiders("ABC", [sale], 1)
    assert '0 purchase and 1 sale rows' in s['summary']
    assert '0 purchase and 0 sale rows' in cc.summarize_insiders('ABC', [rows[1]], 3)['summary']


def test_company_names_are_cleaned_for_name_matched_databases():
    assert cc.search_name("LOCKHEED MARTIN CORP") == "LOCKHEED MARTIN"
    assert cc.search_name("Apple Inc.") == "APPLE"
    assert cc.search_name("NVIDIA CORP /DE/") == "NVIDIA"
    assert cc.clean_ticker(" brk.b ") == "BRK.B" and cc.clean_ticker("../etc") is None


def test_bottom_line_explains_each_number_in_plain_words():
    info = {"quoteType": "EQUITY", "shortName": "Acme", "trailingPE": 40.0, "forwardPE": 30.0, "revenueGrowth": 0.12,
            "profitMargins": 0.2, "debtToEquity": 250.0, "currentPrice": 100.0, "targetMeanPrice": 120.0,
            "targetLowPrice": 90.0, "targetHighPrice": 150.0, "numberOfAnalystOpinions": 12}
    out = cc.bottom_line("ACME", info)
    items = {i["key"]: i for i in out["items"]}
    assert "$40 for each $1 of yearly profit" in items["value"]["plain"] and items["value"]["tone"] == "watch"
    assert "grow" in items["value_next"]["plain"]
    assert "12% higher" in items["growth"]["plain"] and items["growth"]["tone"] == "good"
    assert "20 cents" in items["profit"]["plain"]
    assert "$2.50 for every $1" in items["debt"]["plain"] and items["debt"]["tone"] == "watch"
    assert "20% above" in items["analysts"]["plain"] and "often wrong" in items["analysts"]["plain"]
    assert "2 strong points and 2 to watch" in out["summary"]


def test_funds_skip_company_checks():
    out = cc.bottom_line("SPY", {"quoteType": "ETF"})
    assert out["ok"] and out["applies"] is False and "fund" in out["summary"]


def test_congress_without_a_key_says_how_to_connect(monkeypatch):
    monkeypatch.delenv("QUIVER_API_KEY", raising=False)
    out = cc.congress("LMT")
    assert out["ok"] is False and out["connected"] is False and "QUIVER_API_KEY" in out["error"]


def test_one_failing_source_never_hides_the_others(monkeypatch):
    monkeypatch.setattr(cc, "insiders", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(cc, "contracts", lambda *a: {"ok": True, "summary": "c"})
    monkeypatch.setattr(cc, "lobbying", lambda *a: {"ok": True, "summary": "l"})
    monkeypatch.setattr(cc, "congress", lambda *a: {"ok": False, "error": "off"})
    monkeypatch.setattr(cc, "bottom_line", lambda *a: {"ok": True, "summary": "b", "items": []})
    import sys
    import types
    fake = types.SimpleNamespace(Ticker=lambda t: types.SimpleNamespace(info={"quoteType": "EQUITY", "longName": "Acme Widgets Inc"}))
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    import edgar_client
    monkeypatch.setattr(edgar_client, "company_title", lambda t: "")
    out = cc.build("ACME")
    assert out["research_only"] and out["search_name"] == "ACME WIDGETS"
    assert out["sections"]["insiders"]["ok"] is False and "RuntimeError" in out["sections"]["insiders"]["error"]
    assert out["sections"]["contracts"]["summary"] == "c" and out["sections"]["lobbying"]["summary"] == "l"


def test_route_rejects_bad_symbols_and_serves_cached_checks(monkeypatch, tmp_path):
    import app as desk
    cc.configure(tmp_path)
    calls = []
    monkeypatch.setattr(cc, "build", lambda t, now=None: calls.append(t) or {"ok": True, "ticker": t, "sections": {}, "checked_at": "x"})
    monkeypatch.setattr(cc, "_cache", {})
    client = desk.app.test_client()
    assert client.get("/api/research/company?ticker=../x", base_url=BASE).status_code == 400
    first = client.get("/api/research/company?ticker=lmt", base_url=BASE).get_json()
    second = client.get("/api/research/company?ticker=LMT", base_url=BASE).get_json()
    assert first["cached"] is False and second["cached"] is True and calls == ["LMT"]
    client.get("/api/research/company?ticker=LMT&refresh=1", base_url=BASE)
    assert calls == ["LMT", "LMT"] and (tmp_path / "company_checks.json").exists()


@pytest.fixture
def sec_responses(monkeypatch):
    import edgar_client
    monkeypatch.setattr(cc, "_sec_headers", lambda: {"User-Agent": "test owner"})
    monkeypatch.setattr(edgar_client, "cik_for_ticker", lambda t: "0000000123")
    monkeypatch.setattr(cc.time, "sleep", lambda *a: None)
    listing = {"filings": {"recent": {"form": ["4", "4"], "filingDate": ["2026-09-11", "2026-09-12"],
                                     "accessionNumber": ["0001-01", "0001-02"], "primaryDocument": ["a.xml", "b.xml"]}}}
    return listing


def test_failed_filings_never_become_no_insider_buys(monkeypatch, sec_responses):
    monkeypatch.setattr(cc.requests, "get", lambda url, **kw:
                        NS(status_code=200, json=lambda: sec_responses) if "submissions" in url else NS(status_code=403))
    result = cc.insiders("ABC", datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert not result["ok"] and result["reviewed_filings"] == 0
    assert "unknown" in result["error"] and "no insider" not in result["error"].lower()


def test_partial_coverage_is_explicit_and_old_transactions_excluded(monkeypatch, sec_responses):
    old = FORM4.replace("2026-09-10", "2026-01-10")
    monkeypatch.setattr(cc.requests, "get", lambda url, **kw:
                        NS(status_code=200, json=lambda: sec_responses) if "submissions" in url else
                        NS(status_code=200, content=old.encode(), text=old) if url.endswith("a.xml") else NS(status_code=503))
    result = cc.insiders("ABC", datetime(2026, 9, 25, tzinfo=timezone.utc))
    assert result["ok"] and not result["complete"] and result["reviewed_filings"] == 1
    assert result["buys"]["people"] == 0 and "Partial filing coverage" in result["summary"]
    assert "Unread filings" in result["summary"]


@pytest.mark.parametrize("text", ["<html><body>Access denied</body></html>", "<!DOCTYPE x><ownershipDocument/>"])
def test_nonfiling_responses_are_not_empty_valid_filings(text):
    with pytest.raises(ValueError):
        cc.parse_form4(text)


@pytest.mark.parametrize("at", ["bad", float("nan"), float("inf"), 1100, 900])
def test_corrupt_future_and_failed_cached_reads_are_rechecked(monkeypatch, at):
    monkeypatch.setattr(cc.time, "time", lambda: 1000)
    monkeypatch.setattr(cc, "_cache", {"ABC": {"ok": True, "_at": at, "_cache_version": cc.CACHE_VERSION,
                                               "sections": {"insiders": {"ok": False}}}})
    monkeypatch.setattr(cc, "_save_cache", lambda: None)
    calls = []
    monkeypatch.setattr(cc, "build", lambda t: calls.append(t) or {"ok": True, "sections": {}})
    assert cc.check("ABC")["cached"] is False and calls == ["ABC"]
