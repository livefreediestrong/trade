"""Decision context coverage: local facts, model boundary and frozen receipts."""
import copy
from datetime import datetime, timedelta, timezone
import json

import pytest

import live_research
from test_brain_repairs import isolated, analysis, thesis

NOW = datetime(2026, 9, 26, 17, tzinfo=timezone.utc)


def facts():
    return {'quote': {'price': 100, 'fresh': True, 'market_time': NOW.isoformat(),
                      'bid': 99.99, 'ask': 100.01, 'source': 'fixture'},
            'research_evidence': {'retrieved_at': NOW.isoformat(), 'items': [
                {'url': 'https://example.com/filing', 'published_at': NOW.isoformat()}]},
            'earnings': {'date': '2026-09-28'}, 'learning_context': {'sample_count': 45}}


def test_context_is_bounded_and_does_not_leak_or_mutate_config():
    original = facts()
    cfg = {'decision_horizon_min': 20, 'broker_identity': {'account_id': 'PRIVATE'},
           'api_key': 'SECRET', 'session_active': True}
    before = copy.deepcopy((original, cfg))
    context = live_research.decision_context(original, cfg, now=NOW)
    assert context['quote']['research_price_current'] and context['quote']['spread_bps'] == 2
    assert context['dated_headline_count'] == 1 and context['matching_observation_count'] == 45
    assert context['earnings_date'] == '2026-09-28'
    assert (original, cfg) == before
    blob = json.dumps(context)
    assert 'SECRET' not in blob and 'PRIVATE' not in blob and len(blob) < 5000
    assert any('complete account holdings' in x for x in context['missing_context'])


@pytest.mark.parametrize('change', [
    {'delayed': True}, {'market_data_type': 3}, {'market_data_type': 4},
    {'market_data_type': 2}, {'source': 'IBKR delayed mkt data'},
    {'source': 'frozen'}, {'market_time': (NOW-timedelta(seconds=121)).isoformat()},
    {'market_time': (NOW+timedelta(minutes=1)).isoformat()}, {'market_time': None},
    {'market_time': '2026-09-26T17:00:00'}, {'price': float('nan')}, {'price': True},
    {'fresh': False},
])
def test_receipt_fresh_flag_cannot_hide_delayed_stale_or_invalid_quote(change):
    original = facts(); original['quote'].update(change)
    context = live_research.decision_context(original, {}, now=NOW)
    assert not context['quote']['research_price_current']
    assert context['quote']['spread_bps'] is None
    assert any('price' in x for x in context['missing_context'])


@pytest.mark.parametrize('change', [
    {'retrieved_at': (NOW-timedelta(seconds=121)).isoformat()},
    {'retrieved_at': (NOW+timedelta(seconds=1)).isoformat()},
    {'retrieved_at': None}, {'items': None}, {'items': 'malformed'},
    {'items': [{'url':'https://example.com/future', 'published_at': (NOW+timedelta(seconds=1)).isoformat()}]},
])
def test_bad_or_empty_news_is_unknown_not_no_news(change):
    original = facts(); original['research_evidence'].update(change)
    result = live_research.decision_context(original, {}, now=NOW)
    assert result['dated_headline_count'] == 0
    assert any('does not mean there is no news' in x for x in result['missing_context'])


def test_duplicate_news_and_missing_history_do_not_inflate_confidence():
    original = facts()
    original['research_evidence']['items'] *= 6
    original['learning_context']['sample_count'] = float('nan')
    original['earnings']['date'] = '2026-09-25'
    result = live_research.decision_context(original, {}, now=NOW)
    assert result['dated_headline_count'] == 1
    assert result['matching_observation_count'] is None and result['earnings_date'] is None
    assert any('missing or small' in x for x in result['missing_context'])


def test_shared_model_and_saved_record_receive_the_same_context(isolated, monkeypatch):
    import app as desk
    import llm_trader
    import companion_evidence
    seen = []
    def decide(a, cfg, **kw):
        seen.append(json.loads(llm_trader._analysis_context_blob(a)))
        return thesis(brain_mode='mock')
    monkeypatch.setattr(llm_trader, 'decide_trade_thesis', decide)
    monkeypatch.setattr(desk.news_stream, 'cached_evidence', lambda _: {'status':'unavailable', 'items':[]})
    cfg = dict(desk.DEFAULT_CONFIG, brain_mode='mock')
    before = copy.deepcopy(desk.load_config()), copy.deepcopy(desk.load_ledger())
    result = desk._research_thesis(analysis(), cfg, source='scanner')
    saved = next(x for x in desk._decision_ring.since(limit=5) if x['id'] == result['decision_record_id'])
    checked = seen[0]['companion_context']['decision_checks']
    assert checked == saved['inputs']['companion_context']['decision_checks']
    assert saved['prompt_version'] == 'thesis-v3-context'
    case = companion_evidence.daytime_case(saved, companion_evidence.decision_detail({}))
    texts = [x['text'] for x in case['roles']['changing_woman']['uncertainties']]
    assert all(x in texts for x in checked['missing_context'])
    assert (desk.load_config(), desk.load_ledger()) == before
    checked['missing_context'].append('Later change')
    assert 'Later change' not in saved['inputs']['companion_context']['decision_checks']['missing_context']
