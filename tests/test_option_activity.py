"""Local statements never become permission, inferred fills or a live P&L input."""
import copy
import csv
import io

import pytest

import app as desk
import option_activity as activity
import option_lifecycle as life
from test_execution_repairs import execution, BASE  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_option_lifecycle import options, entered  # noqa: F401


HEADERS = ['Account ID','Asset Class','Currency','Conid','Symbol','Date','Transaction Type','Quantity','Trade ID','Realized P/L','Comm/Tax','Underlying Symbol']


def report(env, changes=None, headers=None, repeat=1):
    row = dict(zip(HEADERS,[env.identity['account_id'],'OPT','USD','100','TEST C100',
               life.now_utc().astimezone(life.paper_loop.NY_TZ).date().isoformat(),'Expiration','-2','12345','-200','-1.20','TEST']))
    row.update(changes or {})
    out=io.StringIO(); writer=csv.writer(out); fields=headers or HEADERS
    writer.writerow(fields)
    for _ in range(repeat): writer.writerow([row.get(k,'') for k in fields])
    return out.getvalue()


def preview(env,text=None):
    return desk.app.test_client().post('/api/live-agent/options/activity/preview',base_url=BASE,
                json=dict(csv=text if text is not None else report(env),filename='activity.csv'))


def save(token):
    return desk.app.test_client().post('/api/live-agent/options/activity/save',base_url=BASE,
                                    json=dict(token=token,acknowledge=True))


def test_preview_save_reload_preserves_active_controls_and_risk_ledger(live):
    before=copy.deepcopy(desk.load_config()); ledger=copy.deepcopy(desk.load_ledger())
    result=preview(live)
    assert result.status_code==200,result.json
    assert result.json['added']==1 and result.json['by_currency']['USD']['realized_pnl']=='-200'
    assert live.identity['account_id'] not in result.get_data(as_text=True)
    assert not (desk.DATA_DIR/'option_activity_evidence.json').exists()
    done=save(result.json['token'])
    assert done.status_code==200,done.json
    response=desk.app.test_client().get('/api/live-agent/options/activity',base_url=BASE)
    assert response.json['total_rows']==1
    assert desk.load_config()==before and desk.load_ledger()==ledger and not live.sent
    assert before['live_agent']['enabled'] is True


def test_duplicate_upload_rows_and_reimport_are_not_counted_twice(live):
    first=preview(live,report(live,repeat=2)); assert first.json['total_rows']==1
    assert save(first.json['token']).status_code==200
    again=preview(live,report(live,repeat=2));assert again.json['added']==0 and again.json['already_stored']==1
    assert save(again.json['token']).status_code==200
    raw=activity.load(desk);assert len(raw['events'])==1 and len(raw['reports'])==1


def test_changed_statement_is_preserved_as_conflict_not_overwritten(live):
    assert save(preview(live).json['token']).status_code==200
    result=preview(live,report(live,{'Realized P/L':'-250'}))
    assert result.json['conflicting_events']==1 and result.json['by_currency']=={}
    assert save(result.json['token']).status_code==200
    records=activity.load(desk)['events']
    assert {r['event']['realized_pnl'] for r in records.values()}=={'-200','-250'}
    assert all(r['conflict'] for r in result.json['rows'])


@pytest.mark.parametrize('column,value',[
    ('Account ID','OTHER'),('Asset Class','FUT'),('Transaction Type','Pending Exercise'),
    ('Currency','US'),('Conid','0'),('Conid','NaN'),('Trade ID',''),('Trade ID','=cmd'),
    ('Quantity','0'),('Quantity','1.5'),('Quantity','NaN'),('Quantity','Infinity'),
    ('Quantity','1e2'),('Quantity','1,000'),('Realized P/L','--10'),('Realized P/L','1e999'),
    ('Date','09/26/2026'),('Date','2099-12-31'),('Symbol',''),('Underlying Symbol','x'*101),
])
def test_unsupported_or_ambiguous_rows_reject_whole_import(live,column,value):
    result=preview(live,report(live,{column:value}))
    assert result.status_code==400,result.json
    assert not (desk.DATA_DIR/'option_activity_evidence.json').exists() and not live.sent


@pytest.mark.parametrize('text',['','a,b\n1,2','<xml>not csv</xml>','\x00','x'*1_000_001],ids=['empty','headers','xml','nul','too_large'])
def test_bad_file_rejected_without_store(live,text):
    assert preview(live,text).status_code==400
    assert not (desk.DATA_DIR/'option_activity_evidence.json').exists()


@pytest.mark.parametrize('header,change',[('Level Of Detail','SUMMARY'),('Model','allocation')])
def test_summary_and_model_rows_rejected(live,header,change):
    assert preview(live,report(live,{header:change},HEADERS+[header])).status_code==400


def test_duplicate_alias_headers_and_ragged_data_rejected(live):
    assert preview(live,report(live,{'ClientAccountID':live.identity['account_id']},HEADERS+['ClientAccountID'])).status_code==400
    assert preview(live,report(live)+'broken,row\n').status_code==400


def test_missing_amounts_are_unknown_and_currency_is_not_combined(live):
    first=preview(live,report(live,{'Realized P/L':'','Comm/Tax':''}));assert save(first.json['token']).status_code==200
    row=first.json['rows'][0];assert row['realized_pnl'] is None and row['commission_tax'] is None
    assert first.json['by_currency']['USD']['realized_pnl'] is None
    second=preview(live,report(live,{'Currency':'EUR','Trade ID':'987','Realized P/L':'1.23'}))
    assert second.json['by_currency']['EUR']['realized_pnl']=='1.23'
    assert second.json['by_currency']['USD']['pnl_missing']==1


def test_statement_matches_contract_but_never_clears_hold_or_creates_fill(options):
    entered(options)
    raw=options.live.service.load();raw['option_positions']['entry']['hold']='Possible assignment'
    options.live.service.save(raw)
    before=copy.deepcopy(raw);ledger=copy.deepcopy(desk.load_ledger())
    result=preview(options.live)
    assert result.json['rows'][0]['match_count']==1
    assert save(result.json['token']).status_code==200
    assert options.live.service.load()==before and desk.load_ledger()==ledger
    assert len(options.live.sent)==1


def test_same_contract_in_multiple_positions_is_ambiguous(options):
    entered(options);raw=options.live.service.load()
    other=copy.deepcopy(raw['option_positions']['entry']);other['id']='other'
    raw['option_positions']['other']=other;options.live.service.save(raw)
    result=preview(options.live)
    assert result.json['rows'][0]['match_count']==2
    assert 'ambiguous' in result.json['rows'][0]['match']


def test_stock_delivery_is_evidence_without_guessed_option_pair(live):
    result=preview(live,report(live,{'Asset Class':'STK','Transaction Type':'Buy','Quantity':'200','Symbol':'TEST','Conid':'9'}))
    assert result.status_code==200 and result.json['rows'][0]['match_count']==0
    assert not live.sent


@pytest.mark.parametrize('change',['account','paper_mode','expired','store'])
def test_save_rechecks_owner_expiry_and_store(live,change,monkeypatch):
    first=preview(live)
    if change in ('account','paper_mode'):
        cfg=desk.load_config();cfg['broker_identity'][change if change=='paper_mode' else 'account_id']='OTHER' if change=='account' else False
        desk.save_config(cfg)
    elif change=='expired':
        original=activity.time.monotonic;monkeypatch.setattr(activity.time,'monotonic',lambda:original()+125)
    else:
        second=preview(live,report(live,{'Trade ID':'second'}));assert save(second.json['token']).status_code==200
    assert save(first.json['token']).status_code==400
    assert save(first.json['token']).status_code==400 and not live.sent


def test_explicit_ack_get_cross_site_and_single_use(live):
    client=desk.app.test_client();token=preview(live).json['token']
    assert client.post('/api/live-agent/options/activity/save',base_url=BASE,json=dict(token=token,acknowledge=False)).status_code==400
    assert client.get('/api/live-agent/options/activity/save',base_url=BASE).status_code==405
    assert client.post('/api/live-agent/options/activity/save',base_url=BASE,json=dict(token=token,acknowledge=True),headers={'Origin':'https://foreign.example'}).status_code==403
    assert save(token).status_code==200
    assert save(token).status_code==400


def test_real_account_evidence_does_not_change_enabled_live_controls(live):
    cfg=desk.load_config();cfg['broker_identity'].update(account_id='U_REVIEW',paper_mode=False)
    desk.save_config(cfg);live.identity=cfg['broker_identity']
    result=preview(live);assert result.status_code==200 and result.json['paper_mode'] is False
    assert save(result.json['token']).status_code==200
    assert desk.load_config()==cfg and not live.sent


def test_foreign_selected_account_does_not_display_prior_rows(live):
    assert save(preview(live).json['token']).status_code==200
    cfg=desk.load_config();cfg['broker_identity']['account_id']='OTHER';desk.save_config(cfg)
    result=desk.app.test_client().get('/api/live-agent/options/activity',base_url=BASE)
    assert result.json['rows']==[] and result.json['by_currency']=={}


def test_corrupted_saved_evidence_cannot_be_replaced_by_upload(live):
    assert save(preview(live).json['token']).status_code==200
    raw=activity.load(desk);next(iter(raw['events'].values()))['event']['quantity']='900'
    desk._save_json(desk.DATA_DIR/'option_activity_evidence.json',raw)
    assert preview(live).status_code==400
    assert next(iter(desk._load_json(desk.DATA_DIR/'option_activity_evidence.json',{})['events'].values()))['event']['quantity']=='900'


def test_token_cache_is_bounded(live):
    tokens=[preview(live).json['token'] for _ in range(6)]
    assert save(tokens[0]).status_code==400
    assert save(tokens[-1]).status_code==200


def test_csv_bom_camelcase_headers_and_compact_dates(live):
    content=report(live,{'Date':'20260926'}).replace('Account ID','ClientAccountID').replace('Asset Class','AssetClass').replace('Realized P/L','FifoPnlRealized')
    assert preview(live,'\ufeff'+content).status_code==200


def test_unreadable_optional_evidence_does_not_change_execution_corruption_gate(live):
    path=desk.DATA_DIR/'option_activity_evidence.json';path.write_text('{broken',encoding='utf-8')
    before=set(desk._CORRUPT_PATHS);cfg=copy.deepcopy(desk.load_config())
    assert preview(live).status_code==400
    assert set(desk._CORRUPT_PATHS)==before and desk.load_config()==cfg
    assert path.read_text(encoding='utf-8')=='{broken'


def test_success_is_not_reported_if_store_write_did_not_happen(live,monkeypatch):
    token=preview(live).json['token']
    monkeypatch.setattr(desk,'_save_json',lambda *args:None)
    result=save(token)
    assert result.status_code==400 and 'confirmed' in result.json['error']


def test_statement_evidence_is_backed_up_and_readable_after_restore(live,tmp_path):
    import json
    import zipfile
    from types import SimpleNamespace
    import desk_backups
    assert save(preview(live).json['token']).status_code==200
    before=activity.load(desk)
    receipt=desk_backups.create(desk)
    archive=desk.DATA_DIR/'backups'/receipt['archive']
    manifest=desk_backups.verify(archive)
    assert 'option_activity_evidence.json' in manifest['files']
    restored=tmp_path/'restore-evidence';restored.mkdir()
    with zipfile.ZipFile(archive) as z:
        content=z.read('option_activity_evidence.json')
    assert json.loads(content)==before
    (restored/'option_activity_evidence.json').write_bytes(content)
    assert activity.load(SimpleNamespace(DATA_DIR=restored,_CORRUPT_PATHS=set()))==before
