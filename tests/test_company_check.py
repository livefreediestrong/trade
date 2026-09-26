"""Company check is research only: offline parsing, plain summaries, graceful gaps."""
from __future__ import annotations


import company_check as cc

BASE = "http://127.0.0.1:5056"

FORM4 = """<?xml version="1.0"?>
<ownershipDocument>
 <aff10b5One>1</aff10b5One>
 <reportingOwner>
  <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
  <reportingOwnerRelationship><isDirector>0</isDirector><isOfficer>1</isOfficer><officerTitle>Chief Financial Officer</officerTitle></reportingOwnerRelationship>
 </reportingOwner>
 <nonDerivativeTable>
  <nonDerivativeTransaction>
   <transactionDate><value>2026-09-10</value></transactionDate>
   <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
   <transactionAmounts><transactionShares><value>1000</value></transactionShares>
    <transactionPricePerShare><value>50.5</value></transactionPricePerShare></transactionAmounts>
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
    assert buy["action"] == "bought on the open market" and buy["value"] == 50500 and buy["planned"] is True
    assert rows[1]["value"] is None and rows[1]["action"] == "gave shares to pay tax"


def test_insider_summary_puts_buys_first_and_explains_sales():
    rows = cc.parse_form4(FORM4)
    s = cc.summarize_insiders("ABC", rows, 1)
    assert "1 insider bought ABC" in s["summary"] and "$50.5K" in s["summary"]
    sale = dict(rows[0], code="S", action="sold on the open market")
    s = cc.summarize_insiders("ABC", [sale], 1)
    assert "No insider bought" in s["summary"] and "Sales are common" in s["summary"]
    assert "no open-market" in cc.summarize_insiders("ABC", [rows[1]], 3)["summary"]


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
