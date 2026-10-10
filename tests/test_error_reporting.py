"""Optional Sentry reporting stays off by default and strips account data."""
from __future__ import annotations

import error_reporting as er


def test_off_without_dsn(monkeypatch):
    monkeypatch.setattr(er.os, "environ", {})
    monkeypatch.setitem(er._state, "enabled", False)
    assert er.init() is False and er.status()["enabled"] is False


def test_scrub_drops_request_data_locals_and_account_numbers():
    event = {
        "request": {"method": "POST", "url": "http://127.0.0.1:5056/api/config?token=abc",
                    "data": {"account": "U1234567"}, "headers": {"Cookie": "x"}, "cookies": {"a": "b"}},
        "breadcrumbs": {"values": [{"message": "DU7654321"}]},
        "user": {"ip_address": "1.2.3.4"}, "extra": {"cfg": {"broker_identity": "U1234567"}},
        "logentry": {"message": "order for DU7654321 failed", "params": ["U1234567"]},
        "exception": {"values": [{"value": "Account U1234567 rejected Bearer AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
                                  "stacktrace": {"frames": [{"function": "place", "vars": {"acct": "U1234567"}}]}}]},
    }
    out = er.scrub_event(event)
    assert out["request"] == {"method": "POST", "url": "http://127.0.0.1:5056/api/config"}
    assert "breadcrumbs" not in out and "user" not in out and "extra" not in out
    assert out["logentry"] == {"message": "order for [account] failed"}
    value = out["exception"]["values"][0]["value"]
    assert "U1234567" not in value and "AAAA" not in value and "[account]" in value
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert "U1234567" not in repr(out) and "DU7654321" not in repr(out)
