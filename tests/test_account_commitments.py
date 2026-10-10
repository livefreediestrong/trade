"""Read-only account inventory and unfinished-order cost evidence."""
import copy
from datetime import timedelta
from types import SimpleNamespace as NS

import pytest
import broker_ibkr as broker
from broker_safety import assess
from test_broker_safety import NOW, position, stop
from test_broker_lifecycle import gateway


def order(**changes):
    return dict(stop(id='one', currency='USD', symbol='TEST', side='buy', order_type='LMT',
                     limit_price=10, multiplier=1), **changes)


def report(orders=None, positions=None, **changes):
    return assess({"ok": True, "checked_at": NOW.isoformat(),
        "positions": positions or [], "orders": orders or [], **changes}, NOW)


def test_partial_fill_is_removed_from_limit_outlay_and_never_becomes_cash():
    r = report([order(qty=100, filled_qty=17)])['commitments']
    assert r['buy_limits'] == [{'currency': 'USD', 'amount': 830, 'order_count': 1}]
    assert r['orders'][0]['remaining_qty'] == 83
    assert r['orders'][0]['limit_amount'] == 830
    assert 'spendable_cash' not in r and 'available_funds' not in r


def test_option_multiplier_is_required_and_stock_option_values_are_not_net():
    r = report([order(id='stock', qty=4, limit_price=25),
                order(id='opt', asset_type='OPT', qty=2, filled_qty=1, limit_price=2.5, multiplier=10),
                order(id='missing', asset_type='OPT', multiplier=None)])['commitments']
    assert r['buy_limits'][0]['amount'] == 125
    assert r['unknown_buy_count'] == 1
    assert r['orders'][2]['limit_amount'] is None


@pytest.mark.parametrize('changes', [dict(order_type='MKT'), dict(order_type='STP'),
    dict(asset_type='BAG'), dict(asset_type='FUT'), dict(limit_price=None),
    dict(limit_price=float('nan')), dict(limit_price=1.7976931348623157e308),
    dict(limit_price=True), dict(limit_price=0), dict(currency=''),
    dict(multiplier=0), dict(multiplier=float('inf')), dict(multiplier=True),
    dict(status='Inactive'), dict(status='PendingSubmit'), dict(status='unknown')])
def test_unpriced_or_unsupported_buy_is_not_zero_or_priced(changes):
    r = report([order(**changes)])['commitments']
    assert r['buy_limits'] == []
    assert r['unknown_buy_count'] == 1
    assert r['orders'][0]['limit_amount'] is None


@pytest.mark.parametrize('changes', [dict(parent_id=42), dict(oca_group='bracket'),
    dict(conditions_count=1), dict(conditions_count=None), dict(transmit=False),
    dict(good_after_time='later'), dict(why_held='child'), dict(algo_strategy='Adaptive'),
    dict(active_start_time='later')])
def test_conditional_siblings_do_not_inflate_standalone_buy_subtotal(changes):
    r = report([order(**changes)])['commitments']
    assert r['buy_limits'] == [] and r['contingent_buy_count'] == 1
    assert r['orders'][0]['limit_amount'] is None


def test_pending_cancellation_still_claims_quantity_and_sells_never_fund_buys():
    r = report([order(id='canceling', status='PendingCancel'),
                order(id='sale', side='sell', qty=999)])['commitments']
    assert r['buy_limits'][0]['amount'] == 100
    assert r['sell_order_count'] == 1
    assert r['orders'][1]['limit_amount'] is None


def test_currencies_are_separate_and_terminal_rows_not_counted_as_working():
    r = report([order(id='usd'), order(id='eur', currency='EUR'),
                order(id='filled', status='Filled', filled_qty=10),
                order(id='cancelled', status='Cancelled', filled_qty=2)])['commitments']
    assert {x['currency']: x['amount'] for x in r['buy_limits']} == {'USD': 100}
    assert r['unknown_buy_count'] == 1 and r['orders'][1]['currency'] == 'EUR'
    assert len(r['orders']) == 2


@pytest.mark.parametrize('currency', ['GBP', 'ZAR', 'EUR', 'JPY'])
def test_unverified_price_subunits_or_conversion_do_not_become_dollar_cost(currency):
    r = report([order(currency=currency)])['commitments']
    assert not r['buy_limits'] and r['orders'][0]['limit_amount'] is None
    assert 'price units' in r['orders'][0]['note']


def test_all_broker_holdings_include_options_foreign_currency_and_manual_positions():
    rows = [position(), position(con_id=200, asset_type='OPT', qty=-1, local_symbol='TEST 261016C100'),
            position(con_id=300, currency='EUR'), position(con_id=400, asset_type='FUT', qty=2)]
    r = report(positions=rows)['commitments']
    assert len(r['holdings']) == 4
    assert r['short_option_count'] == 1
    assert len(r['groups']) == 4
    assert all('account' not in str(x).lower() for x in r['holdings'])


def test_conflicting_duplicate_orders_or_holdings_do_not_offer_a_subtotal():
    for orders, positions in [([order(), order(qty=9)], []),
                              ([], [position(), position(qty=11)])]:
        r = report(orders, positions)['commitments']
        assert r['ok'] is False and r['buy_limits'] == []


def test_identical_duplicates_are_not_counted_twice():
    r = report([order(), copy.deepcopy(order())], [position(), position()])['commitments']
    assert r['buy_limits'][0]['amount'] == 100
    assert len(r['holdings']) == 1 and len(r['orders']) == 1


@pytest.mark.parametrize('changes', [dict(qty=True), dict(filled_qty=True), dict(qty=-1),
    dict(filled_qty=11), dict(qty=float('inf'))])
def test_unreadable_order_quantities_withhold_entire_report(changes):
    assert report([order(**changes)])['ok'] is False


def test_failed_or_stale_snapshot_never_retains_previous_commitments():
    r = report([order()], checked_at=(NOW-timedelta(seconds=46)).isoformat())
    assert r['ok'] is False and not r.get('commitments')


def test_snapshot_retains_currency_exact_multiplier_and_limit_without_trading(gateway, monkeypatch):
    from ib_async import Option, LimitOrder
    contract = Option('TEST', '20261016', 100, 'C', 'SMART', multiplier='10', currency='EUR', conId=123)
    native = LimitOrder('BUY', 3, 2.5, account='DU123', permId=22)
    trade = NS(contract=contract, order=native, orderStatus=NS(status='Submitted', filled=1, whyHeld=''))
    monkeypatch.setattr(gateway, 'reqAllOpenOrders', lambda: [trade])
    monkeypatch.setattr(gateway, 'placeOrder', lambda *a: pytest.fail('Read submitted an order'))
    monkeypatch.setattr(gateway, 'cancelOrder', lambda *a: pytest.fail('Read canceled an order'))
    r = broker.get_safety_snapshot()
    assert r['ok']
    assert r['orders'][0]['currency'] == 'EUR'
    assert r['orders'][0]['multiplier'] == 10
    assert r['orders'][0]['limit_price'] == 2.5


@pytest.mark.parametrize('value', [True, float('nan'), 1.7976931348623157e308])
def test_unavailable_snapshot_price_is_not_reported_as_an_amount(gateway, monkeypatch, value):
    from ib_async import Stock, LimitOrder
    native = LimitOrder('BUY', 1, value, account='DU123')
    trade = NS(contract=Stock('TEST', 'SMART', 'USD', conId=100), order=native,
               orderStatus=NS(status='Submitted', filled=0, whyHeld=''))
    monkeypatch.setattr(gateway, 'reqAllOpenOrders', lambda: [trade])
    r = broker.get_safety_snapshot()
    assert r['ok'] and r['orders'][0]['limit_price'] is None


@pytest.mark.parametrize('field', ['qty', 'filled', 'position'])
def test_boolean_quantities_do_not_turn_into_one_in_adapter(gateway, monkeypatch, field):
    from ib_async import Stock, LimitOrder
    contract = Stock('TEST', 'SMART', 'USD', conId=100)
    native = LimitOrder('BUY', True if field == 'qty' else 1, 5, account='DU123')
    trade = NS(contract=contract, order=native,
               orderStatus=NS(status='Submitted', filled=True if field == 'filled' else 0, whyHeld=''))
    monkeypatch.setattr(gateway, 'reqAllOpenOrders', lambda: [trade])
    monkeypatch.setattr(gateway, 'positions', lambda account: [NS(account=account, contract=contract,
        position=True if field == 'position' else 1)])
    assert broker.get_safety_snapshot()['ok'] is False


def test_snapshot_discards_other_account_orders_and_rechecks_identity(gateway, monkeypatch):
    from ib_async import Stock, LimitOrder
    contract = Stock('TEST', 'SMART', 'USD', conId=100)
    native = LimitOrder('BUY', 1, 5, account='OTHER')
    trade = NS(contract=contract, order=native, orderStatus=NS(status='Submitted', filled=0, whyHeld=''))
    monkeypatch.setattr(gateway, 'reqAllOpenOrders', lambda: [trade])
    assert broker.get_safety_snapshot()['orders'] == []
    def changed():
        gateway.account = 'DU456'
        return []
    monkeypatch.setattr(gateway, 'reqAllOpenOrders', changed)
    assert broker.get_safety_snapshot()['ok'] is False
