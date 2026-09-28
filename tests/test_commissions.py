"""2026-09-28 strategy review: commissions, fail-closed cost checks, exits while paused,
stuck orders, the evidence lock on automatic buys and the tradeable universe."""
from datetime import datetime, timedelta, timezone

import pytest

import app as desk
import broker_router as router
import live_agent as agent
import strategy_scorecard
from test_execution_repairs import execution  # noqa: F401 - fixture
from test_live_agent import live, run_again  # noqa: F401 - fixtures
from test_trade_intelligence import entered, _hold  # noqa: F401 - fixtures

FIXED = {"fee_bps": 1.0, "slip_bps": 5.0}  # IBKR Pro Fixed commission defaults apply


def test_ibkr_fixed_commission_minimum_and_cap():
    assert desk._order_commission_usd(FIXED, 1, 3.0, "buy") == pytest.approx(0.03)  # 1% cap beats $1 minimum
    assert desk._order_commission_usd(FIXED, 400, 50.0, "buy") == pytest.approx(2.0)  # $0.005/share
    assert desk._order_commission_usd(FIXED, 100, 50.0, "buy") == pytest.approx(1.0)  # $1 minimum
    assert desk._order_commission_usd(FIXED, 1, 3.0, "sell") == pytest.approx(0.05)  # + regulatory fees


def test_one_cheap_share_no_longer_passes_the_cost_check():
    # Review worked example: 1 share at $3.00, 2% stop, 2.5R target.
    edge = desk._edge_after_costs(3.0, 0.06, 0.15, FIXED, quote={"bid": 2.995, "ask": 3.005}, shares=1)
    assert edge["commission_per_share_usd"] == pytest.approx(0.08)
    assert edge["net_reward_risk"] < 1.2
    # The same trade at a size where commissions are small still passes.
    big = desk._edge_after_costs(100.0, 2.0, 5.0, FIXED, quote={"bid": 99.99, "ask": 100.01}, shares=400)
    assert big["net_reward_risk"] > 1.2


def test_cheap_stock_stop_never_rounds_to_zero():
    preset = desk.get_preset("high")
    stop, target = desk._stop_target_distances(0.50, None, {}, preset)
    assert stop >= 0.01 and target > stop


def test_final_gate_refuses_unprofitable_and_incomplete_ideas():
    cfg = dict(FIXED, min_net_reward_risk=1.2)
    assert "No order was sent" in desk._final_agent_edge_error(
        {"stop": 2.94, "target": 3.15, "quote": {"bid": 2.99, "ask": 3.01}}, cfg, 1, 3.0)
    assert desk._final_agent_edge_error({"stop": None, "target": 3.2}, cfg, 1, 3.0)
    assert desk._final_agent_edge_error(
        {"stop": 98.0, "target": 105.0, "quote": {"bid": 99.99, "ask": 100.01}}, cfg, 400, 100.0) is None


def test_breakeven_stop_covers_round_trip_commissions():
    row = {"entry": 3.0, "stop": 2.94, "target": 3.15, "risk_per_share": 0.06, "cost_per_share": 0.02}
    _, updates = agent.exit_decision(row, 3.07, datetime.now(timezone.utc), {"breakeven_after_r": 1.0}, 120.0)
    assert updates["breakeven"] and updates["stop"] == pytest.approx(3.02)


def _exit_signal(cfg, **extra):
    saved = cfg["live_agent"]
    return dict(source="live_agent", agent_exit="stop_loss", ticker="TEST", agent_revision=saved["revision"],
                agent_run_id=saved.get("run_id"), agent_identity=cfg["broker_identity"], **extra)


def test_pausing_keeps_stock_exits_but_live_trading_off_stops_them(live):
    cfg = desk.load_config()
    cfg["live_agent"]["enabled"] = False
    assert agent.authorization_error(_exit_signal(cfg), cfg) is None
    assert agent.authorization_error(dict(_exit_signal(cfg), agent_exit=None), cfg)  # no new risk
    assert agent.authorization_error(_exit_signal(cfg, agent_option_position_id="x"), cfg)
    assert agent.authorization_error(dict(_exit_signal(cfg), agent_identity={"account_id": "OTHER"}), cfg)
    cfg["mode"] = "manual"  # Live trading OFF
    assert agent.authorization_error(_exit_signal(cfg), cfg)


def test_paused_agent_still_sells_at_its_stop(entered, monkeypatch):
    cfg = desk.load_config()
    cfg["live_agent"]["enabled"] = False
    cfg["session_active"] = False
    desk.save_config(cfg)
    _hold(monkeypatch, 2, 99.0)
    run_again(entered)
    assert entered.sent[-1]["side"] == "sell" and len(entered.sent) == 2


def test_stuck_order_on_another_stock_does_not_freeze_the_stop(entered, monkeypatch):
    ledger = desk.load_ledger()
    ledger["pending_broker_orders"] = [{"order_id": "stuck", "signal": {"id": "s", "ticker": "OTHER"}}]
    desk.save_ledger(ledger)
    monkeypatch.setattr(desk, "_reconcile_pending_broker_orders", lambda: None)
    _hold(monkeypatch, 2, 99.0)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert entered.sent[-1]["side"] == "sell"


def test_stuck_order_on_the_same_stock_still_waits(entered, monkeypatch):
    ledger = desk.load_ledger()
    ledger["pending_broker_orders"] = [{"order_id": "stuck", "signal": {"id": "s", "ticker": "TEST"}}]
    desk.save_ledger(ledger)
    _hold(monkeypatch, 2, 99.0)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert all(order["side"] == "buy" for order in entered.sent)


def _calls(n_days, per_day, bps, tickers=12):
    start = datetime(2026, 9, 1, 15, tzinfo=timezone.utc)
    return [{"outcome_status": "scored", "side": "buy", "setup": "PASS|fair", "ticker": f"T{(d * per_day + i) % tickers}",
             "outcome_executable_move_bps": bps + (i % 3), "ts": (start + timedelta(days=d, minutes=i)).isoformat()}
            for d in range(n_days) for i in range(per_day)]


def test_promotion_needs_enough_independent_evidence_that_beats_commissions():
    now = datetime(2026, 9, 28, tzinfo=timezone.utc)
    strong = strategy_scorecard.promotion_status(_calls(12, 6, 400), hurdle_bps=245, now=now)
    assert strong["passed"] and strong["calls"] == 72 and strong["sessions"] == 12
    small_edge = strategy_scorecard.promotion_status(_calls(12, 6, 20), hurdle_bps=245, now=now)
    assert not small_edge["passed"]  # positive, but not after commissions
    few_days = strategy_scorecard.promotion_status(_calls(3, 30, 400), hurdle_bps=245, now=now)
    assert not few_days["passed"] and any("trading days" in m for m in few_days["missing"])
    unknown = strategy_scorecard.promotion_status(_calls(12, 6, 400), hurdle_bps=float("inf"), now=now)
    assert not unknown["passed"]


@pytest.mark.promotion_gate
def test_locked_evidence_blocks_new_buys_but_not_exits(live, monkeypatch):
    monkeypatch.setattr(live.service, "_share_price_cap", lambda cfg: 4.40)
    cfg = desk.load_config()
    entry = dict(_exit_signal(cfg), agent_exit=None)
    assert "Automatic buys are locked" in live.service.trade_error(entry, cfg, 0.0, reducing=False)
    assert "locked" not in str(live.service.trade_error(_exit_signal(cfg), cfg, 0.0, reducing=True) or "")


def test_universe_keeps_only_listed_non_leveraged_symbols(monkeypatch):
    monkeypatch.setattr(agent, "_listed_symbols", lambda: {"AAPL", "PLUG", "TQQQ"})
    assert agent._tradeable_listed(["AAPL", "PLUG", "TQQQ", "SIXB", "FZROX", "REEMF"]) == ["AAPL", "PLUG"]
    monkeypatch.setattr(agent, "_listed_symbols", lambda: None)  # directory unavailable
    assert agent._tradeable_listed(["AAPL", "TQQQ"]) == ["AAPL"]


def test_radar_picks_join_only_when_radar_is_on(monkeypatch):
    import market_radar
    monkeypatch.setattr(market_radar, "get_cached", lambda: {"movers": [{"ticker": "NEWCO"}]})
    monkeypatch.setattr(agent, "_listed_symbols", lambda: None)
    assert "NEWCO" not in agent.research_universe({"watchlist": ["AAPL"], "radar_enabled": False})
    assert "NEWCO" in agent.research_universe({"watchlist": ["AAPL"], "radar_enabled": True})
