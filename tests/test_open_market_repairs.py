"""Regression checks for the open-market review. Fake transport only."""
import copy
from datetime import datetime, timedelta, timezone

import pytest
import app as desk
import live_agent as agent
import auto_live_options as options
import broker_router as router
import risk_policy
from test_execution_repairs import execution  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_option_lifecycle import options as option_env, signal as option_signal


def config():
    return dict(risk_preset='high', mode='auto_live', session_active=True,
                broker_identity=dict(broker='ibkr', account_id='TEST', paper_mode=False),
                live_agent=dict(enabled=True, policy=dict(agent.DEFAULTS),
                                auto_options=options.validate(dict(enabled=True))))


def test_effective_limits_preserve_user_config_and_account_cap():
    cfg=config(); before=copy.deepcopy(cfg)
    limits=risk_policy.effective_limits(cfg, 110)
    assert limits['max_order_usd']==4.4 and limits['max_daily_loss_usd']==4.4
    assert limits['max_orders_per_day']==12 and cfg==before
    cfg['live_agent']['policy'].update(max_order_usd=2, max_daily_loss_usd=1, max_orders_per_day=3)
    limits=risk_policy.effective_limits(cfg,110)
    assert (limits['max_order_usd'],limits['max_daily_loss_usd'],limits['max_orders_per_day'])==(2,1,3)


@pytest.mark.parametrize('equity',[None,0,-1,float('nan'),float('inf'),'bad'])
def test_unknown_equity_never_shows_unlimited_limits(equity):
    limits=risk_policy.effective_limits(config(),equity)
    assert not limits['verified'] and limits['max_order_usd'] is None
    assert risk_policy.option_entry_error(config(),equity,1)


def test_option_maximum_loss_is_capped_not_just_premium():
    assert risk_policy.option_entry_error(config(),110,450,agent=True)
    assert risk_policy.option_entry_error(config(),110,4.4,agent=True) is None
    assert risk_policy.option_entry_error(config(),110,float('inf'))


def test_stock_limit_offset_cannot_exceed_position_budget():
    sig=dict(side='buy',suggested_shares=1,signal_price=4.4,review_order=dict(type='limit',limit=4.41))
    qty,_,error=desk._cap_shares_for_broker(sig,config(),{},equity_override=110,allow_fetch=False)
    assert qty==0 and error


def test_real_options_setting_is_preserved_but_not_armed():
    cfg=config(); before=copy.deepcopy(cfg)
    payload=options.status_payload(cfg)
    assert payload['requested'] and not payload['armed']
    assert 'qualification' in payload['entry_block']
    assert not payload['lifecycle']['automatic_entries'] and cfg==before
    cfg['live_agent']['auto_options']['automatic_exits']=True
    assert not options.status_payload(cfg)['armed'], 'A checkbox cannot qualify real-money exits'
    cfg['broker_identity']['paper_mode']=True
    assert options.status_payload(cfg)['armed']
    cfg['session_active']=False
    assert not options.status_payload(cfg)['armed']


def test_live_options_cannot_reach_terms_even_if_a_signal_is_injected():
    with pytest.raises(ValueError,match='qualification'):
        options.execution_terms_option(dict(asset_type='OPT',contracts=1),config(),1,{},1e9)


def test_unavailable_symbol_is_skipped_after_restart_without_refunding_attempt(live,monkeypatch):
    def no_history(cfg,*,ticker,notes,**kw):
        live.calls.append(ticker)
        notes.update(error='Unavailable',coverage_unavailable=True)
    monkeypatch.setattr(desk,'generate_scan_signal',no_history)
    live.service.tick()
    raw=live.service.load()
    assert 'TEST' in raw['research_coverage'] and raw['days'][live.service.key(live.cfg)]['research']==1
    assert 0 < (datetime.fromisoformat(raw['next_at'])-datetime.now(timezone.utc)).total_seconds()<=30
    raw['next_at']=None; raw['cursor']=0; live.service.save(raw)
    restarted=agent.LiveAgent(desk); restarted.tick()
    assert live.calls==['TEST','OTHER'] and not live.sent
    raw=restarted.load(); raw['next_at']=None; restarted.save(raw); restarted.tick()
    assert live.calls==['TEST','OTHER'] and restarted.phase=='coverage_wait'
    assert restarted.status()['today']['research']==2


def test_provider_outage_does_not_accelerate_or_quarantine_every_symbol(live,monkeypatch):
    def outage(cfg,*,notes,**kw): notes.update(error='Provider timeout',coverage_unavailable=False)
    monkeypatch.setattr(desk,'generate_scan_signal',outage)
    live.service.tick();raw=live.service.load()
    assert not raw.get('research_coverage')
    assert (datetime.fromisoformat(raw['next_at'])-datetime.now(timezone.utc)).total_seconds()>90


def test_expired_symbol_cooldown_is_reconsidered(live):
    raw=live.service.load();raw['research_coverage']={'TEST':dict(reason='old',retry_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())}
    live.service.save(raw);live.service.tick()
    assert live.calls==['TEST'] and len(live.sent)==1


def test_options_hold_does_not_turn_off_live_stock_agent(live,monkeypatch):
    cfg=desk.load_config();cfg['live_agent']['auto_options']=options.validate(dict(enabled=True))
    desk.save_config(cfg)
    monkeypatch.setattr(agent.LiveAgent,'_maybe_convert_to_option',lambda *a:pytest.fail('Unprotected conversion'))
    live.service.tick()
    assert len(live.sent)==1 and live.sent[0].get('asset_type','STK')=='STK'
    current=desk.load_config()
    assert current['mode']=='auto_live' and current['session_active'] and current['live_agent']['enabled']


def test_real_options_refused_in_final_authorization_even_if_terms_bypassed(live):
    cfg=config()
    # Directly exercise the final agent guard with a valid account/revision.
    cfg['live_agent'].update(revision='p',run_id='r')
    sig=dict(ticker='SPY',source='live_agent',asset_type='OPT',agent_identity=copy.deepcopy(cfg['broker_identity']),
             agent_revision='p',agent_run_id='r')
    assert 'qualification' in live.service.trade_error(sig,cfg,0,False)


def test_option_order_path_enforces_small_account_cap_before_dispatch(option_env,monkeypatch):
    env=option_env
    cfg=desk.load_config();cfg['risk_preset']='high';desk.save_config(cfg)
    monkeypatch.setattr(desk,'_broker_day_pnl',lambda:(0.,110.,None))
    result=desk._ingest_one_signal(option_signal(env),cfg_override=cfg)
    assert not result.get('fill') and not env.live.sent
    assert 'effective account limit' in result.get('reject_reason',''),result


def test_risk_limit_change_before_dispatch_is_rechecked(option_env,monkeypatch):
    env=option_env; real=desk._broker_session_gate
    calls=[]
    def change(cfg,notional,*,reducing=False):
        calls.append(notional)
        if len(calls)==1:
            changed=desk.load_config();changed['live_agent']['policy']['max_order_usd']=1
            desk.save_config(changed)
        return real(cfg,notional,reducing=reducing)
    monkeypatch.setattr(desk,'_broker_session_gate',change)
    result=desk._ingest_one_signal(option_signal(env),cfg_override=env.cfg)
    assert not result.get('fill') and not env.live.sent
