"""Review regressions: real app control flow, temporary files, no broker I/O."""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import threading
import time

import pytest
import app as desk
import broker_alpaca as broker
import broker_ibkr
import broker_router
import data_sources as ds
import screener_logic
from broker_fixtures import approval
from test_execution_repairs import execution, BASE


def test_manual_limits_enable_and_block_reviewed_order(execution):
    client = desk.app.test_client()
    response = client.post('/api/config', base_url=BASE, json={'kill_switch': {
        'armed': True, 'max_daily_loss_usd': 10, 'max_trades_per_day': 1,
        'max_position_size_usd': 100}})
    assert response.status_code == 200, response.get_json()
    cfg = desk.load_config()
    assert cfg['kill_switch']['armed'] is True
    ticket = approval(client, 'review')
    result = client.post('/api/signals/review/approve', base_url=BASE, json=ticket)
    assert result.status_code == 400, result.get_json()
    assert not execution[2]
    assert not desk._broker_risk_gate(cfg, desk.load_ledger(), -11, 100000)[0]
    ledger = dict(desk.load_ledger(), broker_daily={desk._today_str(): {'trades': 1}})
    assert not desk._broker_risk_gate(cfg, ledger, 0, 100000)[0]
    response = client.post('/api/config', base_url=BASE, json={'kill_switch': {'armed': False}})
    assert response.status_code == 200 and desk.load_config()['kill_switch']['armed'] is False


def test_approval_response_preserves_newer_reconciliation(execution, monkeypatch):
    client = desk.app.test_client()
    review = client.post('/api/signals/review/review', base_url=BASE,
                         json={'order': {'type': 'limit', 'limit_price': 99}}).get_json()
    assert review['ok']
    monkeypatch.setattr(broker, 'wait_for_fill', lambda *a, **kw: {
        'state': 'partially_filled', 'terminal': False, 'filled_qty': 3, 'filled_avg_price': 99})
    original = desk.execute_gated_broker_or_paper
    def execute_then_reconcile(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result['fill']['shares'] == 3
        monkeypatch.setattr(broker, 'wait_for_fill', lambda *a, **kw: {
            'state': 'filled', 'terminal': True, 'filled_qty': 10, 'filled_avg_price': 98})
        desk._reconcile_pending_broker_orders()
        return result
    monkeypatch.setattr(desk, 'execute_gated_broker_or_paper', execute_then_reconcile)
    response = client.post('/api/signals/review/approve', base_url=BASE,
                           json={'review_token': review['review_token'], 'ack_ticker': 'TEST'})
    assert response.status_code == 200, response.get_json()
    assert desk.load_signals()[0]['fill']['shares'] == 10
    assert response.get_json()['fill']['shares'] == 10
    assert desk.load_signals()[0]['fill']['broker_reconciled'] is True
    assert not desk.load_ledger()['pending_broker_orders']


def paper_signal():
    now = datetime.now(timezone.utc)
    return dict(id='paper-race', ticker='TEST', side='buy', workspace='paper', mode_at_create='auto_paper',
                status='pending', suggested_shares=1, signal_price=100, confidence=.99, verdict='PASS',
                ts=now.isoformat(), expires_at=(now+timedelta(minutes=15)).isoformat(),
                quote=ds.quote_snapshot(100, now, 'fixture'), llm_model='mock-fixture')


@pytest.mark.parametrize('change', [{'mode': 'manual'}, {'session_active': False}, {'paper_order_budget': 1}])
def test_primary_paper_rechecks_after_quote(execution, monkeypatch, change):
    cfg = dict(desk.load_config(), mode='auto_paper', session_active=True, rth_only=False,
               slip_bps=0, fee_bps=0)
    desk.save_config(cfg)
    def quote(_):
        desk.save_config(dict(desk.load_config(), **change))
        return 100.
    monkeypatch.setattr(desk, 'fetch_last_price', quote)
    result = desk.paper_fill(paper_signal(), cfg, source='auto_paper')
    assert not result['ok'] and not desk.load_ledger()['fills']


def test_macro_pending_keeps_paper_workspace(execution):
    cfg = dict(desk.load_config(), mode='auto_paper', session_active=True, rth_only=False,
               slip_bps=0, fee_bps=0)
    desk.save_config(cfg)
    result = desk.execute_loop_decision(analysis={'ticker': 'TEST', 'price': 100, 'verdict': 'PASS',
        'entry_quality': {'label': 'early'}}, thesis={'side': 'buy', 'confidence': .99,
        'llm_model': 'mock-fixture', 'macro_force_ask_first': True}, cfg=cfg, mid=100.)
    assert result['pending'] and desk.signal_workspace(result['signal']) == 'paper'


def test_restart_recovers_committed_paper_fill_only_in_paper_workspace(execution):
    fill = dict(id='fill', signal_id='paper-race', ticker='TEST', shares=1, price=100)
    ledger = desk.load_ledger()
    ledger['fills'] = [fill]
    desk.save_ledger(ledger)
    desk.save_signals([dict(paper_signal(), status='approving'),
                       dict(id='missing', workspace='live', status='approving')])
    desk.recover_interrupted_approvals()
    recovered, missing = desk.load_signals()
    assert recovered['status'] == 'approved' and recovered['fill'] == fill
    assert missing['status'] == 'rejected'
    assert len(desk.load_ledger()['fills']) == 1


def test_invalid_utf8_is_quarantined_and_cannot_be_overwritten(execution, tmp_path):
    path = tmp_path/'bad.json'
    raw = b'{"cash":\xff}'
    path.write_bytes(raw)
    assert desk._load_json(path, {}) == {}
    assert str(path.resolve()) in desk._CORRUPT_PATHS
    assert next(tmp_path.glob('bad.json.corrupt.*.bak')).read_bytes() == raw
    desk._save_json(path, {'cash': 0})
    assert path.read_bytes() == raw


@pytest.mark.parametrize('quantity,price', [(5, 101), (10, 101), (0, 0)])
def test_execution_revisions_supersede_prior_fill_without_double_count(execution, quantity, price):
    sig = execution[1]
    response = dict(order_id='correction-order', broker='ibkr', order={'broker_identity': execution[0]['broker_identity']})
    def state(q, p, version):
        return dict(state='filled', terminal=True, filled_qty=q, filled_avg_price=p,
                    execution_details_verified=True, execution_versions={'execution': version},
                    execution_correction=version > 1)
    first, _ = desk._apply_broker_update(sig, response, state(10, 100, 1), 'test')
    corrected, terminal = desk._apply_broker_update(sig, response, state(quantity, price, 2), 'test')
    assert terminal and corrected['shares'] == quantity and corrected['price'] == price
    assert corrected['id'] == first['id']
    # Older late state must not restore the superseded quantity/price.
    stale, _ = desk._apply_broker_update(sig, response, state(10, 100, 1), 'test')
    assert stale['shares'] == quantity and stale['price'] == price
    ledger = desk.load_ledger()
    assert ledger['broker_daily'][desk._today_str()]['trades'] == 1
    assert len(ledger['broker_fills']) == 1


def test_terminal_fills_receive_batched_readonly_correction_refresh(execution, monkeypatch):
    cfg, sig, _ = execution
    identity = dict(cfg['broker_identity'], broker='ibkr')
    desk.save_config(dict(cfg, broker_identity=identity))
    response = dict(order_id='correction-order', broker='ibkr', order={'broker_identity': identity})
    sig['live_response'] = response
    initial = dict(state='filled', terminal=True, filled_qty=10, filled_avg_price=100,
                   execution_details_verified=True, execution_versions={'execution': 1})
    desk._apply_broker_update(sig, response, initial, 'test')
    assert not desk.load_ledger()['pending_broker_orders']
    monkeypatch.setattr(desk, '_LAST_EXECUTION_CORRECTIONS_AT', 0.)
    # Router forwards missing attributes dynamically; restore absence, not a
    # resolved Alpaca function that would shadow future IBKR routing.
    monkeypatch.setitem(broker_router.__dict__, 'public_status', lambda: {'broker': 'ibkr'})
    calls = []
    def refresh(ids, expected):
        calls.append(ids)
        assert expected == identity
        return {'ok': True, 'identity': identity, 'orders': [dict(initial, order_id=ids[0],
            identity=identity, filled_qty=5, filled_avg_price=101,
            execution_versions={'execution': 2}, execution_correction=True)]}
    monkeypatch.setattr(broker_ibkr, 'refresh_execution_corrections', refresh)
    desk._reconcile_execution_corrections()
    desk._reconcile_execution_corrections()
    assert calls == [['correction-order']]
    assert desk.load_ledger()['broker_fills'][0]['shares'] == 5
    assert desk.load_signals()[0]['fill']['shares'] == 5


def test_timeout_retains_capacity_and_does_not_start_model(execution, monkeypatch):
    entered, release = [], threading.Event()
    calls = []
    def analyze(ticker):
        entered.append(ticker)
        assert release.wait(5)
        return {'ticker': ticker, 'verdict': 'PASS', 'price': 100}
    monkeypatch.setattr(screener_logic, 'analyze_ticker', analyze)
    monkeypatch.setattr(desk, '_research_thesis', lambda *a, **kw: calls.append(a) or {})
    monkeypatch.setattr(desk, '_EVALUATION_SLOTS', threading.BoundedSemaphore(2))
    slots = threading.BoundedSemaphore(2)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda t: desk._evaluate_one_with_timeout(t, {}, .1, request_slots=slots),
                                    ['A', 'B', 'C', 'D', 'E', 'F']))
        assert len(entered) == 2
        assert all('timeout' in r['error'] for r in results)
        # A second request cannot escape the process-wide slots either.
        assert 'timeout' in desk._evaluate_one_with_timeout('G', {}, .05)['error']
    finally:
        release.set()
        for thread in threading.enumerate():
            if thread.name.startswith('watchlist-eval-'):
                thread.join(2)
    assert not calls
    assert desk._EVALUATION_SLOTS.acquire(blocking=False)
    desk._EVALUATION_SLOTS.release()


@pytest.mark.parametrize('body', [{'deadline_sec': 'NaN'}, {'pool': True}, {'pool': 2.5}])
def test_evaluation_controls_reject_invalid_numbers(execution, body):
    response = desk.app.test_client().post('/api/watchlist/evaluate', base_url=BASE,
        json=dict(tickers=['AAPL'], **body))
    assert response.status_code == 400
