"""Exercise real agent -> desk risk -> durable intent -> fake broker transport.

The inherited fixture denies network. No Gateway order can be sent by this suite.
"""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

import pytest

import app as desk
import broker_router as router
import broker_ibkr as ibkr
import data_sources as ds
import live_agent as agent
from test_execution_repairs import execution, BASE


@pytest.fixture
def live(execution, monkeypatch, tmp_path):
    cfg, _, sent = execution
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    monkeypatch.setattr(agent.paper_loop, "is_rth", lambda *args: True)
    identity = dict(cfg["broker_identity"], broker="ibkr", client_id=37, paper_mode=True)
    policy = dict(agent.DEFAULTS, symbols=["TEST", "OTHER"], max_order_usd=250.)
    # Eval rotates the watchlist; order allow-list stays policy.symbols.
    cfg.update(mode="auto_live", paper_research_enabled=False, broker_identity=identity,
               watchlist=["TEST", "OTHER"], watchlist_focus="all",
               live_agent={"policy": policy, "revision": "policy-1", "run_id": "run-1", "enabled": True})
    desk.save_config(cfg)
    monkeypatch.setitem(router.__dict__, "public_status", lambda: {"broker": "ibkr"})
    monkeypatch.setitem(router.__dict__, "verify_execution_context", lambda: {"ok": True, "identity": copy.deepcopy(identity)})
    monkeypatch.setitem(router.__dict__, "get_open_orders", lambda: {"ok": True, "orders": []})
    monkeypatch.setitem(router.__dict__, "wait_for_fill", lambda *a, **k: {
        "state": "filled", "terminal": True, "filled_qty": sent[-1]["shares"], "filled_avg_price": 100.})
    monkeypatch.setitem(router.__dict__, "reconcile_after_timeout", lambda *a, **k: pytest.fail("Unexpected cancellation path"))
    monkeypatch.setattr(desk.llm_trader, "model_cost_today", lambda: {"model_usd": 0.})
    def quote(ticker):
        desk._QUOTE_SNAPSHOTS[ticker] = ds.quote_snapshot(100, datetime.now(timezone.utc), "ibkr_live")
        return 100.
    monkeypatch.setattr(desk, "fetch_last_price", quote)
    service = agent.LiveAgent(desk)
    monkeypatch.setattr(desk, "_live_agent", service)
    calls = []
    def generate(cfg, *, ticker, **kwargs):
        calls.append(ticker)
        now = datetime.now(timezone.utc)
        return {"id": "decision-"+str(len(calls)), "ticker": ticker, "side": "buy", "suggested_shares": 10,
                "signal_price": 100., "confidence": .8, "verdict": "PASS", "lateness_label": "early",
                "ts": now.isoformat(), "quote": ds.quote_snapshot(100, now, "ibkr_live"), "llm_model": "real-model"}
    monkeypatch.setattr(desk, "generate_scan_signal", generate)
    return NS(cfg=cfg, policy=policy, identity=identity, sent=sent, service=service, calls=calls, generate=generate)


def run_again(live):
    raw = live.service.load(); raw["next_at"] = None; live.service.save(raw)
    live.service.tick()


def test_agent_research_to_limit_order_and_confirmed_fill(live):
    live.service.tick()
    assert live.calls == ["TEST"]
    assert len(live.sent) == 1
    order = live.sent[0]
    assert order["type"] == "limit" and order["limit"] == 100.05 and order["shares"] == 2
    assert order["position_intent"] == "buy" and order["agent_policy"]["revision"] == "policy-1"
    assert desk.load_ledger()["broker_fills"][0]["shares"] == 2
    assert live.service.status()["today"] == {"research": 1, "orders": 1}
    assert not desk.load_ledger().get("fills")


def test_restart_retains_cadence_counters_and_rotation(live):
    live.service.tick()
    restarted = agent.LiveAgent(desk); restarted.tick()
    assert live.calls == ["TEST"] and restarted.status()["today"]["orders"] == 1
    run_again(live)
    assert live.calls == ["TEST", "OTHER"] and len(live.sent) == 2


@pytest.mark.parametrize("field,value", [("max_order_usd", float("nan")), ("max_orders_per_day", True),
                                         ("interval_sec", 30.5), ("order_type", "stop"), ("symbols", ["SPY", "<svg>"])])
def test_invalid_policy(field, value):
    with pytest.raises(ValueError): agent.validate(dict(agent.DEFAULTS, **{field: value}))


def test_policy_save_pauses_and_rejects_stale_revision(live):
    client = desk.app.test_client()
    before = copy.deepcopy(desk.load_config())
    reply = client.post("/api/live-agent/policy", base_url=BASE, json={"policy": live.policy, "revision": "policy-1"})
    assert reply.status_code == 200
    cfg = desk.load_config()
    assert not cfg["live_agent"]["enabled"] and cfg["mode"] == before["mode"]
    assert cfg["broker_identity"] == before["broker_identity"] and not live.sent
    assert client.post("/api/live-agent/policy", base_url=BASE, json={"policy": live.policy, "revision": "policy-1"}).status_code == 409
    assert desk._scheduled_scan_config(cfg) is None


def test_activation_requires_exact_account_revision_and_confirmation(live):
    client = desk.app.test_client()
    body = {"revision": "policy-1", "identity": live.identity, "confirm": "PAPER"}
    for patch in ({"confirm": "REAL"}, {"identity": dict(live.identity, account_id="OTHER")}, {"revision": "old"}):
        assert client.post("/api/live-agent/start", base_url=BASE, json=dict(body, **patch)).status_code in (400, 409)
    assert not live.sent
    result = client.post("/api/live-agent/start", base_url=BASE, json=body)
    assert result.status_code == 200 and not live.sent  # Starts scheduler policy, never places in HTTP route.
    assert desk.load_config()["live_agent"]["run_id"] != "run-1"


@pytest.mark.parametrize("revoke", ["pause", "account", "revision", "session"])
def test_changes_during_research_discard_decision(live, monkeypatch, revoke):
    def scan(cfg, **kw):
        sig = live.generate(cfg, **kw)
        changed = desk.load_config()
        if revoke == "pause": changed["live_agent"]["enabled"] = False
        if revoke == "account": changed["broker_identity"]["account_id"] = "OTHER"
        if revoke == "revision": changed["live_agent"]["revision"] = "new"
        if revoke == "session": changed["session_active"] = False
        desk.save_config(changed)
        return sig
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    live.service.tick()
    assert not live.sent and live.service.phase == "discarded"


def test_pause_during_account_checks_blocks_submission(live, monkeypatch):
    def account():
        changed = desk.load_config(); changed["live_agent"]["enabled"] = False; desk.save_config(changed)
        return 0., 100000., None
    monkeypatch.setattr(desk, "_broker_day_pnl", account)
    live.service.tick()
    assert not live.sent and "paused" in live.service.message


@pytest.mark.parametrize("change", ["hold", "watch", "confidence", "mock", "stale", "future", "budget", "pnl", "loss", "tiny"])
def test_unqualified_decisions_never_submit(live, monkeypatch, change):
    def scan(cfg, **kw):
        sig = live.generate(cfg, **kw)
        if change == "hold": sig.update(side="hold", abstain=True)
        if change == "watch": sig["verdict"] = "WATCH"
        if change == "confidence": sig["confidence"] = .1
        if change == "mock": sig["llm_model"] = "mock-model"
        if change in ("stale", "future"):
            sig["quote"]["market_time"] = (datetime.now(timezone.utc)+timedelta(seconds=-40 if change == "stale" else 40)).isoformat()
        return sig
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    if change == "budget": monkeypatch.setattr(desk.llm_trader, "model_cost_today", lambda: {"model_usd": 999})
    if change == "pnl": monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (None, 100000., "No daily P&L"))
    if change == "loss": monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (-21., 100000., None))
    if change == "tiny":
        cfg = desk.load_config(); cfg["live_agent"]["policy"]["max_order_usd"] = 10.; desk.save_config(cfg)
    live.service.tick()
    assert not live.sent


def test_market_order_and_sell_only_available_holdings(live, monkeypatch):
    cfg = desk.load_config(); cfg["live_agent"]["policy"]["order_type"] = "market"; desk.save_config(cfg)
    def scan(cfg, **kw): return dict(live.generate(cfg, **kw), side="sell")
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (1., None))
    live.service.tick()
    assert len(live.sent) == 1 and live.sent[0]["type"] == "market" and live.sent[0]["shares"] == 1
    assert live.sent[0]["position_intent"] == "sell"


def test_uncertain_submission_is_not_retried_after_restart(live, monkeypatch):
    def submit(order):
        live.sent.append(order)
        raise TimeoutError("Acknowledgement lost")
    monkeypatch.setattr(desk, "live_broker_place_order", submit)
    live.service.tick()
    assert desk.load_ledger()["pending_broker_orders"] and len(live.sent) == 1
    agent.LiveAgent(desk).tick(); run_again(live)
    assert len(live.sent) == 1 and live.service.phase == "reconciling"


def test_order_and_research_limits_survive_restart_and_account_transport_change(live):
    cfg = desk.load_config(); cfg["live_agent"]["policy"]["max_orders_per_day"] = 1; desk.save_config(cfg)
    live.service.tick()
    cfg = desk.load_config(); cfg["broker_identity"]["client_id"] = 999; desk.save_config(cfg)
    run_again(live)
    assert len(live.sent) == 1 and live.service.phase == "daily_limit"
    assert agent.LiveAgent(desk).status()["today"]["orders"] == 1


@pytest.mark.parametrize("raw", ['{"days": []}', '{"days": {}, "attempts": {}, "events": [], "cursor": false}', '{broken'])
def test_corrupt_state_blocks_without_replacing_it(live, raw):
    live.service.path.write_text(raw, encoding="utf-8")
    live.service.tick()
    assert not live.sent and live.service.phase == "error"
    assert live.service.path.read_text(encoding="utf-8") == raw


def test_out_of_session_agent_waits_even_if_legacy_rth_is_off(live, monkeypatch):
    monkeypatch.setattr(agent.paper_loop, "is_rth", lambda *a: False)
    live.service.tick()
    assert not live.calls and not live.sent and live.service.phase == "waiting_for_market"


def test_final_ibkr_position_guard_also_applies_to_agent_orders():
    contract = NS(secType="STK", currency="USD", conId=1, primaryExchange="NASDAQ", symbol="TEST")
    ib = NS(positions=lambda account: [], openTrades=lambda: [])
    order = {"position_intent": "sell", "side": "sell", "shares": 1, "agent_policy": {"revision": "p", "run_id": "r"}}
    assert "sell" in ibkr._position_intent_error(ib, contract, order, {"account_id": "A"}).lower()
    contract.primaryExchange = "UNVERIFIED"
    assert "verified US-listed" in ibkr._position_intent_error(ib, contract, order, {"account_id": "A"})


def test_agent_cannot_be_bypassed_by_legacy_auto_signal(live):
    sig = live.generate(live.cfg, ticker="TEST")
    sig["workspace"] = "live"
    result = desk.execute_gated_broker_or_paper(sig, live.cfg, source="auto_live", via="auto_live")
    assert not result["ok"] and not live.sent and "outside its policy" in result["error"]


@pytest.mark.parametrize("fails", [False, True])
def test_failed_and_empty_scans_consume_one_interval(live, monkeypatch, fails):
    def scan(*a, **kw):
        live.calls.append(kw["ticker"])
        if fails: raise TimeoutError("Provider down")
    monkeypatch.setattr(desk, "generate_scan_signal", scan)
    live.service.tick(); live.service.tick(); agent.LiveAgent(desk).tick()
    assert live.calls == ["TEST"] and not live.sent
    assert live.service.status()["today"]["research"] == 1


def test_partial_limit_fill_remains_tracked_and_cannot_repeat(live, monkeypatch):
    monkeypatch.setitem(router.__dict__, "wait_for_fill", lambda *a, **k: {
        "state": "partially_filled", "terminal": False, "filled_qty": 1, "filled_avg_price": 100.})
    live.service.tick()
    assert live.service.phase == "broker_pending" and "Partial fill" in live.service.message
    assert desk.load_ledger()["broker_fills"][0]["shares"] == 1
    run_again(live)
    assert len(live.sent) == 1 and live.service.phase == "reconciling"


def test_consumed_decision_id_cannot_be_submitted_twice(live):
    live.service.tick()
    signal = copy.deepcopy(next(s for s in desk.load_signals() if s.get("source") == "live_agent"))
    result = desk.execute_gated_broker_or_paper(signal, desk.load_config(), source="auto_live", via="auto_live")
    assert not result["ok"] and "already reserved" in result["error"]
    assert len(live.sent) == 1


def test_new_activation_revokes_an_old_decision(live):
    signal = live.generate(live.cfg, ticker="TEST")
    signal.update(source="live_agent", agent_revision="policy-1", agent_run_id="run-1", agent_identity=live.identity)
    client = desk.app.test_client()
    assert client.post("/api/live-agent/pause", base_url=BASE, json={}).status_code == 200
    assert client.post("/api/live-agent/start", base_url=BASE, json={
        "revision": "policy-1", "identity": live.identity, "confirm": "PAPER"}).status_code == 200
    assert "authorization changed" in agent.authorization_error(signal, desk.load_config())
    assert not live.sent


def test_start_requires_genuine_daily_pnl(live, monkeypatch):
    monkeypatch.setattr(desk, "_broker_day_pnl", lambda: (None, 100000., "Missing daily P&L"))
    cfg = desk.load_config(); cfg["live_agent"]["enabled"] = False; desk.save_config(cfg)
    reply = desk.app.test_client().post("/api/live-agent/start", base_url=BASE, json={
        "revision": "policy-1", "identity": live.identity, "confirm": "PAPER"})
    assert reply.status_code == 400 and not desk.load_config()["live_agent"]["enabled"] and not live.sent


def test_research_cap_and_model_call_revocation(live, monkeypatch):
    cfg = desk.load_config(); cfg["live_agent"]["policy"]["max_research_per_day"] = 1; desk.save_config(cfg)
    monkeypatch.setattr(desk, "generate_scan_signal", lambda *a, **k: None)
    live.service.tick(); run_again(live)
    assert live.service.phase == "daily_limit" and not live.sent


def test_short_cover_and_no_new_short_entry(live, monkeypatch):
    monkeypatch.setattr(desk, "_broker_position_qty", lambda ticker: (-1., None))
    live.service.tick()
    assert live.sent[0]["position_intent"] == "cover" and live.sent[0]["shares"] == 1
    monkeypatch.setattr(desk, "generate_scan_signal", lambda cfg, **kw: dict(live.generate(cfg, **kw), side="sell"))
    run_again(live)
    assert len(live.sent) == 1 and "never opens short" in live.service.message


def test_research_universe_uses_watchlist_not_order_filter():
    cfg = {"watchlist": ["AAPL", "NVDA", "BTC", "USD", "SPY"], "watchlist_focus": "liquid"}
    policy = dict(agent.DEFAULTS, symbols=["SPY"])
    symbols = agent.research_universe(cfg, policy)
    assert "SPY" in symbols and "AAPL" in symbols and "NVDA" in symbols
    assert "BTC" not in symbols and "USD" not in symbols
    assert symbols != ["SPY"]


def test_order_universe_matches_research_universe():
    cfg = {"watchlist": ["AAPL", "NVDA", "BTC", "USD", "SPY"], "watchlist_focus": "liquid"}
    policy = dict(agent.DEFAULTS, symbols=["SPY"])  # saved hint ignored for live orders
    assert agent.order_universe(cfg, policy) == agent.research_universe(cfg, policy)
    assert "AAPL" in agent.order_universe(cfg, policy)
    assert "BTC" not in agent.order_universe(cfg, policy)


def test_non_spy_watchlist_name_is_live_order_eligible(live, monkeypatch):
    """Full evaluated equity universe may auto-order — not SPY-only."""
    cfg = desk.load_config()
    cfg["watchlist"] = ["IDEA", "TEST"]
    cfg["live_agent"]["policy"]["symbols"] = ["SPY"]  # stale SPY-only hint must not block
    desk.save_config(cfg)
    ingested = []
    def capture(sig, *, research_only=False, cfg_override=None):
        ingested.append({"ticker": sig.get("ticker"), "source": sig.get("source"),
                         "research_only": research_only, "flags": list(sig.get("research_flags") or [])})
        return sig
    monkeypatch.setattr(desk, "_ingest_one_signal", capture)
    live.service.tick()
    assert live.calls == ["IDEA"]
    assert len(ingested) == 1
    assert ingested[0]["ticker"] == "IDEA"
    assert ingested[0]["research_only"] is False
    assert ingested[0]["source"] == "live_agent"
    assert "live_order_universe_excluded" not in ingested[0]["flags"]
