"""Behavior tests of the isolated paper kernel; no broker or network access."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import socket
import inspect

import pytest
from pydantic import ValidationError

from quant_models import Fill, Portfolio, RiskConfig, Signal, Tick
from quant_risk import RiskManager, RiskStore
from quant_engine import PaperEngine, PaperGateway, MomentumAlpha, UnconfiguredBrokerGateway

BASE = datetime(2026, 9, 23, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    original = socket.socket.connect
    def blocked(*args, **kwargs):
        # Windows asyncio's own wake-up pipe uses a local socketpair.
        caller=inspect.currentframe().f_back
        if caller.f_code.co_name=='_fallback_socketpair' and caller.f_globals.get('__name__')=='socket':
            return original(*args,**kwargs)
        raise AssertionError('Network is forbidden in paper kernel tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)


@pytest.fixture
def kit(tmp_path):
    store = RiskStore(tmp_path / 'isolated.sqlite3', 'paper-test')
    yield store
    store.close()


def tick(at=BASE, symbol='SPY', **kwargs):
    return Tick(symbol=symbol, market_at=at, received_at=at, bid='99', ask='100',
                bid_size='100', ask_size='100', source='recorded_exchange', data_kind='recorded', **kwargs)


def portfolio(cash='1000', now=BASE, volatility='.01', flow='0'):
    return Portfolio(account='paper-test', as_of=now, equity=cash, cash=cash,
        gross_exposure='0', volatility=volatility, net_cash_flow=flow)


def signal(t, id='one', side='buy', budget='10', volatility='.01', samples=20):
    return Signal(id=id, symbol=t.symbol, side=side, created_at=t.received_at,
        market_at=t.market_at, expires_at=t.received_at+timedelta(seconds=5),
        strategy='test', budget=budget, volatility=volatility, volatility_samples=samples)


def reserve(risk, t=None, **kwargs):
    t=t or tick()
    assert risk.observe(t, t.received_at)
    result=risk.decide(signal(t, **kwargs), t, portfolio(now=t.received_at), t.received_at)
    assert result.approved, result.reason
    return result.order


def test_fractional_budget_includes_fee_and_reservation_survives_restart(kit):
    risk=RiskManager(RiskConfig(), kit)
    order=reserve(risk)
    assert D(0) < order.quantity < D('.1')
    assert order.quantity * order.limit_price * D('1.001') <= 10
    assert kit.active()[0][1]=='reserved'
    restarted=RiskManager(RiskConfig(), kit)
    assert restarted.halted=='restart_requires_reconciliation'
    assert restarted.watchdog(BASE)==[order.id]


def test_volatility_sizes_down_and_ratcheting_drawdown_persists(kit):
    cfg=RiskConfig(max_position_fraction='.5', max_gross_fraction='.8', max_order_notional='500')
    risk=RiskManager(cfg, kit)
    low=reserve(risk, budget='500', volatility='.02')
    kit.status(low.id,'cancelled')
    t=tick(BASE+timedelta(seconds=1))
    high=reserve(risk,t,id='two',budget='500',volatility='.04')
    assert high.quantity <= low.quantity / 2
    kit.status(high.id,'cancelled')
    risk.update_portfolio(portfolio('1100'),BASE)
    assert not risk.update_portfolio(portfolio('1070',volatility='.02'),BASE)
    assert risk.drawdown_limit==D('.02')
    assert risk.halted=='portfolio_drawdown'
    assert RiskManager(cfg,kit).halted=='portfolio_drawdown'


def test_external_cashflow_does_not_create_false_drawdown(kit):
    risk=RiskManager(RiskConfig(),kit)
    risk.update_portfolio(portfolio(),BASE)
    assert risk.update_portfolio(portfolio('900',flow='-100'),BASE)
    assert risk.drawdown==0


@pytest.mark.parametrize('change', [
    {'market_at': BASE+timedelta(seconds=1), 'received_at':BASE+timedelta(seconds=1)},
    {'market_at': BASE-timedelta(seconds=10), 'received_at':BASE},
    {'halted':True}, {'source':'mock provider'},
    {'market_at':BASE.replace(hour=3), 'received_at':BASE.replace(hour=3)}])
def test_bad_ticks_do_not_refresh_watchdog(kit,change):
    clock=[0.]
    risk=RiskManager(RiskConfig(),kit,monotonic=lambda:clock[0])
    bad=Tick.model_validate({**tick().model_dump(),**change})
    assert not risk.observe(bad,BASE)
    clock[0]=11
    risk.watchdog(BASE)
    assert risk.halted=='stale_data_heartbeat'


def test_same_timestamp_cannot_swap_price_after_validation(kit):
    risk=RiskManager(RiskConfig(),kit)
    t=tick();risk.observe(t,BASE)
    changed=Tick.model_validate({**t.model_dump(),'ask':'101'})
    assert risk.decide(signal(t),changed,portfolio(),BASE).reason=='unverified_or_stalled_feed'
    assert not risk.observe(t,BASE)


def test_cash_reservations_bound_many_symbols_and_duplicate_signals(kit):
    cfg=RiskConfig(max_position_fraction='.6',max_gross_fraction='.8',max_order_notional='800',risk_fraction='.02')
    risk=RiskManager(cfg,kit)
    first=reserve(risk,budget='800')
    second=reserve(risk,tick(symbol='QQQ'),id='two',budget='800')
    reserved=(first.quantity*first.limit_price+second.quantity*second.limit_price)*D('1.001')
    assert reserved <= 1000
    assert first.quantity*100+second.quantity*100 <= 800
    t=tick();assert risk.decide(signal(t),t,portfolio(),BASE).reason=='duplicate_signal'


def test_no_naked_short_and_no_inadequate_sample(kit):
    risk=RiskManager(RiskConfig(),kit);t=tick();risk.observe(t,BASE)
    assert risk.decide(signal(t,side='sell'),t,portfolio(),BASE).reason=='no_capacity_or_owned_shares'
    assert risk.decide(signal(t,samples=3),t,portfolio(),BASE).reason=='insufficient_volatility_samples'


def test_partial_fills_next_quote_fee_dedup_and_terminal_lifecycle(kit):
    async def scenario():
        risk=RiskManager(RiskConfig(),kit);gateway=PaperGateway(risk)
        t=tick();risk.observe(t,BASE);await gateway.on_tick(t)
        order=risk.decide(signal(t),t,await gateway.snapshot(BASE),BASE).order
        await gateway.submit(order)
        with pytest.raises(Exception): await gateway.on_tick(t)
        later=Tick.model_validate({**tick(BASE+timedelta(seconds=1)).model_dump(),'ask_size':'.04'})
        risk.observe(later,later.received_at)
        fills=await gateway.on_tick(later)
        assert len(fills)==1 and fills[0].quantity==D('.04')
        assert kit.order(order.id)[1]=='partial'
        assert not kit.record_paper_fill(fills[0])
        bad=Fill.model_validate({**fills[0].model_dump(),'quantity':'.05'})
        with pytest.raises(ValueError,match='Conflicting'):kit.record_paper_fill(bad)
        assert D(kit.get('cash'))==D('995.996')
        await gateway.cancel(order.id)
        with pytest.raises(ValueError,match='Terminal'):kit.status(order.id,'working')
        assert kit.positions()=={'SPY':D('.04')}
    asyncio.run(scenario())


def test_drawdown_tick_cancels_before_new_fill(kit):
    async def scenario():
        cfg=RiskConfig(max_position_fraction='.8',max_gross_fraction='.9',risk_fraction='.02',max_order_notional='800')
        risk=RiskManager(cfg,kit);gateway=PaperGateway(risk)
        t=tick();risk.observe(t,BASE);await gateway.on_tick(t)
        order=risk.decide(signal(t,budget='600'),t,await gateway.snapshot(BASE),BASE).order
        await gateway.submit(order)
        later=tick(BASE+timedelta(seconds=1));risk.observe(later,later.received_at);await gateway.on_tick(later)
        next_t=tick(BASE+timedelta(seconds=2));risk.observe(next_t,next_t.received_at);await gateway.on_tick(next_t)
        second=risk.decide(signal(next_t,id='second',budget='50'),next_t,await gateway.snapshot(next_t.received_at),next_t.received_at).order
        assert second
        await gateway.submit(second)
        crash=Tick.model_validate({**tick(BASE+timedelta(seconds=3)).model_dump(),'bid':'80','ask':'81'})
        risk.observe(crash,crash.received_at)
        assert await gateway.on_tick(crash)==[]
        assert risk.halted=='portfolio_drawdown'
        assert second.id in risk.watchdog(crash.received_at)
    asyncio.run(scenario())


def test_watchdog_cancels_without_any_more_ticks(kit):
    async def scenario():
        clock=[0.];risk=RiskManager(RiskConfig(heartbeat_timeout=.06),kit,monotonic=lambda:clock[0])
        order=reserve(risk);gateway=PaperGateway(risk);await gateway.submit(order)
        class Quiet:
            async def on_tick(self,*args):return None
        engine=PaperEngine(risk,Quiet(),gateway,now=lambda:BASE,watchdog_interval=.01)
        async def stalled():
            clock[0]=1
            await asyncio.sleep(.05)
            if False:yield tick()
        await engine.run(stalled())
        assert kit.order(order.id)[1]=='cancelled'
        assert risk.halted=='stale_data_heartbeat'
    asyncio.run(scenario())


def test_unconfirmed_cancellation_retains_reservation(kit):
    async def scenario():
        risk=RiskManager(RiskConfig(),kit);order=reserve(risk)
        class NoAck(PaperGateway):
            async def cancel(self,order_id):return False
        engine=PaperEngine(risk,MomentumAlpha(),NoAck(risk),now=lambda:BASE)
        risk.trip('test_stop',BASE)
        await engine._cancel_due()
        assert kit.order(order.id)[1]=='cancel_pending'
        assert len(kit.active())==1
        assert kit.db.execute("SELECT COUNT(*) FROM audit WHERE event='cancel_unconfirmed'").fetchone()[0]==1
    asyncio.run(scenario())


def test_full_loop_submits_and_fills_with_persistent_audit(kit):
    async def scenario():
        now=[BASE];risk=RiskManager(RiskConfig(),kit);gateway=PaperGateway(risk)
        class Once:
            async def on_tick(self,t,at):return signal(t) if t.market_at==BASE else None
        async def source():
            yield tick()
            now[0]=BASE+timedelta(seconds=1)
            yield tick(now[0])
        await PaperEngine(risk,Once(),gateway,now=lambda:now[0]).run(source())
        assert kit.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==1
        assert not kit.active()
        assert kit.positions()['SPY']>0
        assert risk.halted=='feed_ended_or_engine_stopped'
    asyncio.run(scenario())


def test_live_gateway_is_rejected_and_schema_refuses_nonfinite(kit):
    with pytest.raises(ValueError,match='PAPER'):
        PaperEngine(RiskManager(RiskConfig(),kit),MomentumAlpha(),UnconfiguredBrokerGateway())
    for value in ('NaN','Infinity','-1'):
        with pytest.raises(ValidationError):Tick.model_validate({**tick().model_dump(),'bid':value})
    with pytest.raises(ValidationError):Tick.model_validate({**tick().model_dump(),'market_at':BASE.replace(tzinfo=None)})


def test_momentum_requires_causal_warmup(kit):
    async def scenario():
        alpha=MomentumAlpha(samples=3,threshold=D('.0001'))
        signals=[]
        for i,price in enumerate((100,100.2,100.6,100.7)):
            t=Tick.model_validate({**tick(BASE+timedelta(seconds=i)).model_dump(),'bid':str(price),'ask':str(price+.1)})
            signals.append(await alpha.on_tick(t,t.received_at))
        assert signals[:3]==[None]*3
        assert signals[-1].side=='buy' and signals[-1].volatility_samples==3
    asyncio.run(scenario())


def test_watchdog_still_runs_while_alpha_is_waiting(kit):
    async def scenario():
        clock=[0.];now=[BASE]
        risk=RiskManager(RiskConfig(heartbeat_timeout=.04,io_timeout=.2),kit,monotonic=lambda:clock[0])
        gateway=PaperGateway(risk)
        existing=reserve(risk);await gateway.submit(existing)
        class SlowAlpha:
            async def on_tick(self,t,at):
                clock[0]=1
                await asyncio.sleep(.07)
                return signal(t,id='after-stall')
        async def source():yield tick(symbol='QQQ')
        await PaperEngine(risk,SlowAlpha(),gateway,now=lambda:now[0],watchdog_interval=.005).run(source())
        assert kit.order(existing.id)[1]=='cancelled'
        assert kit.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0]==1
        assert risk.halted=='stale_data_heartbeat'
    asyncio.run(scenario())


def test_submit_timeout_does_not_release_reservation_when_cancel_unknown(kit):
    async def scenario():
        risk=RiskManager(RiskConfig(io_timeout=.02),kit)
        class Ambiguous(PaperGateway):
            async def submit(self,order):await asyncio.sleep(2)
            async def cancel(self,order_id):return False
        class Once:
            async def on_tick(self,t,at):return signal(t)
        async def source():yield tick()
        with pytest.raises(ExceptionGroup):
            await PaperEngine(risk,Once(),Ambiguous(risk),now=lambda:BASE).run(source())
        assert risk.halted=='submission_unconfirmed'
        assert len(kit.active())==1
        assert kit.active()[0][1]=='cancel_pending'
    asyncio.run(scenario())


def test_expired_order_is_cancelled_before_matching_tick(kit):
    async def scenario():
        now=[BASE];risk=RiskManager(RiskConfig(),kit);gateway=PaperGateway(risk)
        class Once:
            async def on_tick(self,t,at):return signal(t) if t.market_at==BASE else None
        async def source():
            yield tick()
            now[0]=BASE+timedelta(seconds=6)
            yield tick(now[0])
        await PaperEngine(risk,Once(),gateway,now=lambda:now[0]).run(source())
        assert kit.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0]==0
        assert kit.db.execute('SELECT status FROM orders').fetchone()[0]=='cancelled'
    asyncio.run(scenario())


def test_bad_source_aborts_and_cancels_existing_paper_order(kit):
    async def scenario():
        risk=RiskManager(RiskConfig(),kit);gateway=PaperGateway(risk)
        order=reserve(risk);await gateway.submit(order)
        async def source():
            raise RuntimeError('feed broke')
            yield tick()
        with pytest.raises(ExceptionGroup):
            await PaperEngine(risk,MomentumAlpha(),gateway,now=lambda:BASE).run(source())
        assert kit.order(order.id)[1]=='cancelled'
    asyncio.run(scenario())


def test_fill_receipt_after_latency_deadline_is_rejected(kit):
    risk=RiskManager(RiskConfig(),kit);order=reserve(risk)
    fill=Fill(id='late',order_id=order.id,account=order.account,symbol='SPY',side='buy',
        quantity='.01',price='100',fee='0',market_at=BASE+timedelta(seconds=1),
        received_at=BASE+timedelta(seconds=6),source='paper:recorded_exchange')
    with pytest.raises(ValueError,match='deadline'):kit.record_paper_fill(fill)
    assert kit.positions()=={} and D(kit.get('cash'))==1000


def test_recorded_replay_never_overwrites_existing_database(tmp_path):
    from tools.replay_quant import replay
    path=tmp_path/'ticks.jsonl'
    ticks=[]
    for i in range(25):
        price=D('100')+D(i)*D('.03')+D(i%3)*D('.01')
        ticks.append(Tick.model_validate({**tick(BASE+timedelta(seconds=i)).model_dump(),
            'bid':price,'ask':price+D('.02')}))
    path.write_text('\n'.join(t.model_dump_json() for t in ticks),encoding='utf-8')
    db=tmp_path/'replay.sqlite3'
    result=asyncio.run(replay(path,db,D('1000')))
    assert result['environment']=='isolated recorded-data paper replay'
    assert result['orders']>0 and result['halt']=='feed_ended_or_engine_stopped'
    before=db.read_bytes()
    with pytest.raises(FileExistsError):asyncio.run(replay(path,db,D('1000')))
    assert db.read_bytes()==before
