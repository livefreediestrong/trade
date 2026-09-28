from datetime import datetime, timezone, timedelta
from types import SimpleNamespace as NS

import pytest
import app as desk
import broker_ibkr as broker
from broker_safety import assess
from test_broker_lifecycle import gateway

NOW = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)
BASE = "http://127.0.0.1:5056"


def position(**kwargs):
    return {"con_id": 100, "symbol": "TEST", "asset_type": "STK", "currency": "USD", "qty": 10, **kwargs}


def stop(**kwargs):
    return {"con_id": 100, "asset_type": "STK", "side": "sell", "order_type": "STP", "qty": 10,
            "filled_qty": 0, "permanent_id": 123, "transmit": True, "status": "Submitted",
            "conditions_count": 0, "good_after_time": "", "good_till_date": "",
            "active_start_time": "", "active_stop_time": "", "algo_strategy": "",
            "parent_id": 0, "oca_group": "", "why_held": "", "stop_price": 95, "time_in_force": "GTC", **kwargs}


def result(positions=None, orders=None, **kwargs):
    return assess({"ok": True, "checked_at": NOW.isoformat(), "positions": [position()] if positions is None else positions,
                   "orders": [stop()] if orders is None else orders, **kwargs}, NOW)


def test_matching_active_full_quantity_stop_is_reported_without_qualification():
    r = result()
    assert r["rows"][0]["state"] == "stop_reported"
    assert r["broker_native_qualification"] is False


@pytest.mark.parametrize("change", [{"qty": 9}, {"qty": 11}, {"filled_qty": 1}, {"filled_qty": -1},
    {"qty": float("nan")}, {"parent_id": 8}, {"permanent_id": 0}, {"transmit": False},
    {"status": "PendingSubmit"}, {"status": "Inactive"}, {"why_held": "child"}, {"oca_group": "siblings"},
    {"order_type": "STP LMT"}, {"stop_price": float("inf")}, {"con_id": 999}])
def test_incomplete_contingent_or_wrong_contract_stop_is_not_full_coverage(change):
    report = result(orders=[stop(**change)])
    assert not report["ok"] or report["rows"][0]["state"] == "review"


def test_duplicate_stops_not_added_together_and_day_expiry_is_visible():
    assert result(orders=[stop(), stop()])["rows"][0]["state"] == "review"
    assert any("expires" in x for x in result(orders=[stop(time_in_force="DAY")])["rows"][0]["notes"])


@pytest.mark.parametrize("expiry,text", [("20260925", "Expires today"), ("20260924", "Past its listed expiry"),
                                          ("2026-10-16", "21 calendar days"), ("bad", "could not be verified")])
def test_options_keep_expiry_and_early_assignment_visible(expiry, text):
    row = result([position(asset_type="OPT", expiry=expiry, qty=-1)], [])["rows"][0]
    assert any(text in s for s in row["notes"])
    assert any("before expiry" in s for s in row["notes"])
    assert row["state"] == "review"


def test_unknown_stale_or_invalid_data_never_becomes_empty_account():
    for override in ({"ok": False}, {"positions": None}, {"orders": [None]},
                     {"checked_at": (NOW-timedelta(seconds=46)).isoformat()},
                     {"checked_at": (NOW+timedelta(seconds=1)).isoformat()}):
        assert assess({"ok": True, "checked_at": NOW.isoformat(), "positions": [position()],
                       "orders": [stop()], **override}, NOW)["ok"] is False
    assert result([position(qty=float("nan"))])["ok"] is False
    assert result([], [])["position_count"] == 0


def test_safety_route_never_calls_order_methods(monkeypatch):
    import broker_alpaca as adapter
    monkeypatch.setattr(adapter, "public_status", lambda: {"broker": "ibkr", "connected": True})
    monkeypatch.setattr(adapter, "get_safety_snapshot", lambda: {
        "ok": True, "checked_at": datetime.now(timezone.utc).isoformat(), "positions": [], "orders": []}, raising=False)
    for method in ("place_from_desk_order", "cancel_reviewed_order", "flatten_broker"):
        monkeypatch.setattr(adapter, method, lambda *a: pytest.fail("read-only route changed orders"))
    r = desk.app.test_client().get("/api/broker-safety", base_url=BASE)
    assert r.status_code == 200 and r.json["position_count"] == 0


@pytest.mark.parametrize("asset,currency,qty", [("OPT", "USD", -1), ("FUT", "USD", 1), ("STK", "EUR", 2), ("CASH", "JPY", 1)])
def test_restart_exposure_reader_does_not_filter_assets_or_currencies(gateway, monkeypatch, asset, currency, qty):
    monkeypatch.setattr(gateway, "positions", lambda account: [NS(account=account, position=qty,
                        contract=NS(secType=asset, currency=currency))])
    r = broker.get_position_exposure()
    assert r["ok"] and r["has_positions"] and r["position_count"] == 1


def test_snapshot_contains_only_selected_account(gateway, monkeypatch):
    from ib_async import Stock, StopOrder
    contract = Stock("TEST", "SMART", "USD", conId=100)
    order = StopOrder("SELL", 10, 95, account="DU123", permId=123)
    trade = NS(contract=contract, order=order,
               orderStatus=NS(status="Submitted", filled=0, whyHeld=""))
    monkeypatch.setattr(gateway, "reqAllOpenOrders", lambda: [trade])
    monkeypatch.setattr(gateway, "positions", lambda account: [
        NS(account=account, position=10, contract=contract), NS(account="OTHER", position=999, contract=contract)])
    r = broker.get_safety_snapshot()
    assert r["ok"] and len(r["positions"]) == 1 and len(r["orders"]) == 1
    assert assess(r)["rows"][0]["state"] == "stop_reported"
