"""Market-time causality and recorded feed gaps in the actual paper kernel."""
import asyncio
from datetime import timedelta
from decimal import Decimal as D
from quant_models import RiskConfig, Signal
from quant_risk import RiskManager
from quant_engine import PaperEngine, PaperGateway
from tools.replay_quant import replay
from test_quant_kernel import kit, no_network, tick, BASE


def run(coro):
    # Own this loop without resetting eventkit's ambient main-thread loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_market_tick_before_order_creation_does_not_fill(kit):
    now = [BASE+timedelta(seconds=4)]
    risk = RiskManager(RiskConfig(), kit)
    class Once:
        async def on_tick(self, t, at):
            if t.market_at != BASE:
                return None
            return Signal(id='first', symbol='SPY', side='buy', created_at=at, market_at=t.market_at,
                expires_at=at+timedelta(seconds=5), strategy='causal', volatility='.01',
                volatility_samples=20, budget='10')
    async def source():
        yield tick(BASE).model_copy(update={'received_at': now[0]})
        now[0] = BASE+timedelta(seconds=5)
        yield tick(BASE+timedelta(seconds=1)).model_copy(update={'received_at': now[0]})
    run(PaperEngine(risk, Once(), PaperGateway(risk), now=lambda: now[0]).run(source()))
    assert kit.db.execute('SELECT COUNT(*) FROM orders').fetchone()[0] == 1
    assert kit.db.execute('SELECT COUNT(*) FROM fills').fetchone()[0] == 0


def test_replay_detects_gap_before_refreshing_heartbeat(tmp_path):
    ticks = [tick(BASE)]
    for i in range(25):
        price = D('100')+D(i)*D('.03')+D(i%3)*D('.01')
        ticks.append(tick(BASE+timedelta(seconds=30+i)).model_copy(update={'bid': price-D('.01'), 'ask': price}))
    path = tmp_path/'ticks.jsonl'
    path.write_text('\n'.join(t.model_copy(update={'data_kind': 'recorded', 'source': 'recorded_exchange'}).model_dump_json() for t in ticks))
    result = run(replay(path, tmp_path/'gap.sqlite3', D('1000')))
    assert result['orders'] == 0
    assert 'stale' in result['halt'] or 'heartbeat' in result['halt']
