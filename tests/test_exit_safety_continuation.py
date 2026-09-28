"""Exercise exit/cover conflicts with isolated files and intercepted IB transport."""
import copy
import socket
import threading
from types import SimpleNamespace as NS

import pytest
from ib_async import Stock, Option, LimitOrder, StopOrder

import app as desk
import broker_ibkr as ibkr
import broker_router as router
import live_agent as agent
from broker_fixtures import deadline
from test_broker_lifecycle import gateway  # noqa: F401
from test_execution_repairs import execution  # noqa: F401
from test_live_agent import live  # noqa: F401
from test_trade_intelligence import entered, _hold  # noqa: F401
from test_broker_safety import position, stop, result
from test_options_risk_fixes import _FakeIB, _sto, _pos, IDENTITY


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Exercise adapter bodies on the fixture thread. Queue/thread ownership is
    # covered separately; Windows asyncio creates a socketpair on worker start.
    monkeypatch.setattr(ibkr, '_WORKER', threading.current_thread())
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **kw: pytest.fail('Network forbidden'))


@pytest.mark.parametrize('snapshot', [
    {'ok': True}, {'ok': True, 'positions': None}, {'ok': True, 'positions': {}},
    {'ok': True, 'positions': [None]},
    *[{'ok': True, 'positions': [{'symbol': 'TEST', 'qty': q, 'side': 'long'}]}
      for q in (None, 'bad', float('nan'), float('inf'), True)],
    {'ok': True, 'positions': [{'symbol': '', 'qty': 2, 'side': 'long'}]},
    {'ok': True, 'positions': [{'symbol': 'TEST', 'qty': 2, 'side': 'long'},
                              {'symbol': 'TEST', 'qty': 0, 'side': 'long'}]},
])
def test_bad_holdings_do_not_erase_fox_position(entered, monkeypatch, snapshot):
    before = copy.deepcopy(entered.service.load()['managed'])
    monkeypatch.setitem(router.__dict__, 'get_positions', lambda: snapshot)
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert entered.service.load()['managed'] == before
    assert len(entered.sent) == 1
    assert entered.service.exit_checks['TEST']['state'] == 'holdings_unverified'


def test_account_change_during_holdings_read_cannot_erase_fox_position(entered, monkeypatch):
    before = copy.deepcopy(entered.service.load()['managed'])
    def changed():
        monkeypatch.setitem(router.__dict__, 'verify_execution_context', lambda: {
            'ok': True, 'identity': dict(entered.identity, account_id='OTHER')})
        return {'ok': True, 'positions': []}
    monkeypatch.setitem(router.__dict__, 'get_positions', changed)
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert entered.service.load()['managed'] == before
    assert len(entered.sent) == 1


def test_config_change_during_holdings_read_cannot_erase_fox_position(entered, monkeypatch):
    before = copy.deepcopy(entered.service.load()['managed'])
    cfg = desk.load_config()
    def changed():
        current = desk.load_config()
        current['live_agent']['enabled'] = False
        desk.save_config(current)
        return {'ok': True, 'positions': []}
    monkeypatch.setitem(router.__dict__, 'get_positions', changed)
    assert entered.service._manage_exits(cfg, agent.validate(entered.policy)) is False
    assert entered.service.load()['managed'] == before


@pytest.mark.parametrize('reader', ['get_positions', 'get_option_positions'])
def test_position_reader_rejects_disconnect_during_snapshot(gateway, monkeypatch, reader):
    def positions(account):
        gateway.disconnected = True
        return []
    monkeypatch.setattr(gateway, 'positions', positions)
    assert getattr(ibkr, reader)()['ok'] is False


def native_position(asset, qty, symbol='TEST', account='DU123'):
    c = (Stock(symbol, 'SMART', 'USD', conId=42, primaryExchange='NASDAQ') if asset == 'STK'
         else Option(symbol, '20261016', 100, 'C', 'SMART', multiplier='100', currency='USD', conId=77))
    return NS(account=account, position=qty, avgCost=100, contract=c)


def native_working(asset, qty, filled=0, action='SELL', account='DU123', kind='LMT'):
    c = native_position(asset, 0, account=account).contract
    o = (StopOrder(action, qty, 90, account=account) if kind == 'STP'
         else LimitOrder(action, qty, 110, account=account))
    return NS(contract=c, order=o, orderStatus=NS(status='Submitted', filled=filled, avgFillPrice=0))


def stock_sale(gateway, monkeypatch, positions, trades=(), shares=1):
    monkeypatch.setattr(gateway, 'positions', lambda account: positions)
    gateway.trades = list(trades)
    monkeypatch.setattr(gateway, 'openTrades', lambda: gateway.trades, raising=False)
    identity = ibkr.get_account()['identity']
    return ibkr.place_from_desk_order({
        'ticker': 'TEST', 'side': 'sell', 'shares': shares, 'signal_id': 'exit-test',
        'broker_identity': identity, 'valid_until': deadline(),
        'risk_authorization': {'equity': 100000., 'day_pnl': -250., 'reducing': True},
    })


@pytest.mark.parametrize('working', [False, True])
def test_stock_sale_cannot_remove_short_call_cover(gateway, monkeypatch, working):
    positions = [native_position('STK', 100)]
    trades = [native_working('OPT', 1)] if working else []
    if not working:
        positions.append(native_position('OPT', -1))
    r = stock_sale(gateway, monkeypatch, positions, trades)
    assert not r['ok'] and 'cover' in r['error'].lower()
    assert len(gateway.trades) == len(trades)


def test_stock_sale_can_use_only_surplus_above_call_cover(gateway, monkeypatch):
    r = stock_sale(gateway, monkeypatch, [native_position('STK', 150), native_position('OPT', -1)],
                   shares=50)
    assert r['ok'], r
    assert len(gateway.trades) == 1


@pytest.mark.parametrize('kind', ['LMT', 'STP'])
def test_working_stock_sales_already_claim_surplus(gateway, monkeypatch, kind):
    r = stock_sale(gateway, monkeypatch, [native_position('STK', 150), native_position('OPT', -1)],
                   [native_working('STK', 60, filled=10, kind=kind)], shares=1)
    assert not r['ok'] and 'cover' in r['error'].lower()
    assert len(gateway.trades) == 1


def test_other_account_call_does_not_reserve_this_accounts_shares(gateway, monkeypatch):
    r = stock_sale(gateway, monkeypatch, [native_position('STK', 100),
                   native_position('OPT', -1, account='DU999')], shares=100)
    assert r['ok'], r


@pytest.mark.parametrize('kind', ['LMT', 'STP'])
def test_new_covered_call_cannot_reuse_shares_with_working_sell(kind):
    ib = _FakeIB([native_position('STK', 100, symbol='SPY', account='DU1')])
    trade = native_working('STK', 1, account='DU1', kind=kind)
    trade.contract.symbol = 'SPY'
    ib._trades = [trade]
    with pytest.raises(ValueError, match='(?i)cover|unpledged'):
        ibkr._place_option_from_desk(ib, _sto(), IDENTITY, 'ref')


@pytest.mark.parametrize('changes', [{'conditions_count': 1}, {'good_after_time': '20261016 10:00:00 US/Eastern'}])
def test_conditional_or_future_stop_is_not_reported_as_active(changes):
    assert result(orders=[stop(**changes)])['rows'][0]['state'] == 'review'


def test_limit_sell_competing_with_stop_is_flagged():
    r = result(orders=[stop(), stop(order_type='LMT', permanent_id=124)])
    assert r['rows'][0]['state'] == 'review'
    assert any('sell order' in x.lower() for x in r['rows'][0]['notes'])


def test_stop_cannot_be_reported_as_safe_for_stock_covering_short_call():
    r = result(positions=[position(), position(asset_type='OPT', con_id=200, right='C', qty=-1,
                                              expiry='20261016', multiplier=100)])
    assert r['rows'][0]['state'] == 'review'
    assert any('short call' in x.lower() for x in r['rows'][0]['notes'])


@pytest.mark.parametrize('working', [False, True])
def test_single_leg_close_cannot_strip_possible_spread_protection(working):
    ib = _FakeIB([_pos('OPT', 'SPY', 1, 'C', 1000)])
    if working:
        trade = native_working('OPT', 1, account='DU1')
        trade.contract.symbol, trade.contract.conId = 'SPY', 1050
        ib._trades = [trade]
    else:
        ib._positions.append(_pos('OPT', 'SPY', -1, 'C', 1050))
    with pytest.raises(ValueError, match='long leg for protection'):
        ibkr._place_option_from_desk(ib, _sto(option_intent='STC'), IDENTITY, 'close-leg')


def test_single_long_option_without_short_obligations_still_reaches_risk_check():
    ib = _FakeIB([_pos('OPT', 'SPY', 1, 'C', 1000)])
    with pytest.raises(ValueError, match='risk authorization'):
        ibkr._place_option_from_desk(ib, _sto(option_intent='STC'), IDENTITY, 'close-leg')


@pytest.mark.parametrize('multiplier', ['', '10', '150', None, float('nan')])
def test_unknown_or_adjusted_short_call_requires_review(gateway, monkeypatch, multiplier):
    short = native_position('OPT', -1)
    short.contract.multiplier = multiplier
    r = stock_sale(gateway, monkeypatch, [native_position('STK', 200), short], shares=1)
    assert not r['ok'] and 'cover' in r['error'].lower()
    assert not gateway.trades


def test_pending_call_buyback_does_not_release_cover_early(gateway, monkeypatch):
    r = stock_sale(gateway, monkeypatch, [native_position('STK', 100), native_position('OPT', -1)],
                   [native_working('OPT', 1, action='BUY')], shares=1)
    assert not r['ok'] and 'cover' in r['error'].lower()
    assert len(gateway.trades) == 1


@pytest.mark.parametrize('field', ['conditions_count', 'good_after_time', 'active_start_time', 'algo_strategy'])
def test_missing_stop_activation_details_remain_unverified(field):
    order = stop()
    order.pop(field)
    assert result(orders=[order])['rows'][0]['state'] == 'review'


def test_new_fill_during_holdings_read_is_not_erased(entered, monkeypatch):
    def later_fill():
        entered.service._update_managed('TEST', {'shares': 3})
        return {'ok': True, 'positions': []}
    monkeypatch.setitem(router.__dict__, 'get_positions', later_fill)
    assert entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy)) is False
    assert entered.service.load()['managed']['TEST']['shares'] == 3
    assert len(entered.sent) == 1


def test_correction_during_quote_does_not_authorize_old_exit(entered, monkeypatch):
    _hold(monkeypatch, 2, 99.)
    quote = router.stock_quote
    def corrected(ticker):
        entered.service._update_managed('TEST', {'tracking_error': 'New execution correction'})
        return quote(ticker)
    monkeypatch.setitem(router.__dict__, 'stock_quote', corrected)
    entered.service._manage_exits(desk.load_config(), agent.validate(entered.policy))
    assert len(entered.sent) == 1
    assert entered.service.load()['managed']['TEST']['tracking_error'] == 'New execution correction'
