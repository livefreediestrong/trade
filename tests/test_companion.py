import copy
import datetime as dt
import socket
import pandas as pd
import pytest

import app as desk
import data_sources as ds
import research_companion as rc
import research_learning as learning
from trade_planner import estimate

BASE = "http://127.0.0.1:5056"


def test_templates_keep_existing_desk_usable_until_backend_restart(monkeypatch):
    from flask import render_template
    with desk.app.test_request_context('/'):
        page=render_template('index.html')
        assert 'id="moss-desk"' in page and '/static/companion.js' in page
        sidebar_start = page.index('<nav class="page-nav')
        for marker in ('id="moss-fox"', 'id="moss-woman"', 'id="moss-speech"'):
            assert sidebar_start < page.index(marker) < page.index('</nav>', sidebar_start)
            assert page.count(marker) == 1
        monkeypatch.delitem(desk.app.config,'COMPANION_AVAILABLE')
        old_backend=render_template('index.html')
        assert 'after the next app restart' in old_backend
        assert 'id="moss-desk"' not in old_backend and '/static/companion.js' not in old_backend
        assert 'id="paper-sizing-form"' not in old_backend and 'id="cost-planner"' not in old_backend
        assert 'id="desk-live"' in old_backend and 'id="desk-paper"' in old_backend


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(desk, "DATA_DIR", tmp_path)
    for name in ("CONFIG", "LEDGER", "SIGNALS", "JOURNAL", "DECISIONS", "LESSONS"):
        monkeypatch.setattr(desk, name+"_PATH", tmp_path/(name.lower()+".json"))
    monkeypatch.setattr(desk, "_CORRUPT_PATHS", set())
    monkeypatch.setattr(desk, "_QUOTE_SNAPSHOTS", {})
    monkeypatch.setattr(desk, "_MARKS", {})
    monkeypatch.setattr(socket.socket, "connect", lambda *a, **k: pytest.fail("Network forbidden"))
    monkeypatch.setattr(desk, "live_broker_place_order", lambda *a, **k: pytest.fail("Broker writes forbidden"))
    monkeypatch.setattr(desk, "fetch_last_price", lambda *a:100.)
    monkeypatch.setattr(desk, "_broker_book_cached", lambda: {"equity":110., "day_pnl_usd":0, "risk_ready":True})
    cfg=dict(desk.load_config(), mode="live_manual", session_active=False, rth_only=False,
             paper_research_enabled=True, paper_auto_approve=True, paper_fractional_enabled=True,
             paper_order_budget=10., fee_bps=10, slip_bps=5, risk_per_trade_pct=0)
    desk.save_config(cfg)
    desk.save_ledger(dict(desk.load_ledger(), cash=1000.,equity=1000.,positions=[],fills=[]))
    return desk.app.test_client()


def signal(side="buy", qty=.1):
    now=dt.datetime.now(dt.timezone.utc)
    return {"id":"fraction", "ticker":"TEST", "workspace":"paper", "side":side,
            "suggested_shares":qty, "signal_price":100., "status":"pending", "confidence":.9,
            "verdict":"PASS", "quote":ds.quote_snapshot(100,now,"fixture"),
            "expires_at":(now+dt.timedelta(minutes=15)).isoformat()}


def test_fractional_paper_entry_includes_costs_and_closes_completely(isolated):
    original=copy.deepcopy(desk.load_config())
    sig=signal(qty=2)
    cfg=desk.paper_research_config(original)
    desk._size_paper_research(sig,cfg,desk.load_ledger())
    assert 0 < sig["suggested_shares"] < .1
    result=desk.paper_fill(sig,cfg,"auto_paper")
    assert result["ok"],result
    fill=result["fill"]
    assert 0 < fill["shares"] < .1
    assert fill["notional"]+fill["fee_usd"] <= 10.0001
    assert desk.load_config()==original
    # Turning off fractional entry must not strand a fractional holding.
    desk.save_config(dict(original,paper_fractional_enabled=False,paper_order_budget=1))
    closed=desk.paper_flatten_all()
    assert not desk.load_ledger()["positions"],closed
    assert not desk.load_ledger().get("broker_fills")
    assert desk.load_config()["mode"]=="live_manual"


def test_fractional_partial_sale_keeps_remainder_and_stop_exits(isolated):
    cfg=desk.paper_research_config(desk.load_config())
    assert desk.paper_fill(signal(),cfg,"auto_paper")["ok"]
    held=desk.load_ledger()["positions"][0]["shares"]
    assert desk.paper_fill(signal("sell",.025),cfg,"manual_approve")["ok"]
    ledger=desk.load_ledger()
    assert ledger["positions"][0]["shares"]==pytest.approx(held-.025)
    ledger["positions"][0]["stop_price"]=101
    desk.save_ledger(ledger)
    desk.check_paper_exit_intents(cfg)
    assert not desk.load_ledger()["positions"]


def test_fractional_config_change_during_quote_requires_new_research(isolated,monkeypatch):
    cfg=desk.paper_research_config(desk.load_config())
    def quote(_):
        desk.save_config(dict(desk.load_config(),paper_order_budget=1))
        return 100
    monkeypatch.setattr(desk,"fetch_last_price",quote)
    result=desk.paper_fill(signal(),cfg,"auto_paper")
    assert not result["ok"] and result["requires_review"]
    assert not desk.load_ledger()["fills"]


def test_sizing_controls_preserve_live_settings(isolated):
    before=desk.load_config()
    r=isolated.post('/api/paper-research',base_url=BASE,json={"fractional_enabled":False,"order_budget":5})
    assert r.status_code==200
    assert desk.load_config()==dict(before,paper_fractional_enabled=False,paper_order_budget=5.)
    for payload in ({"order_budget":float('nan')},{"order_budget":-1},{"fractional_enabled":"true"}):
        assert isolated.post('/api/paper-research',base_url=BASE,json=payload).status_code==400


def test_unknown_fees_do_not_invent_total_or_profit():
    result=estimate({"budget":10,"price":100,"exit_price":110})
    assert 0 < result['shares'] < .1
    assert result['cash_needed_estimate'] is None and result['net_pnl_estimate'] is None
    assert result['break_even_price'] is None and not result['all_in_guaranteed']


def test_costs_and_short_scenarios_use_both_fees_and_correct_direction():
    values={"budget":10,"price":100,"exit_price":110,"fee_mode":"custom","entry_fee":.1,"exit_fee":.1,"slippage_bps":0}
    long=estimate(values)
    assert long['shares']==.099 and long['cash_needed_estimate']==10
    assert long['net_pnl_estimate']==.79
    short=estimate(dict(values,direction="short",exit_price=90,borrow_fee=.05))
    assert short['net_pnl_estimate']==.75 and short['cash_needed_estimate'] is None
    assert short['short_note']


@pytest.mark.parametrize('extra',[{'fractional':False}, {'entry_fee':20}])
def test_unaffordable_scenario_does_not_charge_fees_for_zero_shares(extra):
    values=dict(budget=10,price=100,fee_mode='custom',entry_fee=.1,exit_fee=.1)
    result=estimate(dict(values,**extra))
    assert not result['trade_possible'] and result['shares']==0
    assert result['cash_needed_estimate']==0 and result['net_pnl_estimate']==0 and result['fees_assumed']==0
    assert result['break_even_price'] is None and 'No trade' in result['note']


@pytest.mark.parametrize('value',[True,-1,float('inf'),float('nan'),'bad',None])
def test_costs_reject_invalid_values(value):
    with pytest.raises(ValueError):estimate({"budget":value,"price":100})


def event(i=1,**changes):
    return dict({"id":str(i),"ticker":"TEST","intended_side":"buy","llm_model":"real-v1",
                 "prompt_version":"v3","workspace":"research","verdict":"PASS","horizon_min":20,
                 "input_hash":"hash"+str(i),"ts":"2026-09-23T14:00:00+00:00","outcome_ts":"2026-09-23T14:20:00+00:00",
                 "outcome_executable_move_bps":10,"outcome_status":"scored","scoring_version":"horizon-net-v2"},**changes)


def test_learning_updates_real_parameters_with_provenance_and_no_leakage():
    now=dt.datetime(2026,9,23,15,tzinfo=dt.timezone.utc)
    rows=[event(i) for i in range(20)]
    result=learning.train(rows+[rows[0],event(22,mock=True),event(23,outcome_ts='2026-09-23T16:00:00+00:00'),event(24,error='bad')],now)
    assert result['qualified_samples']==20 and result['excluded']==4
    w=result['weights'][0]
    assert w['alpha']==22 and w['beta']==2 and w['weight']==1.5
    assert learning.train(list(reversed(rows)),now)['evidence_hash']==learning.train(rows,now)['evidence_hash']
    assert learning.train(rows[:3],now)['weights'][0]['weight']==1
    # Many tiny wins plus a large loss cannot boost a negative-average setup.
    assert learning.train(rows+[event(50,outcome_executable_move_bps=-1000)],now)['weights'][0]['weight']<=1
    assert len(learning.train(rows+[event(40,llm_model='other')],now)['weights'])==2
    assert learning.train([event(outcome_tolerance_sec=0,outcome_ts='2026-09-23T14:20:01+00:00')],now)['qualified_samples']==0


def test_history_excludes_unfinished_and_future_bars():
    bars=pd.DataFrame({'Close':[90,100,300,500]},index=pd.to_datetime(['2026-09-21','2026-09-22','2026-09-23','2026-09-24']))
    now=dt.datetime(2026,9,23,14,tzinfo=dt.timezone.utc)
    result=rc.historical_observation('TEST',bars,'fixture',now)
    assert result['as_of']=='2026-09-22' and result['close']==100 and not result['stale']
    assert result['sample_days']==2


def test_schedule_holidays_early_close_and_restart_idempotency(isolated):
    service=rc.Companion(desk)
    assert not service.due(dt.datetime(2026,11,26,16,tzinfo=dt.timezone.utc))
    assert not service.due(dt.datetime(2026,11,27,19,tzinfo=dt.timezone.utc))
    now=dt.datetime(2026,11,27,16,tzinfo=dt.timezone.utc)
    assert service.due(now)
    data=service.load();data['briefs']=[{'session_day':'2026-11-27','scheduled':True}];service.save(data)
    assert not rc.Companion(desk).due(now)


def test_notebook_retains_context_and_does_not_trade(isolated,monkeypatch):
    service=rc.Companion(desk)
    now=dt.datetime(2026,9,23,22,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(rc,'now_utc',lambda:now)
    bars=pd.DataFrame({'Close':[99,100]},index=pd.to_datetime(['2026-09-22','2026-09-23']))
    monkeypatch.setattr(ds,'get_daily_with_fallback',lambda *a,**k:(bars,'fixture'))
    monkeypatch.setattr(desk.news_stream,'news_for_symbol',lambda *a,**k:[])
    monkeypatch.setattr(desk,'_research_thesis',lambda *a,**k:pytest.fail('No new model entry advice after close'))
    before=(desk.load_config(),desk.load_ledger(),desk.load_signals())
    service._work(False)
    saved=service.load()
    assert len(saved['briefs'])==1 and saved['learned_model']['qualified_samples']==0
    assert saved['briefs'][0]['account_context']['daily_pnl']==0
    assert before==(desk.load_config(),desk.load_ledger(),desk.load_signals())
    assert rc.Companion(desk).load()==saved


def test_no_data_is_not_a_successful_research_day(isolated,monkeypatch):
    service=rc.Companion(desk)
    monkeypatch.setattr(ds,'get_daily_with_fallback',lambda *a,**k:(None,'unavailable'))
    monkeypatch.setattr(desk.news_stream,'news_for_symbol',lambda *a,**k:[])
    saved=service.load();saved['settings']['use_model']=False;service.save(saved)
    service._work(True)
    assert not service.load()['briefs'] and service.last_error


def test_ranked_research_uses_actual_default_model_and_retries_stale_days(isolated,monkeypatch):
    service=rc.Companion(desk)
    now=dt.datetime(2026,9,23,22,tzinfo=dt.timezone.utc)
    monkeypatch.setattr(rc,'now_utc',lambda:now)
    monkeypatch.setattr(desk,'_llm_public_status',lambda cfg:{'model':'real-v1'})
    desk.save_config(dict(desk.load_config(),watchlist=['AAA','ZZZ'],llm_model=None))
    desk._save_json(desk.DECISIONS_PATH,{'events':[event(i,ticker='ZZZ',prompt_version=desk.llm_trader.PROMPT_VERSION) for i in range(20)]})
    bars=pd.DataFrame({'Close':[99,100]},index=pd.to_datetime(['2026-09-22','2026-09-23']))
    calls=[]
    def history(symbol,**kwargs):
        calls.append(symbol)
        return bars,'fixture'
    monkeypatch.setattr(ds,'get_daily_with_fallback',history)
    monkeypatch.setattr(desk.news_stream,'news_for_symbol',lambda *a,**k:[])
    service._work(False)
    assert calls==['ZZZ','AAA']
    assert service.load()['learned_model']['qualified_samples']==20
    bars.index=pd.to_datetime(['2026-09-19','2026-09-21'])
    before=service.load()
    service._work(True)
    assert service.load()==before and service.last_error


def test_saved_rehearsal_cannot_arm_or_mutate_live_configuration(isolated):
    before=copy.deepcopy(desk.load_config())
    result=isolated.post('/api/companion/plan',base_url=BASE,json=dict(rc.PLAN,fractional=True,short_selling=True))
    assert result.status_code==200 and result.json['armed'] is False
    assert isolated.post('/api/companion/plan',base_url=BASE,json={'armed':True}).status_code==400
    review=isolated.post('/api/companion/rehearse',base_url=BASE,json={})
    assert review.json['submitted']==0 and review.json['armed'] is False
    assert desk.load_config()==before and not desk.load_ledger()['fills']
    assert isolated.post('/api/companion/settings',base_url=BASE,json={'daily_target':1000}).status_code==200
    assert desk.load_config()==before


def notebook_model_inputs(monkeypatch):
    import market_catalog
    import market_discovery
    import screener_logic
    service = rc.Companion(desk)
    now = dt.datetime(2026, 9, 23, 18, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(rc, 'now_utc', lambda: now)
    monkeypatch.setattr(market_catalog, 'notebook_observations', lambda *a: [])
    monkeypatch.setattr(market_discovery, 'candidates', lambda *a: [])
    monkeypatch.setattr(desk.news_stream, 'news_for_symbol', lambda *a, **k: [])
    monkeypatch.setattr(desk, '_llm_public_status', lambda cfg: {'model':'fixture'})
    monkeypatch.setattr(screener_logic, 'analyze_ticker', lambda symbol: {'ticker':symbol,'quote':{'fresh':True}})
    desk.save_config(dict(desk.load_config(), watchlist=['AAA'], llm_enabled=True, llm_on_scan=True, moss_paper={'enabled':False}))
    bars = pd.DataFrame({'Close':[99, 100]}, index=pd.to_datetime(['2026-09-21', '2026-09-22']))
    monkeypatch.setattr(ds, 'get_daily_with_fallback', lambda *a, **k: (bars, 'fixture'))
    calls = []
    monkeypatch.setattr(desk, '_research_thesis', lambda *a, **k: calls.append(k['source']) or {'side':'flat','thesis':'saved result'})
    return service, bars, calls, now


def test_stale_notebook_retries_do_not_start_model_work(isolated, monkeypatch):
    service, bars, calls, now = notebook_model_inputs(monkeypatch)
    bars.index = pd.to_datetime(['2026-09-18', '2026-09-21'])
    service._work(True)
    service._work(True)
    assert calls == [] and not service.load()['briefs'] and service.due(now)
    assert 'latest completed session' in service.last_error


@pytest.mark.parametrize('revocation', ['enabled', 'use_model', 'llm_enabled', 'llm_on_scan'])
def test_notebook_rechecks_current_permission_after_analysis(isolated, monkeypatch, revocation):
    import screener_logic
    service, bars, calls, _ = notebook_model_inputs(monkeypatch)
    def analyze(symbol):
        if revocation in ('enabled', 'use_model'):
            current = service.load(); current['settings'][revocation] = False; service.save(current)
        else:
            desk.save_config(dict(desk.load_config(), **{revocation:False}))
        return {'ticker':symbol, 'quote':{'fresh':True}}
    monkeypatch.setattr(screener_logic, 'analyze_ticker', analyze)
    service._work(True)
    assert calls == []
    briefs = service.load()['briefs']
    if revocation == 'enabled':
        assert not briefs
    else:
        assert len(briefs) == 1 and briefs[0]['model'] is None


def test_scheduled_model_result_survives_notebook_save_retry_and_restart(isolated, monkeypatch):
    service, bars, calls, _ = notebook_model_inputs(monkeypatch)
    save = service.save
    def fail_brief(current):
        if current['briefs']:
            raise ValueError('Synthetic notebook save failure')
        save(current)
    monkeypatch.setattr(service, 'save', fail_brief)
    service._work(True)
    assert calls == ['moss_notebook'] and not service.load()['briefs']
    assert service.load()['model_attempts']['2026-09-23']['status'] == 'completed'
    restarted = rc.Companion(desk)
    restarted._work(True)
    assert calls == ['moss_notebook']
    assert restarted.load()['briefs'][0]['model']['thesis'] == 'saved result'


def test_unfinished_scheduled_model_attempt_is_not_reissued(isolated, monkeypatch):
    service, bars, calls, _ = notebook_model_inputs(monkeypatch)
    saved = service.load()
    saved['model_attempts'] = {'2026-09-23':{'status':'started','started_at':'2026-09-23T17:00:00+00:00'}}
    service.save(saved)
    service._work(True)
    assert calls == []
    assert 'no duplicate request' in service.load()['briefs'][0]['model']['error']
