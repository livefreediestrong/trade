"""Activation review uses isolated accounts/storage; no real orders or network."""
import copy
import sqlite3

import pytest

import app as desk
import broker_router as router
import live_research as research
import research_library
from test_execution_repairs import execution, BASE
from test_live_agent import live


@pytest.fixture
def review(live):
    cfg = desk.load_config()
    cfg['live_agent']['enabled'] = False
    cfg.update(mode='live_manual', session_active=False)
    desk.save_config(cfg)
    return live, desk.app.test_client()


def preview(client):
    result = client.post('/api/live-agent/start-review', base_url=BASE, json={})
    assert result.status_code == 200, result.get_json()
    return result.get_json()


def start(client, data, **updates):
    body = {k: data[k] for k in ('identity', 'revision', 'review_token')}
    body['confirm'] = 'PAPER' if data['identity']['paper_mode'] else 'REAL'
    return client.post('/api/live-agent/start', base_url=BASE, json=dict(body, **updates))


def test_preview_and_cancel_do_not_mutate_controls_or_ledger(review):
    live, client = review
    before = copy.deepcopy(desk.load_config()), copy.deepcopy(desk.load_ledger())
    data = preview(client)
    assert data['identity'] == live.identity and data['policy'] == live.policy
    assert data['universe_count'] >= 2 and data['desk_limits']['max_daily_loss_pct'] == 2
    assert data['auto_options']['enabled'] is False
    assert (desk.load_config(), desk.load_ledger()) == before and not live.sent


@pytest.mark.parametrize('paper', [False, True])
def test_explicit_review_arms_exact_account_once_without_submitting(review, paper):
    live, client = review
    live.identity['paper_mode'] = paper
    cfg = desk.load_config(); cfg['broker_identity'] = copy.deepcopy(live.identity); desk.save_config(cfg)
    original = copy.deepcopy(cfg)
    data = preview(client)
    result = start(client, data)
    assert result.status_code == 200 and result.get_json()['enabled']
    after = desk.load_config()
    assert after['session_active'] and after['mode'] == 'auto_live'
    assert after['live_agent']['policy'] == original['live_agent']['policy']
    assert after['kill_switch'] == original['kill_switch'] and not live.sent
    assert start(client, data).status_code == 400
    assert desk.load_config() == after  # no second run ID or duplicate activation


@pytest.mark.parametrize('field,value', [('confirm','WRONG'),('identity',{}),('revision','changed'),('review_token','unknown')])
def test_confirmation_mismatch_never_arms(review, field, value):
    live, client = review
    data = preview(client); before = copy.deepcopy(desk.load_config())
    assert start(client, data, **{field:value}).status_code == 400
    assert desk.load_config() == before and not live.sent


@pytest.mark.parametrize('change', ['limit', 'kill_switch', 'model', 'options', 'revision', 'identity', 'enabled'])
def test_changed_saved_settings_invalidate_review(review, change):
    live, client = review
    data = preview(client); cfg = desk.load_config()
    if change == 'limit': cfg['live_agent']['policy']['max_order_usd'] = 19
    if change == 'kill_switch': cfg['kill_switch']['armed'] = not cfg['kill_switch']['armed']
    if change == 'model': cfg['brain_mode'] = 'mock'
    if change == 'options': cfg['live_agent']['auto_options'] = {'enabled':True}
    if change == 'revision': cfg['live_agent']['revision'] = 'new-policy'
    if change == 'identity': cfg['broker_identity']['account_id'] = 'changed'
    if change == 'enabled': cfg['live_agent']['enabled'] = True
    desk.save_config(cfg); before = copy.deepcopy(desk.load_config())
    assert start(client, data).status_code == 400
    assert desk.load_config() == before and not live.sent


def test_expiry_and_expiry_during_broker_checks_reject(review, monkeypatch):
    live, client = review
    clock = [100.]
    monkeypatch.setattr(research.time, 'monotonic', lambda:clock[0])
    data = preview(client); clock[0] += 91
    assert start(client, data).status_code == 400
    data = preview(client)
    def slow_pnl():
        clock[0] += 91
        return 0., 100000., None
    monkeypatch.setattr(desk, '_broker_day_pnl', slow_pnl)
    assert start(client, data).status_code == 400
    assert not desk.load_config()['live_agent']['enabled'] and not live.sent


def test_settings_change_during_account_check_revokes_activation(review, monkeypatch):
    live, client = review
    data = preview(client)
    def change():
        cfg = desk.load_config(); cfg['risk_preset'] = 'low'; desk.save_config(cfg)
        return 0., 100000., None
    monkeypatch.setattr(desk, '_broker_day_pnl', change)
    assert start(client, data).status_code == 400
    assert desk.load_config()['risk_preset'] == 'low' and not desk.load_config()['live_agent']['enabled']
    assert not live.sent


@pytest.mark.parametrize('problem', ['pnl', 'loss', 'pending', 'corrupt', 'broker', 'identity'])
def test_new_blocker_after_review_prevents_start(review, monkeypatch, problem):
    live, client = review
    data = preview(client)
    if problem == 'pnl': monkeypatch.setattr(desk, '_broker_day_pnl', lambda:(None,100000.,'Missing P&L'))
    if problem == 'loss': monkeypatch.setattr(desk, '_broker_day_pnl', lambda:(-1e9,100000.,None))
    if problem == 'pending':
        ledger = desk.load_ledger(); ledger['pending_broker_orders'] = {'sample':{}}; desk.save_ledger(ledger)
    if problem == 'corrupt': desk._CORRUPT_PATHS['test-corrupt'] = 'Unreadable test file'
    if problem == 'broker': monkeypatch.setattr(router, 'verify_execution_context', lambda:{'ok':False})
    if problem == 'identity': live.identity['account_id'] = 'different'
    try:
        assert start(client, data).status_code == 400
        assert not desk.load_config()['live_agent']['enabled'] and not live.sent
    finally:
        desk._CORRUPT_PATHS.clear()


@pytest.mark.parametrize('problem', ['active', 'pnl', 'identity', 'paper_flag', 'broker_stops', 'option_exits'])
def test_unavailable_preview_leaves_controls_unchanged(review, monkeypatch, problem):
    live, client = review
    cfg = desk.load_config()
    if problem == 'active': cfg['live_agent']['enabled'] = True
    if problem == 'pnl': monkeypatch.setattr(desk, '_broker_day_pnl', lambda:(None,100000.,'Missing P&L'))
    if problem == 'identity': live.identity['account_id'] = ''
    if problem == 'paper_flag': live.identity['paper_mode'] = None
    if problem in ('broker_stops', 'option_exits'):
        live.identity['paper_mode'] = False
        if problem == 'broker_stops': cfg['live_agent']['policy']['broker_stops'] = True
        else: cfg['live_agent']['auto_options'] = {'automatic_exits':True}
    cfg['broker_identity'] = copy.deepcopy(live.identity); desk.save_config(cfg)
    before = copy.deepcopy(desk.load_config())
    assert client.post('/api/live-agent/start-review', base_url=BASE, json={}).status_code == 400
    assert desk.load_config() == before and not live.sent


def test_preview_request_boundary_and_bounded_tokens(review):
    _, client = review
    assert client.get('/api/live-agent/start-review',base_url=BASE).status_code == 405
    assert client.post('/api/live-agent/start-review',base_url=BASE,json={'policy':{}}).status_code == 400
    assert client.post('/api/live-agent/start-review',base_url=BASE,json={},headers={'Origin':'https://evil.example'}).status_code == 403
    first = preview(client)
    for _ in range(10): preview(client)
    assert start(client, first).status_code == 400


def test_library_failure_is_explicit_and_does_not_change_input(tmp_path, monkeypatch):
    original = {'ticker':'TEST', 'companion_context':{'existing':'keep'}}
    def unavailable(*a): raise sqlite3.OperationalError('Locked')
    monkeypatch.setattr(research_library, 'context', unavailable)
    out = research.with_references(original, tmp_path)
    assert out['companion_context']['reference_library']['available'] is False
    assert out['companion_context']['reference_library']['sources'] == []
    assert out['companion_context']['existing'] == 'keep'
    assert 'reference_library' not in original['companion_context']


@pytest.mark.parametrize('page', ['paper', 'research'])
def test_shared_activation_card_and_controller_are_in_pages(review, page):
    _, client = review
    result = client.get('/desk/'+page,base_url=BASE)
    assert result.status_code == 200
    html = result.get_data(as_text=True)
    assert html.count('id="research-live"') == 1
    assert '/static/research_live.js?v=' in html and 'id="research-live-confirm" hidden' in html
