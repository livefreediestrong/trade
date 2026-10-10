"""Company recipient identity and amounts must stay evidence, not guesses."""
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
import company_check as cc


@pytest.fixture
def source(monkeypatch):
    recipients, awards, calls = [], [], []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        rows = recipients if 'spending_by_category' in url else awards
        return NS(status_code=200, json=lambda: {'results': rows, 'page_metadata': {'hasNext': True}})
    monkeypatch.setattr(cc.requests, 'post', post)
    return recipients, awards, calls


def check():
    return cc.contracts('Apple Inc.', datetime(2026, 9, 27, tzinfo=timezone.utc))


def test_apple_juice_and_similar_recipient_names_cannot_become_company_totals(source):
    recipients, awards, _ = source
    recipients.extend([{'name':'APPLE OCCUPATIONAL MEDICAL SERVICES', 'amount': 29_400_000},
                       {'name':'APPLE INC.', 'amount': 12.5}])
    awards.extend([{'Recipient Name':'APPLE SEVEN SERVICES', 'Award Amount':50_000, 'Description':'apple juice'},
                   {'Recipient Name':'APPLE, INCORPORATED', 'Award Amount':15, 'Recipient UEI':'PUBLICID1234',
                    'recipient_id':'public-hash', 'generated_internal_id':'CONT_AWARD_TEST'}])
    out = check()
    assert out['total_12m'] is None and out['identity_verified'] is False
    assert out['candidate_obligations_12m'] == 12.5
    assert out['excluded_recipient_rows'] == out['excluded_award_rows'] == 1
    assert out['new_awards'][0]['recipient_uei'] == 'PUBLICID1234'
    assert len(out['new_awards']) == 1
    assert out['new_awards'][0]['identity_verified'] is False
    assert 'apple juice' not in str(out['new_awards'])
    assert '29.4M' not in out['summary']
    assert out['coverage']['award_has_more'] is True


@pytest.mark.parametrize('recipient', ['APPLE HOSPITALITY REIT INC', 'APPLEDore MARINE LLC', 'APPLE INC SUBSIDIARY',
                                      'APPLE LLC', 'THE APPLE INC', '', None])
def test_shared_words_different_entities_and_missing_names_are_not_matches(source, recipient):
    recipients, awards, _ = source
    recipients.append({'name':recipient, 'amount':100})
    awards.append({'Recipient Name':recipient, 'Award Amount':100})
    out = check()
    assert out['matched_names'] == [] and out['new_awards'] == []
    assert out['total_12m'] is None and out['candidate_obligations_12m'] is None
    assert 'unknown, not zero' in out['summary']


@pytest.mark.parametrize('value', [None, True, 'bad', float('nan'), float('inf')])
def test_missing_or_invalid_candidate_amount_is_unknown(source, value):
    recipients, _, _ = source
    recipients.extend([{'name':'APPLE INC', 'amount':12}, {'name':'APPLE INC', 'amount':value}])
    assert check()['candidate_obligations_12m'] is None


def test_signed_obligations_are_preserved_and_overflow_is_unknown(source):
    recipients, _, _ = source
    recipients.extend([{'name':'APPLE INC', 'amount':12}, {'name':'APPLE INC', 'amount':-20}])
    assert check()['candidate_obligations_12m'] == -8
    for row in recipients: row['amount'] = 1e308
    assert check()['candidate_obligations_12m'] is None


def test_empty_bounded_search_does_not_prove_zero_contracts(source):
    result = check()
    assert result['total_12m'] is None and result['coverage']['award_rows_read'] == 0
    assert 'unknown, not zero' in result['summary']


def test_requests_retain_public_recipient_ids_without_increasing_scope(source):
    _, _, calls = source
    check()
    assert len(calls) == 2
    assert calls[0][1]['json']['limit'] == 10
    body = calls[1][1]['json']
    assert body['limit'] == 5 and 'Recipient UEI' in body['fields'] and 'recipient_id' in body['fields']
    assert body['filters']['recipient_search_text'] == ['APPLE']


def test_build_passes_full_company_name_for_comparison(monkeypatch):
    import sys
    import edgar_client
    seen = []
    monkeypatch.setitem(sys.modules, 'yfinance', NS(Ticker=lambda _: NS(info={'longName':'Wrong Short Name'})))
    monkeypatch.setattr(edgar_client, 'company_title', lambda _: 'Apple Inc.')
    for name in ('bottom_line', 'insiders', 'lobbying', 'congress'):
        monkeypatch.setattr(cc, name, lambda *args: {'ok':True})
    monkeypatch.setattr(cc, 'contracts', lambda name, *a: seen.append(name) or {'ok':True})
    cc.build('AAPL')
    assert seen == ['Apple Inc.']


def test_old_name_search_cache_is_not_reused(monkeypatch):
    monkeypatch.setattr(cc, '_cache', {'AAPL':{'_at':cc.time.time(), '_cache_version':3,
        'ok':True, 'sections':{'contracts':{'ok':True, 'total_12m':29_400_000}}}})
    monkeypatch.setattr(cc, '_running', {})
    monkeypatch.setattr(cc, '_save_cache', lambda: None)
    monkeypatch.setattr(cc, 'build', lambda _: {'ok':True, 'sections':{'contracts':{'ok':True, 'total_12m':None}}})
    result = cc.check('AAPL')
    assert result['cached'] is False and result['sections']['contracts']['total_12m'] is None
