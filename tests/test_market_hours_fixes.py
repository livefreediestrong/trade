"""Exercise real review/price paths, never a production account or data directory."""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pandas as pd
import pytest
import app as desk
import data_sources as ds
import llm_trader

BASE = 'http://127.0.0.1:5056'
NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    for name in ('CONFIG', 'LEDGER', 'SIGNALS', 'JOURNAL'):
        monkeypatch.setattr(desk, name + '_PATH', tmp_path / (name.lower() + '.json'))
    desk._CORRUPT_PATHS.clear()
    monkeypatch.setattr(desk, '_QUOTE_SNAPSHOTS', {})
    monkeypatch.setattr(desk, '_MARKS', {})
    monkeypatch.setattr(desk, '_broker_public_status', lambda: {'configured': True, 'paper_mode': None})


def test_flat_confidence_does_not_become_buy_confidence(monkeypatch):
    monkeypatch.setattr(llm_trader, 'decide_trade_thesis', lambda *a, **kw: {
        'side': 'flat', 'confidence': .66, 'llm_model': 'mock-momentum',
        'brain_mode': 'mock', 'routed': 'mock_cheap', 'router_reason': 'verdict_avoid'})
    monkeypatch.setattr(desk, '_with_lessons', lambda x, cfg=None: x)
    sig = desk._enrich_signal_with_llm({'ticker': 'WMT', 'side': 'buy', 'confidence': .22}, {}, {})
    assert sig['side'] == 'hold' and sig['confidence'] == .66
    view = desk._signal_ui_projection(sig, {'mode': 'live_manual'})
    assert not view['actionable']
    assert view['llm_model'] == 'mock-momentum' and view['routed'] == 'mock_cheap'


@pytest.mark.parametrize('extra', [
    {'llm_side': 'flat'}, {'side': 'hold'}, {'llm_error': 'unavailable'},
    {'llm_model': 'mock-momentum'}, {'verdict': 'AVOID'}, {'sources': ['demo']},
])
def test_approval_and_direct_broker_path_block_research(extra, monkeypatch):
    sig = dict(id='research', ticker='AAPL', side='buy', status='pending', **{k:v for k,v in extra.items() if k != 'side'})
    sig.update(extra)
    cfg = dict(desk.load_config(), mode='live_manual', session_active=True)
    desk.save_config(cfg)
    desk.save_signals([sig])
    monkeypatch.setattr(desk, 'live_broker_place_order', lambda *a: pytest.fail('No order may be submitted'))
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: pytest.fail('Reject before broker I/O'))
    response = desk.app.test_client().post('/api/signals/research/approve', base_url=BASE, json={})
    assert response.status_code == 400
    assert desk.load_signals()[0]['status'] == 'pending'
    assert not desk.execute_gated_broker_or_paper(sig, cfg, source='test', via='test')['ok']


def test_legacy_flat_buy_projected_as_hold_without_rewriting_record():
    sig = {'side': 'buy', 'llm_side': 'flat', 'confidence': .44, 'llm_confidence': .66,
           'citations': [{'key': 'as_of', 'label': 'As of', 'value': 'old scan time'}]}
    view = desk._signal_ui_projection(sig, {'mode': 'manual'})
    assert view['side'] == 'hold' and view['confidence'] == .66 and not view['actionable']
    assert sig['side'] == 'buy'
    assert 'price time unknown' in view['citations'][0]['label']
    assert sig['citations'][0]['label'] == 'As of'


@pytest.mark.parametrize('age,expected', [(5, True), (119, True), (121, False), (86400, False), (-60, False)])
def test_provider_age_gate(age, expected):
    quote = ds.quote_snapshot(100, NOW.timestamp() - age, 'test', now=NOW)
    assert quote['fresh'] is expected
    assert quote['market_time'] != quote['received_at']


@pytest.mark.parametrize('stamp', [None, 0, 'nonsense', '2026-09-23T12:00:00'])
def test_missing_or_ambiguous_market_time_is_not_fresh(stamp):
    assert not ds.quote_snapshot(100, stamp, 'test')['fresh']


def test_old_bar_is_rejected_and_fresh_fallback_is_accepted(monkeypatch):
    monkeypatch.setattr(ds, 'finnhub_quote', lambda t: {'current': 101, 'timestamp': NOW.timestamp()-86400})
    monkeypatch.setattr(ds, 'yahoo_quote_batch', lambda tickers: {})
    bars = pd.DataFrame({'Close': [100.]}, index=pd.DatetimeIndex([NOW-dt.timedelta(days=1)]))
    monkeypatch.setitem(sys.modules, 'yfinance', NS(Ticker=lambda s: NS(history=lambda **k: bars)))
    assert desk.fetch_last_price('TEST') is None
    assert 'TEST' not in desk._MARKS
    bars.index = pd.DatetimeIndex([dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=20)])
    assert desk.fetch_last_price('TEST') == 100
    assert desk._QUOTE_SNAPSHOTS['TEST']['source'] == 'Yahoo 1m'


def test_stale_quote_cannot_reach_broker_submit(monkeypatch):
    import broker_router as broker
    from broker_fixtures import IDENTITY
    monkeypatch.setattr(broker._module(), 'verify_execution_context', lambda: {'ok':True,'identity':IDENTITY})
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: True)
    monkeypatch.setattr(broker._module(), 'public_status', lambda: {'broker':'alpaca'})
    monkeypatch.setattr(ds, 'latest_quote', lambda t: ds.quote_snapshot(100, NOW.timestamp()-86400, 'stale'))
    monkeypatch.setattr(desk, 'live_broker_place_order', lambda *a: pytest.fail('No stale quote order'))
    result = desk.execute_gated_broker_or_paper({'ticker':'AAPL','side':'buy'}, {'broker_identity':IDENTITY}, source='test', via='test')
    assert result['error'] == 'no_real_quote'


def test_paper_does_not_use_stale_analysis_price(monkeypatch):
    monkeypatch.setattr(desk, 'fetch_last_price', lambda t: None)
    result = desk.paper_fill({'side':'buy','ticker':'AAPL','signal_price':100,'analysis_price':100}, {}, source='test')
    assert result['error'] == 'no_real_quote'


def test_market_timestamp_is_not_fabricated():
    cites = llm_trader.screener_citations({'price': 100})
    assert next(c['value'] for c in cites if c['key']=='as_of') == 'Unknown'


def test_projection_ages_quote_without_inventing_another_receipt():
    original = ds.quote_snapshot(100, NOW.timestamp()-180, 'test', now=NOW-dt.timedelta(seconds=170))
    signal = {'side':'buy','quote':original}
    view = desk._signal_ui_projection(signal, {'mode':'manual'})
    assert not view['quote']['fresh'] and not view['actionable']
    assert view['quote']['received_at'] == original['received_at']
    assert view['quote']['age_sec'] > original['age_sec']


def test_broker_panel_does_not_authorize_using_paper_limits(monkeypatch):
    monkeypatch.setattr(desk, 'can_take_trade', lambda *a, **k: (True, 'ok'))
    result = desk._risk_cockpit(dict(desk.load_config(), mode='live_manual', session_active=True), desk.load_ledger())
    assert not result['trade_allowed'] and result['permission_label'] == 'Unverified'
    assert result['exposure_usd'] is None and result['open_positions'] is None


def test_broker_mode_latest_call_uses_scanner_and_dates_old_calls():
    old = {'ticker':'AAPL','ts':(NOW-dt.timedelta(days=1)).isoformat(),'side':'buy','event':'decision'}
    fresh = {'ticker':'MCD','ts':dt.datetime.now(dt.timezone.utc).isoformat(),'side':'sell'}
    cfg = {'mode':'live_manual'}
    current = desk._latest_desk_call(cfg, [fresh], {'last_decision':old})
    assert current['ticker']=='MCD' and current['call_source']=='Research scanner' and not current['stale']
    historical = desk._latest_desk_call({'mode':'manual'}, [], {'last_decision':old})
    assert historical['stale'] and historical['confidence']==0 and historical['side']=='hold'


def test_ui_hides_approval_and_labels_mock():
    source = (Path(desk.APP_DIR) / 'static/app.js').read_text(encoding='utf-8')
    def function(name):
        start = source.index('  function '+name+'(')
        end = source.index('\n  }', start+3) + len('\n  }')
        return source[start:end]
    code = '\n'.join(function(n) for n in ('signalModelLabel','isOppActionable','deskCall','brokerMode','realMoney','moneyNoun','liteSignature','renderStartupStatus'))
    code += '''
const assert = require('node:assert/strict');
let activeWorkspace = 'live';
assert.match(signalModelLabel({llm_model:'mock-momentum'}), /no AI model/);
assert.equal(isOppActionable({source:'pending',id:'1',actionable:false}),false);
assert.equal(isOppActionable({source:'pending',id:'1',actionable:true}),true);
assert.equal(deskCall({loop:{last_decision:{ticker:'OLD'}}}),null);
assert.equal(deskCall({desk_call:{ticker:'NEW'}}).ticker,'NEW');
global.window = {__brokerStatus:{configured:false,paper_mode:null}};
assert.equal(brokerMode({mode:'live_manual'}),true);
assert.equal(realMoney({mode:'live_manual'}),false);
assert.match(moneyNoun({mode:'live_manual'}), /unverified/);
assert.equal(brokerMode({mode:'auto_paper'}),false);
assert.notEqual(liteSignature({broker:{paper_mode:null}}),liteSignature({broker:{paper_mode:false}}));
assert.notEqual(liteSignature({opportunities:[{id:'x',actionable:true}]}),liteSignature({opportunities:[{id:'x',actionable:false}]}));
assert.notEqual(liteSignature({broker_book:{ok:false}}),liteSignature({broker_book:{ok:true,positions:[]}}));
assert.notEqual(liteSignature({broker_book:{positions:[{ticker:'TEST',shares:100}]}}),liteSignature({broker_book:{positions:[{ticker:'TEST',shares:1}]}}));
const startupElement = {textContent:'',hidden:true};
const $ = () => startupElement;
renderStartupStatus({broker:{paper_mode:null},startup:{message:'Sign in'}});
assert.equal(startupElement.textContent,'Sign in');
renderStartupStatus({broker:{paper_mode:false}});
assert.match(startupElement.textContent,/account identified/);
renderStartupStatus({broker:{paper_mode:false},broker_book:{ok:true,risk_error:'Daily P&L unavailable'}});
assert.match(startupElement.textContent,/connected.*Daily P&L unavailable/);
'''
    subprocess.run(['node', '-e', code], check=True, capture_output=True, text=True)


def test_order_review_displays_quote_model_and_live_mode_without_submitting():
    source = (Path(desk.APP_DIR) / 'static/app.js').read_text(encoding='utf-8')
    def function(name, asynchronous=False):
        start = source.index('  ' + ('async ' if asynchronous else '') + 'function '+name+'(')
        end = source.index('\n  }', start+3) + len('\n  }')
        return source[start:end]
    code = '\n'.join(function(n) for n in ('signalModelLabel','quoteLabel','brokerMode','realMoney','moneyNoun','escapeHtml','signalWorkspace','approvalIsAllowed','updateApproveEligibility'))
    code += function('openApprovePreview', True)
    code += r'''
const assert = require('node:assert/strict');
let activeWorkspace = 'live';
global.window = {__brokerStatus:{broker:'ibkr',configured:true,paper_mode:false}};
const elements = new Map();
const $ = key => {
  if (!elements.has(key)) {
    const classes = new Set(key === '#approve-modal' ? ['hidden'] : []);
    elements.set(key, {
      classList:{toggle(){},contains:name=>classes.has(name),remove:name=>classes.delete(name),add:name=>classes.add(name)},dataset:{},setAttribute(){},focus(){},textContent:'',innerHTML:''
    });
  }
  return elements.get(key);
};
const state = {config:{mode:'live_manual'},ledger:{cash:100000},daily:{pnl:12345},preset:{}};
const lastDataAt = Date.now();
let pendingApproveId = null;
let approvalContext = null;
let approveReturnFocus = null, approveReturnSignalId = null;
const $$ = () => [];
const signal = {id:'fresh',ticker:'TEST',side:'buy',actionable:true,suggested_shares:1,signal_price:100,
  llm_model:'actual-model',quote:{source:'Test feed',market_time:new Date().toISOString(),fresh:true}};
const findSignalById = () => signal;
const requests = [], messages = [];
const api = async path => { requests.push(path); return {review_token:'ticket',order:{type:'limit',limit:100,shares:1,notional_bound:100},broker:window.__brokerStatus}; };
const renderBrokerStatus = b => { window.__brokerStatus=b; };
const toast = msg => messages.push(msg);
const plainify = s => s;
const fmtMoney = v => '$'+v;
global.document = {activeElement:null};
(async () => {
  await openApprovePreview('fresh');
  assert.equal($('#approve-modal-title').textContent,'Review live order');
  assert.match($('#approve-preview').innerHTML,/REAL MONEY/);
  assert.match($('#approve-preview').innerHTML,/Test feed/);
  assert.match($('#approve-preview').innerHTML,/actual-model/);
  assert.doesNotMatch($('#approve-preview').innerHTML,/12345/);
  assert.equal($('#btn-approve-confirm').disabled,true);
  assert.deepEqual(requests,['/api/signals/fresh/review']);
  window.__brokerStatus.paper_mode=null;
  await openApprovePreview('fresh');
  assert.equal(pendingApproveId,null);
  assert.match(messages.at(-1),/unverified/);
  signal.actionable=false;
  await openApprovePreview('fresh');
  assert.equal(requests.length,2);
})().catch(e => { console.error(e); process.exit(1); });
'''
    subprocess.run(['node', '-e', code], check=True, capture_output=True, text=True)
