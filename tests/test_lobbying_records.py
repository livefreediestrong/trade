"""Real LDA response semantics: name candidates, revisions and overlapping amounts."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import pytest
import company_check as cc

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)


def filing(n=1, **overrides):
    row = dict(filing_uuid=f'00000000-0000-4000-8000-{n:012d}', filing_type='Q1',
               filing_type_display='1st Quarter - Report', filing_year=2026,
               filing_period='first_quarter', filing_period_display='1st Quarter (Jan 1 - Mar 31)',
               dt_posted='2026-04-10T12:00:00-04:00', income='20000.00', expenses=None,
               expenses_method=None, client={'id': 2, 'client_id': 200, 'name':'APPLE, INC.'},
               registrant={'id': 3, 'name':'Example Lobbying LLC'},
               lobbying_activities=[{'general_issue_code_display':'Copyright'}])
    row.update(overrides)
    return row


@pytest.fixture
def source(monkeypatch):
    pages, calls = {}, []
    def get(url, **kw):
        calls.append((url, deepcopy(kw)))
        params = kw['params']
        result = pages.get((params['filing_year'], params.get('page', 1)),
                           {'count':0, 'next':None, 'results':[]})
        if isinstance(result, Exception): raise result
        if isinstance(result, int): return NS(status_code=result, headers={})
        return NS(status_code=200, headers={}, json=lambda:deepcopy(result))
    monkeypatch.setattr(cc.requests, 'get', get)
    # Do not sleep in fixture-based source tests. Governor itself is tested below.
    if hasattr(cc, '_lda_next_request'):
        monkeypatch.setattr(cc, '_lda_next_request', 0.0)
        monkeypatch.setattr(cc, 'LDA_REQUEST_INTERVAL', 0.0)
    return pages, calls


def put(source, rows, *, page=1, year=2026, more=False, count=None):
    source[0][year, page] = {'count':len(rows) if count is None else count,
                           'next':'https://lda.gov/api/v1/filings/?page=2' if more else None,
                           'results':rows}


def test_shared_names_do_not_become_company_spending(source):
    put(source, [filing(), filing(2, client={'id':4,'name':'PINEAPPLE PRODUCTS INC'}, income='999000000')])
    result = cc.lobbying('Apple Inc.', NOW)
    assert result['spend'] is None and result['identity_verified'] is False
    assert result['reports'] == 1
    assert result['excluded_names'] == ['PINEAPPLE PRODUCTS INC']
    assert result['records'][0]['client_id'] == 2
    assert '999' not in result['summary']


def test_organization_expenses_and_firm_income_are_never_added(source):
    put(source, [filing(), filing(2, registrant={'id':5,'name':'APPLE INC.'},
                                income=None, expenses='100000.00', expenses_method='a')])
    result = cc.lobbying('Apple Inc.', NOW)
    assert result['spend'] is None
    assert {r['amount_kind'] for r in result['records']} == {'firm_income','organization_expenses'}
    assert sorted(r['amount'] for r in result['records']) == [20000,100000]
    assert '120' not in result['summary']


def test_amendment_and_duplicate_pages_preserve_one_latest_in_sample(source):
    original = filing()
    amendment = filing(2, filing_type='1A', filing_type_display='1st Quarter - Amendment',
                       income='10000.00', dt_posted='2026-05-01T12:00:00-04:00')
    put(source, [original], more=True, count=2)
    put(source, [original,amendment], page=2, count=2)
    result = cc.lobbying('Apple Inc.', NOW)
    assert len(source[1]) == 2
    assert len(result['records']) == 2 and result['duplicate_rows'] == 1
    assert result['latest_reports'] == 1
    assert next(r for r in result['records'] if r['amended'])['status'] == 'latest_in_sample'
    assert next(r for r in result['records'] if not r['amended'])['status'] == 'older_in_sample'
    assert result['spend'] is None


@pytest.mark.parametrize('client', ['APPLE HOSPITALITY REIT INC.', 'APPLE LLC',
                                  'LAW FIRM ON BEHALF OF APPLE INC.', 'PINEAPPLE INC.'])
def test_related_words_and_intermediaries_are_not_automatic_issuer_matches(source, client):
    put(source, [filing(client={'id':5,'name':client})])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['reports'] == 0 and out['spend'] is None
    assert client in out['excluded_names']
    assert 'zero lobbying' in out['summary']


@pytest.mark.parametrize('value', [None, True, 'not given', 'NaN', 'Infinity'])
def test_missing_and_invalid_amount_never_becomes_zero(source, value):
    put(source, [filing(income=value)])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['records'][0]['amount'] is None and out['spend'] is None


def test_zero_negative_both_fields_and_no_activity_do_not_invent_values(source):
    put(source, [filing(income='0', filing_type='Q1Y'),
                 filing(2, income='-100', registrant={'id':5,'name':'Second'}),
                 filing(3, income='10', expenses='20', registrant={'id':6,'name':'Third'})])
    out = cc.lobbying('Apple Inc.', NOW)
    rows = {r['filing_uuid'][-1]:r for r in out['records']}
    assert rows['1']['amount'] == 0 and rows['1']['no_activity']
    assert 'below-threshold' in str(rows['1']['issues'])
    assert rows['2']['amount'] == -100 and rows['2']['status'] == 'needs_review'
    assert rows['3']['amount'] is None and rows['3']['income'] == 10 and rows['3']['expenses'] == 20
    assert out['spend'] is None


@pytest.mark.parametrize('overrides', [dict(dt_posted=None),dict(dt_posted='2027-01-01T00:00:00Z'),
    dict(dt_posted='2026-04-01'),dict(filing_uuid='bad'),dict(filing_type='OTHER'),
    dict(filing_period='second_quarter'),dict(client={'id':True,'name':'APPLE INC.'}),
    dict(lobbying_activities=['bad'])])
def test_invalid_identity_dates_and_type_hold_version_resolution(source, overrides):
    put(source, [filing(**overrides)])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['review_rows'] == 1 and out['latest_reports'] == 0


def test_duplicate_quarters_without_clear_amendment_need_review(source):
    put(source, [filing(),filing(2, dt_posted='2026-05-01T00:00:00Z')])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['review_rows'] == 2 and out['latest_reports'] == 0


def test_tied_timestamps_and_conflicting_uuid_copies_need_review(source):
    put(source, [filing(),filing(2, filing_type='1A'),filing(income='2000')])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['review_rows'] == 3 and out['latest_reports'] == 0
    assert 'Conflicting copies' in str(out['records'])


def test_registration_excluded_and_termination_amendment_recognized(source):
    put(source, [filing(filing_type='RR'),filing(2, filing_type='1@Y')])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['registration_rows_excluded'] == 1 and out['reports'] == 1
    assert out['records'][0]['termination'] and out['records'][0]['amended']


def test_distinct_registrants_and_quarters_are_not_collapsed(source):
    put(source, [filing(),filing(2, registrant={'id':7,'name':'Second'}),
                 filing(3, filing_type='Q2',filing_period='second_quarter')])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['latest_reports'] == 3 and out['spend'] is None


def test_missing_current_year_match_falls_back_only_after_complete_read(source):
    put(source, [filing(client={'id':4,'name':'APPLE SERVICES LLC'})])
    put(source, [filing(filing_year=2025)], year=2025)
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['year'] == 2025 and out['reports'] == 1
    assert [c['year'] for c in out['coverage']] == [2026,2025]


def test_page_limit_is_explicit_and_next_url_is_never_followed(source):
    put(source, [filing(client={'id':4,'name':'APPLE SERVICES LLC'})], more=True, count=100)
    put(source, [filing(2,client={'id':4,'name':'APPLE SERVICES LLC'})], page=2, more=True,count=100)
    source[0][2026,1]['next'] = 'https://evil.example/steal'
    out = cc.lobbying('Apple Inc.', NOW)
    assert len(source[1]) == 2 and all(url == cc.LDA_FILINGS for url,_ in source[1])
    assert out['complete'] is False and out['coverage'][0]['all_search_pages_read'] is False
    assert out['year'] == 2026 and out['reports'] == 0


@pytest.mark.parametrize('failure', [429,503,cc.requests.Timeout('test'),{'results':'broken'}])
def test_second_page_failure_retains_readable_records_as_partial(source, failure):
    put(source,[filing()],more=True,count=30)
    source[0][2026,2] = failure
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['ok'] and not out['complete'] and out['reports'] == 1 and out['warnings']


def test_first_page_error_is_not_no_activity_or_prior_year_success(source):
    source[0][2026,1] = 503
    put(source,[filing(filing_year=2025)],year=2025)
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['ok'] is False and len(source[1]) == 1


def test_returned_document_url_is_rebuilt_from_valid_id(source):
    put(source,[filing(filing_document_url='javascript:bad()')])
    out = cc.lobbying('Apple Inc.', NOW)
    assert out['records'][0]['url'] == 'https://lda.gov/filings/public/filing/00000000-0000-4000-8000-000000000001/print/'


def test_shared_request_governor_spaces_requests_and_honors_retry_after(monkeypatch):
    clock, calls = [10.0], []
    monkeypatch.setattr(cc.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(cc.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    monkeypatch.setattr(cc,'_lda_next_request',0.0)
    def get(*args,**kwargs):
        calls.append(clock[0])
        return NS(status_code=200,json=lambda:{'count':0,'next':None,'results':[]})
    monkeypatch.setattr(cc.requests,'get',get)
    cc._lda_page({},100);cc._lda_page({},100)
    assert calls == pytest.approx([10,14.1])
    monkeypatch.setattr(cc.requests,'get',lambda *a,**k:NS(status_code=429,headers={'Retry-After':'120'}))
    with pytest.raises(ValueError,match='429'): cc._lda_page({},100)
    assert cc._lda_next_request == pytest.approx(clock[0]+120)
    with pytest.raises(cc.requests.Timeout): cc._lda_page({},100)


def test_build_passes_full_legal_name_to_lobbying(monkeypatch):
    import sys
    import edgar_client
    seen=[]
    monkeypatch.setitem(sys.modules,'yfinance',NS(Ticker=lambda _:NS(info={'longName':'Wrong Short Name'})))
    monkeypatch.setattr(edgar_client,'company_title',lambda _:'APPLE INC')
    for fn in ('bottom_line','insiders','contracts','congress'):
        monkeypatch.setattr(cc,fn,lambda *a:{'ok':True})
    monkeypatch.setattr(cc,'lobbying',lambda name,*a:seen.append(name) or {'ok':True})
    cc.build('AAPL',NOW)
    assert seen == ['APPLE INC']


def test_repeated_page_cannot_establish_complete_coverage(source):
    put(source,[filing()],more=True,count=2)
    put(source,[filing()],page=2,count=2)
    out=cc.lobbying('Apple Inc.',NOW)
    assert not out['complete'] and not out['coverage'][0]['all_search_pages_read']
    assert out['duplicate_rows'] == 1 and out['latest_reports'] == 1


def test_changing_counts_warn_and_do_not_cache_as_complete(source):
    put(source,[filing()],more=True,count=2)
    put(source,[],page=2,count=1)
    out=cc.lobbying('Apple Inc.',NOW)
    assert not out['complete'] and 'count changed' in str(out['warnings'])


def test_no_next_or_count_metadata_cannot_establish_complete_coverage(source):
    source[0][2026,1]={'results':[filing()]}
    out=cc.lobbying('Apple Inc.',NOW)
    assert not out['complete'] and out['reports'] == 1


def test_bad_year_is_excluded_without_claiming_no_lobbying(source):
    put(source,[filing(filing_year=True)])
    out=cc.lobbying('Apple Inc.',NOW)
    assert out['reports'] == 0 and out['spend'] is None and out['warnings']
    assert len(source[1]) == 1


def test_record_cap_cannot_be_overridden_by_provider(source):
    put(source,[filing(n) for n in range(1,31)])
    out=cc.lobbying('Apple Inc.',NOW)
    assert len(out['records']) == 25 and out['complete'] is False


def test_retry_after_http_date_is_honored(monkeypatch):
    monkeypatch.setattr(cc,'_lda_next_request',0.0)
    monkeypatch.setattr(cc.time,'monotonic',lambda:100.0)
    monkeypatch.setattr(cc.requests,'get',lambda *a,**kw:NS(status_code=429,
        headers={'Retry-After':'Wed, 01 Jan 2031 00:00:00 GMT'}))
    with pytest.raises(ValueError): cc._lda_page({},200.0)
    assert cc._lda_next_request > 160
