"""Profit/loss intelligence: cost-aware entries, evidence gate, give-back guard, agent exits.

The inherited execution fixture denies network; no Gateway order can be sent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app as desk
import broker_router as router
import live_agent as agent
from test_live_agent import live, run_again  # noqa: F401 - fixture reuse
from test_execution_repairs import execution  # noqa: F401 - fixture reuse

NOW = datetime(2026, 9, 24, 15, 0, tzinfo=timezone.utc)
POLICY = dict(agent.DEFAULTS)


def row(**over):
    base = {"shares": 2, "entry": 100.0, "stop": 99.2, "target": 101.6, "risk_per_share": 0.8,
            "breakeven": False, "exit_at": None}
    return dict(base, **over)


# ---- live agent exit rules -------------------------------------------------

@pytest.mark.parametrize("bid,expected", [(99.2, "stop_loss"), (98.0, "stop_loss"), (101.6, "take_profit"),
                                          (100.5, None)])
def test_exit_decision_stop_and_target(bid, expected):
    reason, _ = agent.exit_decision(row(), bid, NOW, dict(POLICY, breakeven_after_r=0), 120)
    assert reason == expected


def test_breakeven_stop_moves_after_one_r_then_exits_at_entry():
    reason, updates = agent.exit_decision(row(), 100.8, NOW, POLICY, 120)
    assert reason is None and updates == {"stop": 100.0, "breakeven": True}
    reason, _ = agent.exit_decision(row(**updates), 100.0, NOW, POLICY, 120)
    assert reason == "breakeven_stop"
    # Below one R of profit the stop stays where it was planned.
    assert agent.exit_decision(row(), 100.7, NOW, POLICY, 120) == (None, {})


def test_max_hold_and_end_of_day():
    past = (NOW - timedelta(minutes=1)).isoformat()
    assert agent.exit_decision(row(exit_at=past), 100.1, NOW, POLICY, 120)[0] == "max_hold"
    assert agent.exit_decision(row(), 100.1, NOW, POLICY, 9)[0] == "end_of_day"
    assert agent.exit_decision(row(), 100.1, NOW, dict(POLICY, flatten_before_close_min=0), 1)[0] is None
    assert agent.exit_decision(row(), 100.1, NOW, POLICY, None)[0] is None


@pytest.mark.parametrize("reason,limit", [("stop_loss", 98.51), ("end_of_day", 98.51), ("take_profit", 98.76)])
def test_exit_limits_sit_below_the_bid(reason, limit):
    signal = {"id": "x", "ticker": "TEST", "side": "sell", "suggested_shares": 2, "agent_exit": reason,
              "quote": {"price": 99.0}}
    order = agent._exit_terms(signal, POLICY, 99.0)
    assert order["type"] == "limit" and order["limit"] == limit and order["shares"] == 2


def test_exit_terms_refuse_to_buy():
    with pytest.raises(ValueError):
        agent._exit_terms({"side": "buy", "suggested_shares": 1, "quote": {"price": 10}}, POLICY, 10.0)


def test_policy_saved_before_exits_existed_still_validates():
    old = {k: v for k, v in agent.DEFAULTS.items() if k not in agent._ADDED_FIELDS}
    policy = agent.validate(old)
    assert policy["protective_exits"] is True and policy["flatten_before_close_min"] == 10


@pytest.mark.parametrize("field,value", [("protective_exits", "yes"), ("breakeven_after_r", -1),
                                         ("max_hold_min", 1.5), ("flatten_before_close_min", 500)])
def test_invalid_exit_policy(field, value):
    with pytest.raises(ValueError):
        agent.validate(dict(agent.DEFAULTS, **{field: value}))


def _hold(monkeypatch, qty, bid, **quote):
    monkeypatch.setitem(router.__dict__, "get_positions", lambda: {"ok": True, "positions": [
        {"symbol": "TEST", "qty": qty, "side": "long"}]})
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (float(qty), None))
    monkeypatch.setitem(router.__dict__, "stock_quote", lambda ticker: dict({
        "ok": True, "fresh": True, "price": bid, "bid": bid, "ask": bid + .02, "source": "IBKR",
        "market_time": datetime.now(timezone.utc).isoformat(),
        "received_at": datetime.now(timezone.utc).isoformat()}, **quote))


@pytest.fixture
def entered(live, monkeypatch):  # noqa: F811
    monkeypatch.setattr(agent, "_minutes_to_close", lambda now: 120.0)
    live.service.tick()
    assert len(live.sent) == 1 and live.sent[0]["side"] == "buy"
    managed = live.service.load()["managed"]["TEST"]
    assert managed["shares"] == 2 and managed["entry"] == 100.0
    assert managed["stop"] < 100.0 < managed["target"]
    return live


def test_agent_sells_its_position_when_the_stop_is_hit(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.0)
    run_again(entered)
    exit_order = entered.sent[-1]
    assert len(entered.sent) == 2 and exit_order["side"] == "sell" and exit_order["shares"] == 2
    assert exit_order["type"] == "limit" and exit_order["limit"] == 98.51
    assert "managed" not in entered.service.load() or "TEST" not in entered.service.load()["managed"]
    assert entered.service.load()["events"][0]["status"] == "exit_stop_loss"


def test_exit_waits_for_a_fresh_real_time_quote(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.0, delayed=True)
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert all(order["side"] == "buy" for order in entered.sent)
    assert "TEST" in entered.service.load()["managed"]


def test_exit_never_goes_to_a_different_account(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.0)
    monkeypatch.setitem(router.__dict__, "verify_execution_context", lambda: {
        "ok": True, "identity": dict(entered.identity, account_id="OTHER")})
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert all(order["side"] == "buy" for order in entered.sent)


def test_position_sold_elsewhere_is_no_longer_managed(entered, monkeypatch):
    _hold(monkeypatch, 0, 99.0)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert "TEST" not in entered.service.load()["managed"] and len(entered.sent) == 1


def test_exit_outside_the_symbol_list_is_allowed(entered):
    cfg = desk.load_config()
    signal = {"source": "live_agent", "ticker": "ZZZZ", "side": "sell", "agent_exit": "stop_loss",
              "agent_revision": cfg["live_agent"]["revision"], "agent_run_id": cfg["live_agent"]["run_id"],
              "agent_identity": cfg["broker_identity"]}
    assert agent.authorization_error(signal, cfg) is None
    entry = dict(signal, side="buy"); entry.pop("agent_exit")
    assert "outside" in agent.authorization_error(entry, cfg)


# ---- entry quality: costs, evidence, confidence ----------------------------

def test_confidence_is_deterministic():
    values = {desk._confidence_for_verdict("PASS", "early") for _ in range(20)}
    assert len(values) == 1


def test_edge_after_costs():
    cfg = {"fee_bps": 1.0, "slip_bps": 5.0}
    edge = desk._edge_after_costs(100.0, 1.0, 2.0, cfg, quote={"bid": 99.99, "ask": 100.01})
    assert edge["round_trip_cost_usd"] == pytest.approx(0.14)
    assert edge["net_reward_risk"] == pytest.approx((2 - 0.14) / 1.14, abs=1e-3)
    assert 0 < edge["breakeven_win_rate"] < 0.5
    thin = desk._edge_after_costs(100.0, 0.1, 0.12, cfg, intraday={"spread_atr": 0.05, "atr_usd": 0.1})
    assert thin["net_reward_risk"] < 0 and thin["breakeven_win_rate"] == 1.0


def _scan(monkeypatch, atr):
    import screener_logic

    cfg = desk.load_config()
    cfg.update(watchlist=["AAPL"], llm_on_scan=False, market_regime_gate_enabled=False, min_net_reward_risk=1.2)
    monkeypatch.setattr(screener_logic, "analyze_ticker", lambda _: {
        "ticker": "AAPL", "price": 100.0, "verdict": "PASS", "verdict_text": "volume and structure",
        "entry_quality": {"label": "early"}, "intraday": {"atr_usd": atr, "spread_atr": 0.05},
        "volume": {"rel_vol": 2.0, "session_is_today": True}, "checks": {}, "sources": []})
    return desk.generate_scan_signal(cfg, force=True)


def test_scan_blocks_entries_whose_edge_is_eaten_by_costs(monkeypatch):
    sig = _scan(monkeypatch, atr=0.2)
    assert sig["verdict"] == "WATCH" and "thin_edge_after_costs" in sig["research_flags"]


def test_scan_keeps_entries_with_room_after_costs(monkeypatch):
    sig = _scan(monkeypatch, atr=2.0)
    assert "thin_edge_after_costs" not in (sig.get("research_flags") or [])
    assert sig["verdict"] == "PASS" and sig["net_reward_risk"] >= 1.2


def _record(samples, rate, move):
    return {"setup": "PASS/early", "side_stats": {"buy": {"samples": samples, "helped_rate": rate, "avg_move_bps": move}}}


@pytest.mark.parametrize("record,blocked", [
    (_record(20, 0.30, -12.0), True),
    (_record(5, 0.10, -40.0), False),      # too few scored trades to judge
    (_record(20, 0.55, 8.0), False),       # winning record
    (_record(20, 0.30, 4.0), False),       # low hit rate but positive average after costs
])
def test_evidence_gate_needs_a_losing_record(monkeypatch, record, blocked):
    monkeypatch.setattr(desk.lessons, "track_record", lambda *a, **k: record)
    sig = {"ticker": "AAPL", "side": "buy", "verdict": "PASS", "lateness_label": "early", "breakeven_win_rate": 0.4}
    cfg = dict(desk.load_config(), evidence_gate_enabled=True, evidence_min_samples=12)
    assert bool(desk._evidence_block(sig, cfg)) is blocked
    assert desk._evidence_block(sig, dict(cfg, evidence_gate_enabled=False)) is None


# ---- give-back guard -------------------------------------------------------

def _gate(cfg, peak, day_pnl, equity=100000.0):
    ledger = {"broker_daily": {desk._today_str(): {"trades": 0, "peak_pnl": peak}}}
    return desk._broker_risk_gate(cfg, ledger, day_pnl, equity)


def test_giveback_guard_protects_a_meaningful_peak():
    cfg = dict(desk.load_config(), giveback_stop_pct=50.0, daily_profit_target_usd=None,
               max_session_loss_usd=None, kill_switch={})
    preset = desk.get_preset(cfg.get("risk_preset"))
    peak = 100000.0 * float(preset["max_daily_loss_pct"]) / 100.0  # well above the arming floor
    ok, reason = _gate(cfg, peak, peak * 0.4)
    assert not ok and "Protecting today's gains" in reason
    assert _gate(cfg, peak, peak * 0.6)[0]
    assert _gate(dict(cfg, giveback_stop_pct=0), peak, peak * 0.1)[0]
    assert _gate(cfg, 1.0, -0.5)[0]  # a tiny peak does not arm the guard


def test_day_pnl_peak_only_rises(monkeypatch, tmp_path):
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL"):
        monkeypatch.setattr(desk, name + "_PATH", tmp_path / (name.lower() + ".json"))
    desk._CORRUPT_PATHS.clear()
    for pnl in (120.0, 80.0, 150.5, -20.0):
        desk._note_day_pnl_peak(pnl)
    assert desk.load_ledger()["broker_daily"][desk._today_str()]["peak_pnl"] == 150.5


def test_short_holding_is_never_sold_by_an_exit(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.0)
    monkeypatch.setitem(router.__dict__, "get_positions", lambda: {"ok": True, "positions": [
        {"symbol": "TEST", "qty": 2, "side": "short"}]})
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert len(entered.sent) == 1 and "TEST" not in entered.service.load()["managed"]


def test_refused_exit_waits_before_trying_again(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.0)
    attempts = []
    monkeypatch.setattr(desk, "_ingest_one_signal", lambda sig, **kw: attempts.append(sig) or {
        "ok": False, "reject_reason": "blocked for the test"})
    cfg, policy = desk.load_config(), agent.validate(entered.policy)
    assert entered.service._manage_exits(cfg, policy) is True
    assert entered.service._manage_exits(cfg, policy) is False
    assert len(attempts) == 1 and entered.service.load()["managed"]["TEST"]["retry_after"]
