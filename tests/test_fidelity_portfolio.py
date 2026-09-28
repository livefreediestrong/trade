"""Fidelity portfolio: import, holding readings, sizing and routes (suggestions only)."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest

import fidelity_portfolio as fid

WEB_CSV = """﻿Account Number,Account Name,Symbol,Description,Quantity,Last Price,Last Price Change,Current Value,Today's Gain/Loss Dollar,Today's Gain/Loss Percent,Total Gain/Loss Dollar,Total Gain/Loss Percent,Percent Of Account,Cost Basis Total,Average Cost Basis,Type
Z12341743,Cash Management,SPAXX**,HELD IN MONEY MARKET,,,,$4225.70,,,,,58.43%,,,Cash
Z12341743,Cash Management,META,META PLATFORMS INC CLASS A,1.39,$721.51,-$30.16,$1002.89,+$3.49,+0.34%,+$3.49,+0.34%,13.87%,$999.40,$718.99,Cash
Z12341743,Cash Management,INTC,INTEL CORP,17.37,$115.33,-$7.68,$2003.20,+$3.29,+0.16%,+$3.29,+0.16%,27.70%,$1999.90,$115.14,Cash
Z12341743,Cash Management,Pending Activity,,,,,-$10.00,,,,,,,,

"The data and information in this spreadsheet is provided to you solely for your use and is not for distribution."
"""

TRADER_CSV = """Symbol,Quantity,Last,$ Chg,% Ch,$ Avg,$ Day G/L,Value,Basis
META,1.39,721.505,-30.155,-4.01,718.99,3.49,"1,002.89",999.40
"Cash (SPAXX)","4,225.7",1.00,0.00,0.00,--,--,"4,225.70",--
INTC,17.37,115.325,-7.675,-6.24,115.14,3.29,"2,003.20","1,999.90"
"""


def test_imports_fidelity_web_csv():
    out = fid.parse_import(WEB_CSV)
    assert out["source"] == "Fidelity CSV" and out["accounts"] == ["Z12341743"]
    assert {p["ticker"]: (p["qty"], p["avg_cost"]) for p in out["positions"]} == {"META": (1.39, 718.99), "INTC": (17.37, 115.14)}
    assert out["cash"] == pytest.approx(4215.70)  # money market plus pending activity


def test_imports_trader_plus_export():
    out = fid.parse_import(TRADER_CSV)
    assert [p["ticker"] for p in out["positions"]] == ["META", "INTC"]
    assert out["cash"] == pytest.approx(4225.70)


def test_imports_typed_lines_and_merges_duplicates():
    out = fid.parse_import("META 1 700\nmeta 1 740\nINTC, 17.37\nCASH 4225.70\nnot a line")
    meta = next(p for p in out["positions"] if p["ticker"] == "META")
    assert meta["qty"] == 2 and meta["avg_cost"] == 720
    assert out["cash"] == 4225.70 and out["skipped"] == ["not a line"]


def test_import_rejects_empty():
    with pytest.raises(ValueError):
        fid.parse_import("   ")
    with pytest.raises(ValueError):
        fid.parse_import("hello there")


SETTINGS = dict(fid.DEFAULT_SETTINGS)


def test_sizing_uses_risk_limit_and_cash_with_fractional_shares():
    size = fid.size_idea(7000, 4000, 0, 100.0, 96.0, SETTINGS)  # risk $70 / $4 = 17.5 sh; limit 14 sh
    assert size["shares"] == 14.0 and size["limited_by"] == "the position limit"
    small_cash = fid.size_idea(7000, 250, 0, 100.0, 96.0, SETTINGS)
    assert small_cash["shares"] == 2.5 and small_cash["limited_by"] == "available cash"
    assert fid.size_idea(7000, 4000, 0, 100.0, 101.0, SETTINGS)["shares"] == 0


def analysis(verdict="WATCH", price=100.0, atr=3.0, above_20=True, above_50=True, earnings_days=None):
    return {"ticker": "X", "price": price, "verdict": verdict, "verdict_text": "t", "change_pct": -1.0,
            "intraday": {"atr_usd": atr}, "sma": {"above_20": above_20, "above_50": above_50},
            "earnings": {"days_away": earnings_days, "date": "2026-10-01"} if earnings_days is not None else None}


def test_holding_readings():
    pos = {"ticker": "INTC", "qty": 10.0, "avg_cost": 90.0}
    add = fid.holding_row(pos, analysis("PASS"), 10000, 5000, SETTINGS)
    assert add["action"] == "add" and add["stop"] == 94.0 and add["gain_usd"] == 100.0 and add["size"]["shares"] > 0
    weak = fid.holding_row(pos, analysis("AVOID", above_20=False, above_50=False), 10000, 5000, SETTINGS)
    assert weak["action"] == "review" and weak["trend"] == "down" and "94.00" in weak["detail"]
    # AVOID for low volume alone is a same-day entry filter, not a reason to sell
    quiet = fid.holding_row(pos, dict(analysis("AVOID"), verdict_text="Relative volume only 0.9x (need 1.5x). Skip."), 10000, 5000, SETTINGS)
    assert quiet["action"] == "hold" and quiet["trend"] == "up" and "Relative volume only 0.9x" in quiet["detail"]
    hold = fid.holding_row(pos, analysis(), 10000, 5000, SETTINGS)
    assert hold["action"] == "hold"
    big = fid.holding_row(pos, analysis(), 3000, 0, SETTINGS)  # $1,000 is 33% of the account
    assert big["action"] == "review" and "position limit" in big["flags"][0]
    soon = fid.holding_row(pos, analysis(earnings_days=2), 10000, 5000, SETTINGS)
    assert any("Earnings in 2" in f for f in soon["flags"])
    missing = fid.holding_row(pos, {"error": "No data"}, 10000, 5000, SETTINGS)
    assert missing["action"] == "unknown"


def test_ideas_are_todays_pass_buys_not_held():
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(fid.ET).strftime("%Y-%m-%d")
    signals = [
        {"ticker": "AAPL", "verdict": "PASS", "side": "buy", "ts": now, "signal_price": 200, "stop": 196, "target": 210},
        {"ticker": "AAPL", "verdict": "PASS", "side": "buy", "ts": now, "signal_price": 201, "stop": 197},  # duplicate
        {"ticker": "META", "verdict": "PASS", "side": "buy", "ts": now, "signal_price": 700, "stop": 690},  # held
        {"ticker": "WMT", "verdict": "WATCH", "side": "buy", "ts": now, "signal_price": 100, "stop": 99},
        {"ticker": "KO", "verdict": "PASS", "side": "buy", "ts": "2026-01-02T15:00:00+00:00", "signal_price": 60, "stop": 59},
        {"ticker": "PEP", "verdict": "PASS", "side": "buy", "ts": now, "signal_price": 150, "stop": 147,
         "execution_block": "setup_losing_record: won 20%"},
    ]
    ideas = fid.ideas_from_signals(signals, {"META"}, 7000, 4000, SETTINGS, today=today)
    assert [i["ticker"] for i in ideas] == ["AAPL", "PEP"]
    assert ideas[1]["warning"].startswith("setup_losing_record")


@pytest.fixture
def desk(tmp_path):
    return NS(DATA_DIR=tmp_path, LESSONS_PATH=tmp_path / "lessons.json", load_signals=lambda: [])


def test_suggestions_end_to_end(desk):
    data = fid.empty()
    data.update(positions=[{"ticker": "META", "qty": 1.39, "avg_cost": 718.99},
                           {"ticker": "INTC", "qty": 17.37, "avg_cost": 115.14}], cash=4225.70)
    fid.save(desk, data)
    fake = {"META": analysis("AVOID", price=721.5, atr=15, above_20=False, above_50=False),
            "INTC": analysis("WATCH", price=115.33, atr=4)}
    out = fid.suggestions(desk, analyze=lambda t: fake[t])
    assert out["ok"] and out["account_value"] == pytest.approx(1.39 * 721.5 + 17.37 * 115.33 + 4225.70, abs=0.01)
    assert [h["ticker"] for h in out["holdings"]] == ["INTC", "META"]  # INTC is 28% > 20% limit → review, larger first
    assert out["evidence"]["passed"] is False
    assert "never connects to Fidelity" in out["note"]


def test_routes_round_trip(desk):
    from flask import Flask
    app = Flask(__name__)
    fid.register(app, desk)
    c = app.test_client()
    assert c.get("/api/fidelity").get_json()["has_holdings"] is False
    r = c.post("/api/fidelity/import", json={"text": TRADER_CSV})
    assert r.status_code == 200 and len(r.get_json()["portfolio"]["positions"]) == 2
    assert c.post("/api/fidelity/import", json={"text": ""}).status_code == 400
    assert c.post("/api/fidelity/settings", json={"risk_pct": 9}).status_code == 400
    assert c.post("/api/fidelity/settings", json={"risk_pct": 0.5, "max_position_pct": 25, "stop_atr": 2}).get_json()["portfolio"]["settings"]["risk_pct"] == 0.5
    assert c.post("/api/fidelity/clear", json={}).get_json()["has_holdings"] is False
