"""Fox's per-sector cap only ever removes a new buy; missing sector data never blocks."""
from test_live_agent import live  # noqa: F401 - fixture reuse
from test_execution_repairs import execution  # noqa: F401 - fixture reuse
import live_agent as agent

POLICY = dict(agent.DEFAULTS)


def buy(ticker, sector="Technology"):
    return {"ticker": ticker, "side": "buy", "sector": sector}


def held(*names, sector="Technology"):
    return {n: {"shares": 10, "sector": sector} for n in names}


def test_blocks_third_name_in_a_crowded_sector_with_a_plain_reason():
    reason = agent.sector_block(buy("NVDA"), POLICY, held("AAPL", "MSFT"), [])
    assert reason and "AAPL, MSFT" in reason and "limit is 2" in reason and "NVDA" in reason


def test_other_sectors_unknown_sectors_and_etfs_are_not_blocked():
    managed = held("AAPL", "MSFT")
    assert agent.sector_block(buy("XOM", "Energy"), POLICY, managed, []) is None
    assert agent.sector_block(buy("SPY", None), POLICY, managed, []) is None
    assert agent.sector_block(buy("ZZZ", "Unknown"), POLICY, managed, []) is None


def test_adding_to_a_held_name_sells_and_zero_cap_are_allowed():
    managed = held("AAPL", "MSFT")
    assert agent.sector_block(buy("MSFT"), POLICY, managed, []) is None
    assert agent.sector_block({**buy("NVDA"), "side": "sell"}, POLICY, managed, []) is None
    assert agent.sector_block(buy("NVDA"), dict(POLICY, max_positions_per_sector=0), managed, []) is None


def test_buys_still_waiting_at_the_broker_count_but_closed_rows_and_user_orders_do_not():
    pending = [{"signal": {"source": "live_agent", "side": "buy", "ticker": "AMD", "sector": "Technology"}},
               {"signal": {"source": "manual", "side": "buy", "ticker": "INTC", "sector": "Technology"}}]
    managed = {"AAPL": {"shares": 5, "sector": "Technology"}, "ORCL": {"shares": 0, "sector": "Technology"}}
    reason = agent.sector_block(buy("NVDA"), POLICY, managed, pending)
    assert reason and "AAPL, AMD" in reason and "INTC" not in reason and "ORCL" not in reason


def test_saved_policies_from_before_the_cap_upgrade_and_bounds_are_enforced():
    old = {k: v for k, v in agent.DEFAULTS.items() if k != "max_positions_per_sector"}
    assert agent.validate(old)["max_positions_per_sector"] == 2
    for bad in (-1, 11, 1.5):
        try:
            agent.validate(dict(agent.DEFAULTS, max_positions_per_sector=bad))
        except ValueError:
            continue
        raise AssertionError(f"{bad} must be rejected")


def test_fox_gate_applies_the_cap_to_new_buys_only(live, monkeypatch):  # noqa: F811
    import app as desk
    monkeypatch.setattr(agent, "_event_window", lambda cfg: None)
    monkeypatch.setattr(agent, "earnings_block", lambda *a, **k: None)
    cfg = desk.load_config()
    raw = live.service.load()
    raw["managed"] = {n: {"shares": 5, "sector": "Technology", "entry": 100., "stop": 99., "target": 102.,
                          "account_scope": agent.account_scope(cfg["broker_identity"])} for n in ("AAPL", "OTHER")}
    live.service.save(raw)
    signal = {"id": "s", "source": "live_agent", "ticker": "TEST", "side": "buy", "sector": "Technology",
              "agent_revision": cfg["live_agent"]["revision"], "agent_run_id": cfg["live_agent"]["run_id"],
              "agent_identity": cfg["broker_identity"]}
    assert "per-sector limit" in live.service.trade_error(signal, cfg, 0.0, False)
    assert live.service.trade_error(dict(signal, id="s2", sector="Energy"), cfg, 0.0, False) is None
    assert live.service.trade_error(dict(signal, id="s3", side="sell"), cfg, 0.0, True) is None
