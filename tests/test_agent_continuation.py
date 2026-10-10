"""Continuation regressions: isolated broker transport, never real orders."""
import copy

import app as desk
import broker_router as router
import live_agent as agent
from test_execution_repairs import execution  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_trade_intelligence import entered, _hold  # noqa: F401


def test_saved_account_switch_does_not_reassign_old_protective_position(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.)
    cfg = desk.load_config()
    cfg['broker_identity']['account_id'] = 'NEW-ACCOUNT'
    desk.save_config(cfg)
    monkeypatch.setattr(router, 'verify_execution_context', lambda: {
        'ok': True, 'identity': copy.deepcopy(cfg['broker_identity'])})
    before = copy.deepcopy(entered.service.load()['managed'])
    assert entered.service._manage_exits(cfg, agent.validate(entered.policy)) is False
    assert len(entered.sent) == 1
    assert entered.service.load()['managed'] == before


def test_legacy_position_without_account_evidence_is_not_assumed_current(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.)
    raw = entered.service.load()
    raw['managed']['TEST'].pop('account_scope', None)
    entered.service.save(raw)
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert len(entered.sent) == 1
    assert 'TEST' in entered.service.load()['managed']


def test_transport_change_keeps_same_account_protection(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.)
    cfg = desk.load_config()
    cfg['broker_identity']['client_id'] = 99
    desk.save_config(cfg)
    monkeypatch.setattr(router, 'verify_execution_context', lambda: {
        'ok': True, 'identity': copy.deepcopy(cfg['broker_identity'])})
    assert entered.service._manage_exits(cfg, agent.validate(entered.policy)) is True
    assert entered.sent[-1]['side'] == 'sell'


def test_disconnected_cycle_does_not_consume_research_allowance(live, monkeypatch):
    monkeypatch.setattr(router, 'verify_execution_context', lambda: {'ok': False})
    live.service.tick()
    assert live.service.status()['today']['research'] == 0
    assert not live.calls and not live.sent


def test_exit_quote_timestamp_is_checked_even_if_provider_says_fresh(entered, monkeypatch):
    _hold(monkeypatch, 2, 99., market_time='2020-01-01T15:00:00+00:00')
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert len(entered.sent) == 1


def test_add_to_position_does_not_lower_a_raised_stop_or_extend_deadline(entered):
    cfg = desk.load_config()
    deadline = '2026-09-24T20:00:00+00:00'
    entered.service._update_managed('TEST', {'stop': 100., 'breakeven': True, 'exit_at': deadline})
    signal = {'id': 'add-on', 'ticker': 'TEST', 'side': 'buy', 'agent_identity': cfg['broker_identity'],
              'signal_price': 100., 'stop': 95., 'target': 110.}
    entered.service._track_entry(signal, {'shares': 2, 'price': 100.}, dict(entered.policy, max_hold_min=60))
    row = entered.service.load()['managed']['TEST']
    assert row['stop'] >= 100. and row['breakeven']
    assert row['exit_at'] <= deadline


def test_cumulative_partial_fills_are_added_once_and_survive_restart(entered):
    signal = next(s for s in desk.load_signals() if s['id'] == 'decision-1')
    fill = dict(desk.load_ledger()['broker_fills'][0], shares=3, price=101., confirmed=True)
    ledger = desk.load_ledger()
    ledger['broker_fills'][0] = fill
    desk.save_ledger(ledger)
    for service in (entered.service, agent.LiveAgent(desk)):
        service._sync_managed_fills()
        service._sync_managed_fills()
        assert service.load()['managed']['TEST']['shares'] == 3
        assert service.load()['managed']['TEST']['entry'] == 101.
    assert signal['agent_exit_policy']['protective_exits'] is True


def test_fill_after_initial_pending_response_is_tracked(live, monkeypatch):
    monkeypatch.setattr(router, 'wait_for_fill', lambda *a, **k: {
        'state': 'unknown', 'terminal': False, 'filled_qty': 0})
    monkeypatch.setattr(router, 'reconcile_after_timeout', lambda *a, **k: {
        'state': 'unknown', 'terminal': False, 'filled_qty': 0})
    live.service.tick()
    ledger = desk.load_ledger()
    pending = ledger['pending_broker_orders'][0]
    assert not live.service.load().get('managed')
    desk._apply_broker_update(pending['signal'], pending['broker'], {
        'state': 'filled', 'terminal': True, 'filled_qty': 2, 'filled_avg_price': 100.}, 'test_reconcile')
    restarted = agent.LiveAgent(desk)
    restarted._sync_managed_fills()
    assert restarted.load()['managed']['TEST']['shares'] == 2
    assert restarted.load()['managed']['TEST']['account_scope'] == agent.account_scope(live.identity)


def test_fill_correction_requires_review_instead_of_silent_reownership(entered):
    ledger = desk.load_ledger()
    ledger['broker_fills'][0].update(shares=1, price=100., confirmed=True)
    desk.save_ledger(ledger)
    entered.service._sync_managed_fills()
    assert 'corrected' in entered.service.load()['managed']['TEST']['tracking_error']


def test_failed_exit_quote_is_explained_in_status(entered, monkeypatch):
    _hold(monkeypatch, 2, 99., delayed=True)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    check = entered.service.status()['exit_checks']['TEST']
    assert check['state'] == 'quote_unavailable' and check['checked_at']


def test_observed_external_reduction_does_not_reclaim_later_manual_shares(entered, monkeypatch):
    _hold(monkeypatch, 1, 100.1)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert entered.service.load()['managed']['TEST']['shares'] == 1
    _hold(monkeypatch, 5, 99.)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert entered.sent[-1]['shares'] == 1


def test_unconfirmed_fill_is_not_adopted(live):
    signal = dict(live.generate(live.cfg, ticker='TEST'), agent_identity=live.identity)
    live.service._track_entry(signal, {'shares': 2, 'price': 100., 'confirmed': False}, live.policy)
    assert not live.service.load().get('managed')


def test_later_tracking_uses_retained_fill_time_for_hold_deadline(live):
    from datetime import datetime, timedelta, timezone
    stamp = datetime.now(timezone.utc) - timedelta(minutes=40)
    signal = dict(live.generate(live.cfg, ticker='TEST'), agent_identity=live.identity)
    live.service._track_entry(signal, {'shares': 2, 'price': 100., 'ts': stamp.isoformat()},
                             dict(live.policy, max_hold_min=30))
    assert datetime.fromisoformat(live.service.load()['managed']['TEST']['exit_at']) < datetime.now(timezone.utc)


def test_busted_completed_exit_restores_review_record(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    signal = next(s for s in desk.load_signals() if s.get('agent_exit'))
    assert 'TEST' not in entered.service.load()['managed']
    entered.service._track_entry(signal, {'shares': 0, 'price': 0, 'confirmed': True}, entered.policy)
    assert 'corrected' in entered.service.load()['managed']['TEST']['tracking_error']


def test_review_record_clears_when_matching_broker_position_is_closed(entered, monkeypatch):
    _hold(monkeypatch, 0, 99.)
    entered.service._update_managed('TEST', {'tracking_error': 'Review correction'})
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert 'TEST' not in entered.service.load()['managed']
