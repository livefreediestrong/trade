"""Fox (broker agent), Changing Woman (chores/reasoning), events and WSB tied together."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

import pytest

import app as desk
import broker_router as router
import desk_day
import live_agent as agent
import market_events as me
import wsb_monitor as wm
from test_live_agent import live, run_again  # noqa: F401 - fixture reuse
from test_execution_repairs import execution, BASE  # noqa: F401 - fixture reuse


@pytest.fixture(autouse=True)
def calm(monkeypatch):
    """No calendar rows and no WSB data unless a test adds them."""
    monkeypatch.setattr(me, "_state", {"events": [], "sources": {}, "at": __import__("time").time(), "loaded": True, "by_source": {}})
    monkeypatch.setattr(me, "static_fomc", lambda today: [])
    monkeypatch.setattr(wm, "_state", {"threads": {}, "mentions": deque(), "last_poll": 0.0, "last_ok": 0.0,
                                       "last_discovery": 0.0, "coverage_since": None, "error": None,
                                       "comments_read": 0, "loaded": True})


def high_event_now(minutes_from_now=5, title="Testimony - Chairman Kevin Warsh"):
    start = datetime.now(timezone.utc) + timedelta(minutes=minutes_from_now)
    row = me._event("fed", title, start, impact="high", kind="fed_chair")
    me._state["by_source"] = {"fed": [row]}
    return row


def crowd(ticker="TEST", n=60):
    now = datetime.now(timezone.utc).timestamp()
    wm._state["last_ok"] = now
    wm._state["coverage_since"] = now - 3 * 3600
    for i in range(n):
        wm._state["mentions"].append((now - 60, ticker, f"a{i}", "bull", "wsb_daily"))


# ---- the event guard in the live agent ------------------------------------------------

def test_agent_does_not_research_or_buy_in_an_event_window(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    high_event_now(5)
    live.service.tick()
    assert live.calls == [] and not live.sent
    assert live.service.phase == "event_window" and "Warsh" in live.service.message and "exits continue" in live.service.message
    assert live.service.status()["today"]["research"] == 0  # no research budget spent


def test_event_guard_can_be_turned_off(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    high_event_now(5)
    cfg = desk.load_config(); cfg["event_guard_enabled"] = False; desk.save_config(cfg)
    live.cfg["event_guard_enabled"] = False
    live.service.tick()
    assert len(live.sent) == 1


def test_exits_still_run_in_an_event_window(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    live.service.tick()  # buys 2 TEST at 100 before the event
    high_event_now(5)
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setitem(router.__dict__, "get_positions", lambda: {"ok": True, "positions": [{"symbol": "TEST", "qty": 2}]})
    monkeypatch.setattr(desk, "_broker_position_qty", lambda t: (2.0, None))
    monkeypatch.setitem(router.__dict__, "stock_quote", lambda t: {"ok": True, "fresh": True, "price": 99.0, "bid": 99.0,
                                                                   "source": "IBKR", "market_time": now, "received_at": now})
    run_again(live)
    assert live.sent[-1]["side"] == "sell"
    event = live.service.load()["events"][0]
    assert event["status"] == "exit_stop_loss" and event["fill"]["shares"] == 2 and event["exit_reason"] == "stop_loss"


def test_trade_error_blocks_new_agent_entries_but_not_reducing_ones(live):  # noqa: F811
    high_event_now(5)
    cfg = desk.load_config()
    signal = {"id": "s", "source": "live_agent", "ticker": "TEST", "side": "buy",
              "agent_revision": cfg["live_agent"]["revision"], "agent_run_id": cfg["live_agent"]["run_id"],
              "agent_identity": cfg["broker_identity"]}
    assert "Scheduled event" in live.service.trade_error(signal, cfg, 0.0, False)
    assert live.service.trade_error(dict(signal, id="s2", side="sell"), cfg, 0.0, True) is None


def test_agent_fill_is_recorded_for_fox(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    live.service.tick()
    event = live.service.load()["events"][0]
    assert event["side"] == "buy" and event["fill"] == {"shares": 2, "price": 100.0}
    assert event["message"] == "Bought 2 TEST at $100.00"


# ---- WSB crowding caution on new buy ideas -------------------------------------------

def _idea(**over):
    return dict({"ticker": "TEST", "side": "buy", "suggested_shares": 10, "reason": "setup"}, **over)


@pytest.mark.parametrize("action,shares,blocked", [("half", 5, False), ("skip", 10, True), ("note", 10, False), ("off", 10, False)])
def test_wsb_crowding_caution(action, shares, blocked):
    crowd()
    sig = _idea()
    desk._apply_wsb_caution(sig, {"wsb_crowding_action": action, "wsb_crowd_min_mentions": 25})
    assert sig["suggested_shares"] == shares
    assert bool(sig.get("execution_block")) is blocked
    assert ("wsb_crowded" in (sig.get("research_flags") or [])) is (action != "off")
    if action == "half":
        assert sig["advisory_size_mult"] == 0.5


def test_wsb_never_touches_sells_or_uncrowded_or_stale():
    crowd()
    sell = _idea(side="sell")
    desk._apply_wsb_caution(sell, {"wsb_crowding_action": "skip"})
    assert sell["suggested_shares"] == 10 and "execution_block" not in sell
    quiet = _idea(ticker="QUIET")
    desk._apply_wsb_caution(quiet, {"wsb_crowding_action": "skip"})
    assert "execution_block" not in quiet
    wm._state["last_ok"] = 0.0  # stale Reddit data: no caution, nothing blocked
    stale = _idea()
    desk._apply_wsb_caution(stale, {"wsb_crowding_action": "skip"})
    assert "execution_block" not in stale


def test_half_of_one_share_blocks_instead_of_rounding_up():
    crowd()
    sig = _idea(suggested_shares=1)
    desk._apply_wsb_caution(sig, {"wsb_crowding_action": "half"})
    assert sig["suggested_shares"] == 0 and "under one share" in sig["execution_block"]


# ---- the desk-day snapshot -----------------------------------------------------------------

def test_fox_is_off_duty_without_a_policy(execution):  # noqa: F811
    body = desk.app.test_client().get("/api/desk-day", base_url=BASE).get_json()
    assert body["ok"] and body["fox"]["state"] == "off" and "off duty" in body["fox"]["headline"]
    labels = [c["label"] for c in body["woman"]["chores"]]
    assert "Reconcile broker orders" in labels and "Check the calendar" in labels and "Read the WSB threads" in labels
    assert any(n["key"] == "wsb_off" for n in body["woman"]["reasoning"]) or body["wsb"]["configured"]


def test_snapshot_ties_fox_events_and_changing_woman_together(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 25.0)
    live.service.tick()
    row = high_event_now(40, "FOMC Press Conference")
    crowd("TEST")
    snap = desk_day.snapshot(desk)
    fox = snap["fox"]
    assert fox["latest_trade"]["text"] == "Bought 2 TEST at $100.00."
    assert fox["managed"][0]["ticker"] == "TEST" and "Tracking 1 position" in fox["headline"]
    assert "App-managed exits require" in fox["headline"]
    notes = {n["key"].split(":")[0]: n for n in snap["woman"]["reasoning"]}
    assert "FOMC Press Conference" in notes["event_soon"]["text"] and "Fox pauses new entries" in notes["event_soon"]["text"]
    assert notes["wsb_crowded"]["level"] == "caution" and "half size" in notes["wsb_crowded"]["text"]
    assert "min to the close" in notes["close"]["text"] and "sells what he holds 10 min before" in notes["close"]["text"]
    assert snap["events"]["upcoming"][0]["id"] == row["id"] and snap["events"]["upcoming"][0]["guard"]
    # Inside the window Fox is holding and Changing Woman says why.
    high_event_now(5)
    snap = desk_day.snapshot(desk)
    assert snap["fox"]["state"] == "holding" and snap["woman"]["reasoning"][0]["level"] == "block"


def test_market_events_and_wsb_routes(execution):  # noqa: F811
    high_event_now(90)
    client = desk.app.test_client()
    events = client.get("/api/market-events", base_url=BASE).get_json()
    assert events["ok"] and events["events"][0]["when"]
    assert client.get("/api/wsb", base_url=BASE).get_json()["ok"]


# ---- settings -----------------------------------------------------------------------------

@pytest.mark.parametrize('stamp', ['bad', '2026-09-25T12:00:00', 1e100])
def test_bad_saved_timestamps_do_not_break_desk_snapshot(stamp):
    now = datetime.now(timezone.utc)
    assert desk_day._ago(stamp, now) == 'unknown'
    event = {'at': stamp, 'status': 'filled', 'ticker': 'TEST', 'fill': {'shares': 2, 'price': 100}}
    assert desk_day.fox_view({'events': [event]}, None, now)['latest_trade'] is None


def test_future_trade_is_not_announced_as_current():
    now = datetime.now(timezone.utc)
    event = {'at': (now + timedelta(minutes=1)).isoformat(), 'status': 'filled', 'ticker': 'TEST',
             'fill': {'shares': 2, 'price': 100}}
    assert desk_day.fox_view({'events': [event]}, None, now)['latest_trade'] is None
    assert desk_day._ago(event['at'], now) == 'unknown'


@pytest.mark.parametrize('fill', [{'shares': 'bad', 'price': 100}, {'shares': 2, 'price': 'NaN'},
                                  {'shares': 0, 'price': 100}, {'shares': 2, 'price': None}])
def test_invalid_retained_fill_is_never_narrated_as_a_purchase(fill):
    row = desk_day._event_line({'status': 'filled', 'ticker': 'TEST', 'fill': fill})
    assert row['kind'] == 'info'
    assert 'fill details unavailable' in row['text'].lower()
    assert 'Bought' not in row['text']

def test_config_accepts_event_and_wsb_settings(execution):  # noqa: F811
    client = desk.app.test_client()
    reply = client.post("/api/config", base_url=BASE, json={
        "event_guard_enabled": False, "event_guard_before_min": 20, "event_guard_after_min": 30,
        "wsb_crowding_action": "skip", "wsb_crowd_min_mentions": 40,
        "custom_market_events": [{"title": "President address", "start": "2026-09-25 21:00", "impact": "high"}]})
    assert reply.status_code == 200, reply.get_json()
    cfg = desk.load_config()
    assert cfg["event_guard_enabled"] is False and cfg["event_guard_before_min"] == 20 and cfg["wsb_crowd_min_mentions"] == 40
    assert cfg["wsb_crowding_action"] == "skip" and cfg["custom_market_events"][0]["title"] == "President address"
    for bad in ({"wsb_crowding_action": "double"}, {"custom_market_events": [{"title": "x", "start": "later"}]},
                {"event_guard_after_min": 999}):
        assert client.post("/api/config", base_url=BASE, json=bad).status_code == 400


# ---- earnings guard ------------------------------------------------------------------------

def _et_now():
    return datetime.now(me.ET)


@pytest.mark.parametrize("offset,flatten,blocked", [(0, 10, True), (1, 0, True), (1, 10, False), (5, 0, False)])
def test_earnings_guard(offset, flatten, blocked):
    today = _et_now().date()
    day = today + timedelta(days=offset)
    if offset == 1:
        while day.weekday() >= 5:
            day += timedelta(days=1)
    policy = dict(agent.DEFAULTS, flatten_before_close_min=flatten)
    signal = {"ticker": "TEST", "side": "buy", "earnings": {"date": day.isoformat()}}
    assert bool(agent.earnings_block(signal, {}, policy, _et_now())) is blocked
    assert agent.earnings_block(dict(signal, side="sell"), {}, policy) is None
    assert agent.earnings_block(signal, {"event_guard_enabled": False}, policy) is None


def test_agent_holds_a_buy_on_earnings_day(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    def scan(cfg, **kw):
        sig = live.generate(cfg, **kw)
        sig["earnings"] = {"date": _et_now().date().isoformat(), "hour": "amc"}
        return sig
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    live.service.tick()
    assert not live.sent and "earnings today" in live.service.message


def test_option_fills_are_described_as_contracts():
    line = desk_day._event_line({"status": "approved", "ticker": "AAPL", "side": "buy", "asset_type": "OPT",
                                 "option_strategy": "long_call", "fill": {"shares": 2, "price": 3.45}})
    assert line["text"] == "Bought 2 AAPL long_call contracts at $3.45 premium."


def test_wsb_half_size_carries_over_to_option_contracts(live, monkeypatch):  # noqa: F811
    import auto_live_options
    signal = {"ticker": "TEST", "side": "buy", "signal_price": 100.0, "advisory_size_mult": 0.5}
    monkeypatch.setitem(router.__dict__, "get_account", lambda: {"ok": True, "risk_ready": True, "account": {"day_pnl": 0}})
    monkeypatch.setattr(auto_live_options, "risk_ready_error", lambda book: None)
    monkeypatch.setitem(router.__dict__, "get_positions", lambda: {"ok": True, "positions": []})
    monkeypatch.setitem(router.__dict__, "option_chain", lambda t: {"ok": True, "expirations": [], "strikes": []})
    seen = {}
    def build(sig, cfg, **kw):
        return {"asset_type": "OPT", "contracts": 1, "option_intent": "BTO", "right": "C", "expiry": "20261016", "strike": 100}
    monkeypatch.setattr(auto_live_options, "build_option_candidate", build)
    monkeypatch.setitem(router.__dict__, "option_quotes", lambda legs: seen.setdefault("quoted", True) and pytest.fail("must stop before quoting"))
    with pytest.raises(ValueError, match="under one contract"):
        live.service._maybe_convert_to_option(signal, desk.load_config(), {}, {})
