"""Public filing evidence tests. No account access, model calls or orders."""
import copy
from datetime import date, datetime, timezone
from types import SimpleNamespace as NS

import pytest
import company_check as cc
from test_company_check import FORM4, sec_responses

TODAY = date(2026, 9, 25)


def row(**changes):
    out = cc.parse_form4(FORM4)[0]
    out.update(filed='2026-09-12', accession='0001-01')
    out.update(changes)
    return out


def summary(rows, **kw):
    return cc.summarize_insiders('ABC', rows, 2, today=TODAY, **kw)


def test_namespace_owner_id_holding_change_and_filing_plan_scope():
    parsed = cc.parse_form4(FORM4.replace('<ownershipDocument>', '<ownershipDocument xmlns="urn:sec-test">'))
    assert parsed[0]['owner_key'] == '0000001234' and parsed[0]['officer']
    assert parsed[0]['shares_after'] == 5000 and parsed[0]['holding_change_pct'] == 25
    assert 'check which rows' in parsed[0]['plan_label']


@pytest.mark.parametrize('flag,expected', [('',None),('<aff10b5One>0</aff10b5One>',False)])
def test_absent_or_unchecked_plan_is_not_invented_discretionary_trade(flag, expected):
    result = cc.parse_form4(FORM4.replace('<aff10b5One>1</aff10b5One>',flag))[0]
    assert result['planned'] is expected
    assert 'unknown' in result['plan_label'] if expected is None else 'does not prove' in result['plan_label']


def test_joint_owners_are_preserved_without_multiplying_value_or_cluster():
    owner = FORM4.split('<reportingOwner>',1)[1].split('</reportingOwner>',1)[0]
    xml = FORM4.replace('</reportingOwner>','</reportingOwner><reportingOwner>'+owner.replace('DOE JANE','DOE JOHN').replace('1234','5678')+'</reportingOwner>',1)
    parsed = cc.parse_form4(xml)
    assert len(parsed) == 2 and len(parsed[0]['owners']) == 2 and parsed[0]['joint']
    assert parsed[0]['owner_key'] is None and parsed[0]['holding_change_pct'] is None
    result = summary(parsed)
    assert result['buys']['value'] == 50500 and result['buys']['ambiguous_owner_rows'] == 1
    assert result['cluster']['status'] == 'not_observed'


def test_cluster_has_explicit_distinct_id_window_and_ownership_rules():
    rows = [row(),row(date='2026-09-12',owner_key='0000005678')]
    original = copy.deepcopy(rows)
    assert summary(rows)['cluster']['status'] == 'observed'
    assert summary(rows)['cluster']['people'] == 2 and rows == original
    assert summary([row(),row(date='2026-09-12')])['cluster']['status'] == 'not_observed'
    for updates in ({'date':'2026-08-26'}, {'date':'2026-09-26'}, {'ownership':'I'}, {'owner_key':None}, {'joint':True}, {'issues':['bad quantity']}):
        assert summary([row(),row(**dict(owner_key='0000005678') | updates)])['cluster']['status'] == 'not_observed'


def test_partial_coverage_and_amendment_suppress_cluster_and_corrected_totals():
    rows = [row(), row(owner_key='0000005678')]
    assert summary(rows,complete=False)['cluster']['status'] == 'needs_review'
    result = summary(rows, amendments=1)
    assert result['buys']['value'] is None and result['buys']['known_value'] is None
    assert result['buys']['people'] is None and result['cluster']['people'] is None
    assert 'withheld' in result['summary']


def test_unpriced_purchases_do_not_become_zero_dollar_totals():
    result = summary([row(), row(value=None)])
    assert result['buys']['value'] is None and result['buys']['known_value'] == 50500
    assert result['buys']['unpriced_rows'] == 1 and 'unknown' in result['summary']
    zero = cc.parse_form4(FORM4.replace('<value>50.5</value>', '<value>0</value>'))[0]
    assert zero['value'] == 0


@pytest.mark.parametrize('bad', ['-1','NaN','Infinity'])
def test_invalid_share_values_are_visible_but_excluded(bad):
    result = cc.parse_form4(FORM4.replace('<value>1000</value>',f'<value>{bad}</value>'))[0]
    assert result['issues'] and result['value'] is None
    assert summary([result])['buys']['rows'] == 0


def test_multi_transaction_holding_percent_is_not_guessed():
    tx = FORM4.split('<nonDerivativeTransaction>',1)[1].split('</nonDerivativeTransaction>',1)[0]
    parsed = cc.parse_form4(FORM4.replace('</nonDerivativeTable>','<nonDerivativeTransaction>'+tx+'</nonDerivativeTransaction></nonDerivativeTable>'))
    assert parsed[0]['holding_change_pct'] is None


def test_footnotes_keep_price_range_plan_and_missing_reference_context():
    xml = FORM4.replace('<transactionCode>P</transactionCode>','<transactionCode>P</transactionCode><footnoteId id="F1"/><footnoteId id="missing"/>')
    xml = xml.replace('</ownershipDocument>','<footnotes><footnote id="F1">Weighted price; values range 50 to 51.</footnote><footnote id="F2">A transaction follows a 10b5-1 plan adopted earlier.</footnote></footnotes></ownershipDocument>')
    r = cc.parse_form4(xml)[0]
    assert r['footnotes'][0]['text'].startswith('Weighted price')
    assert r['footnotes'][1]['text'] == 'Footnote text unavailable'
    assert '10b5-1' in r['filing_notes'][1]['text']


def test_shortened_footnote_is_explicit():
    xml = FORM4.replace('</ownershipDocument>','<footnotes><footnote id="F1">'+'A'*2100+'</footnote></footnotes></ownershipDocument>')
    r = cc.parse_form4(xml)[0]
    assert r['notes_truncated'] and len(r['filing_notes'][0]['text']) == 2000


def fake_get(monkeypatch, listing, docs):
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        if 'submissions' in url: return NS(status_code=200,json=lambda:listing)
        text = docs[url.split('/')[-1]]
        return NS(status_code=200,content=text.encode(),text=text)
    monkeypatch.setattr(cc.requests,'get',get)
    return calls


def test_fetch_preserves_filing_time_delay_and_original_link(monkeypatch,sec_responses):
    recent = sec_responses['filings']['recent']
    recent['acceptanceDateTime'] = ['2026-09-11T18:00:00Z','2026-09-12T19:00:00Z']
    recent['primaryDocument'][0] = 'xslF345X05/a.xml'
    fake_get(monkeypatch,sec_responses,{'a.xml':FORM4,'b.xml':FORM4})
    result=cc.insiders('ABC',datetime(2026,9,25,tzinfo=timezone.utc))
    r=next(r for r in result['rows'] if r['accession']=='0001-01' and r['code']=='P')
    assert r['filed']=='2026-09-11' and r['delay_days']==1
    assert r['accepted_at']=='2026-09-11T18:00:00Z' and '/xsl' not in r['url']
    assert result['complete'] and result['available_at']=='2026-09-25T00:00:00+00:00'


def test_amendment_is_read_but_not_added_to_original_totals(monkeypatch,sec_responses):
    sec_responses['filings']['recent']['form'][1]='4/A'
    fake_get(monkeypatch,sec_responses,{'a.xml':FORM4,'b.xml':FORM4.replace('<ownershipDocument>','<ownershipDocument><documentType>4/A</documentType>')})
    result=cc.insiders('ABC',datetime(2026,9,25,tzinfo=timezone.utc))
    assert result['amendments']==1 and result['buys']['value'] is None
    assert any(r['amended'] for r in result['rows']) and result['cluster']['status']=='needs_review'


def test_duplicate_accession_is_read_once(monkeypatch,sec_responses):
    recent=sec_responses['filings']['recent']
    recent['accessionNumber'][1]=recent['accessionNumber'][0]
    recent['filingDate'][1]=recent['filingDate'][0]
    recent['primaryDocument'][1]=recent['primaryDocument'][0]
    calls=fake_get(monkeypatch,sec_responses,{'a.xml':FORM4})
    result=cc.insiders('ABC',datetime(2026,9,25,tzinfo=timezone.utc))
    assert len(calls)==2 and result['filings']==1 and result['buys']['rows']==1


def test_conflicting_accession_metadata_is_not_complete_coverage(monkeypatch,sec_responses):
    sec_responses['filings']['recent']['accessionNumber'][1]='0001-01'
    fake_get(monkeypatch,sec_responses,{'a.xml':FORM4})
    result=cc.insiders('ABC',datetime(2026,9,25,tzinfo=timezone.utc))
    assert not result['complete'] and result['malformed_filings']==1
    assert result['cluster']['status']=='needs_review'


@pytest.mark.parametrize('problem',['invalid_date','future_trade','unsafe_path','archive_gap','invalid_quantity'])
def test_unresolved_data_prevents_complete_coverage_claim(monkeypatch,sec_responses,problem):
    xml=FORM4
    if problem=='invalid_date': xml=xml.replace('2026-09-10','invalid')
    if problem=='future_trade': xml=xml.replace('2026-09-10','2026-09-20')
    if problem=='unsafe_path': sec_responses['filings']['recent']['primaryDocument'][1]='../b.xml'
    if problem=='archive_gap': sec_responses['filings']['files']=[{'name':'old-page.json'}]
    if problem=='invalid_quantity': xml=xml.replace('<value>1000</value>','<value>-1</value>')
    fake_get(monkeypatch,sec_responses,{'a.xml':xml,'b.xml':xml})
    result=cc.insiders('ABC',datetime(2026,9,25,tzinfo=timezone.utc))
    assert not result['complete'] and result['cluster']['status']=='needs_review'


def test_all_transaction_types_survive_and_ui_rows_are_bounded():
    rows=[row(code='F',date='2026-09-10') for _ in range(110)]
    result=summary(rows)
    assert len(result['rows'])==100 and result['rows_truncated'] and result['row_count']==110
    assert result['buys']['rows']==0 and result['sells']['rows']==0
