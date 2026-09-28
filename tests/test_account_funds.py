"""Account/currency isolation and unknown-value preservation before broker dispatch."""
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker
from test_broker_lifecycle import gateway
from test_options_risk_fixes import _FakeIB, _sto, IDENTITY
from test_ib_async_protocol import wire


def row(tag, value, account='DU1', currency='USD'):
    return NS(account=account, currency=currency, tag=tag, value=value)


@pytest.mark.parametrize('rows', [
    [row('BuyingPower', '1000000', account='OTHER')],
    [row('BuyingPower', '1000000', currency='EUR')],
    [row('BuyingPower', 'NaN')],
    [row('BuyingPower', 'Infinity')],
    [row('BuyingPower', '1.7976931348623157e308')],
    [row('BuyingPower', 0), row('AvailableFunds', '1000000')],
    [row('BuyingPower', '0'), row('BuyingPower', '1000000')],
    [row('AvailableFunds', '1000000')],
])
def test_short_option_cannot_use_wrong_unknown_or_zero_buying_power(monkeypatch, rows):
    ib = _FakeIB()
    monkeypatch.setattr(ib, 'accountSummary', lambda account: rows)
    monkeypatch.setattr(broker, '_submission_risk_error', lambda *a: None)
    with pytest.raises(ValueError, match='buying power|buying_power'):
        broker._place_option_from_desk(ib, _sto(right='P', covered=False, allow_naked=True), IDENTITY, 'test')


def test_missing_buying_power_remains_unknown_in_account(gateway, monkeypatch):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: [r for r in original(account) if r.tag != 'BuyingPower'])
    result = broker.get_account()
    assert result['ok'] and result['risk_ready']
    assert result['account']['buying_power'] is None


def test_account_exposes_separate_settled_and_available_cash(gateway, monkeypatch):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: original(account) + [
        row('SettledCash', '0', account), row('AvailableFunds', '125', account)])
    result = broker.get_account()
    assert result['ok']
    assert result['account']['settled_cash'] == 0
    assert result['account']['available_funds'] == 125
    assert result['account']['cash'] == 90000


def test_conflicting_equity_is_not_last_row_wins(gateway, monkeypatch):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: original(account) + [row('NetLiquidation', '9999999', account)])
    assert broker.get_account()['ok'] is False


@pytest.mark.parametrize('value,status,expected', [
    ('0', 'reported', 0), ('-25.15', 'reported', -25.15), ('125.00', 'reported', 125),
    (True, 'unavailable', None), (None, 'unavailable', None), ('', 'unavailable', None),
    ('NaN', 'unavailable', None), ('Infinity', 'unavailable', None),
    ('1.7976931348623157e308', 'unavailable', None),
])
def test_optional_cash_is_not_equity_or_pnl_readiness(gateway, monkeypatch, value, status, expected):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: [
        r for r in original(account) if r.tag != 'TotalCashValue'] + [row('TotalCashValue', value, account)])
    result = broker.get_account()
    assert result['ok'] and result['risk_ready']
    assert result['account']['cash'] == expected
    assert result['account']['funds_observation']['field_status']['cash'] == status
    assert result['account']['funds_observation']['broker_value_time'] is None


def test_other_accounts_and_currencies_cannot_overwrite_usd_values(gateway, monkeypatch):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: original(account) + [
        row('BuyingPower', '999999', 'OTHER'), row('BuyingPower', '999999', account, 'EUR'),
        row('SettledCash', '999999', account, 'BASE'), row('SettledCash', '999999', 'OTHER')])
    account = broker.get_account()['account']
    assert account['buying_power'] == 180000
    assert account['settled_cash'] is None
    assert account['funds_observation']['field_status']['settled_cash'] == 'currency_unverified'


def test_conflicting_optional_balance_does_not_become_a_total(gateway, monkeypatch):
    original = gateway.accountSummary
    monkeypatch.setattr(gateway, 'accountSummary', lambda account: original(account) + [
        row('SettledCash', '10', account), row('SettledCash', '11', account)])
    account = broker.get_account()['account']
    assert account['settled_cash'] is None
    assert account['funds_observation']['field_status']['settled_cash'] == 'conflicting'


def test_repeated_identical_numeric_value_is_not_double_counted():
    assert broker._summary_amount([row('BuyingPower', '100'), row('BuyingPower', '100.00')], 'DU1', 'BuyingPower') == (100, 'reported')


@pytest.mark.parametrize('rows', [None, {}, 'unreadable'])
def test_malformed_summary_cannot_authorize_short_option(monkeypatch, rows):
    ib = _FakeIB()
    monkeypatch.setattr(ib, 'accountSummary', lambda account: rows)
    with pytest.raises(ValueError, match='buying power'):
        broker._place_option_from_desk(ib, _sto(right='P', covered=False, allow_naked=True), IDENTITY, 'test')


def test_valid_funding_keeps_remaining_risk_gate_and_records_exact_floor(monkeypatch):
    ib = _FakeIB(buying_power='10000')
    order = _sto(right='P', covered=False, allow_naked=True)
    # Funds alone cannot bypass the final authorization.
    with pytest.raises(ValueError, match='risk authorization'):
        broker._place_option_from_desk(ib, order, IDENTITY, 'test')
    assert order['funds_check']['buying_power'] == 10000
    assert order['funds_check']['risk_floor'] == 9850
    assert 'account_id' not in order['funds_check']
    sent = []
    monkeypatch.setattr(broker, '_submission_risk_error', lambda *a: None)
    monkeypatch.setattr(broker, '_send_native_order', lambda *args: sent.append(args) or 'intercepted')
    assert broker._place_option_from_desk(ib, order, IDENTITY, 'test') == 'intercepted'
    assert len(sent) == 1 and sent[0][2].action == 'SELL' and sent[0][2].totalQuantity == 1


def test_wire_summary_preserves_currency_and_zero(wire):
    import struct
    client, sent, _, _ = wire
    messages = []
    for tag, value, currency in [('NetLiquidation', '110', 'USD'), ('TotalCashValue', '110', 'USD'),
                                 ('BuyingPower', '0', 'USD'), ('BuyingPower', '99999', 'EUR'),
                                 ('SettledCash', '0', 'USD')]:
        fields = (63, 1, 9001, 'DU1', tag, value, currency)
        body = ('\0'.join(map(str, fields))+'\0').encode()
        messages.append(struct.pack('>I', len(body))+body)
    payload = b''.join(messages)
    client.client._onSocketHasData(payload[:11])
    client.client._onSocketHasData(payload[11:])
    result = broker._account_balances(list(client.wrapper.acctSummary.values()), 'DU1')
    assert result['buying_power'] == 0 and result['settled_cash'] == 0
    assert result['cash'] == 110 and result['available_funds'] is None
    assert sent == []


def test_broker_book_preserves_funds_metadata_without_sharing_mutable_state(gateway, monkeypatch):
    import app as desk
    from datetime import datetime, timezone
    monkeypatch.setattr(desk, '_broker_is_configured', lambda: True)
    monkeypatch.setattr(desk, '_BROKER_BOOK_CACHE', {'at': 0, 'val': None})
    monkeypatch.setattr(desk, '_note_day_pnl_peak', lambda value: None)
    monkeypatch.setattr(broker, 'public_status', lambda: {'broker': 'ibkr', 'connected': True})
    metadata = {'currency': 'USD', 'read_at': datetime.now(timezone.utc).isoformat(),
                'field_status': {'settled_cash': 'reported'}, 'broker_value_time': None}
    account = {'ok': True, 'risk_ready': False, 'account': {'equity': 110, 'currency': 'USD',
        'settled_cash': 0, 'available_funds': -1, 'funds_observation': metadata}}
    monkeypatch.setattr(broker, 'get_account', lambda: account)
    monkeypatch.setattr(broker, 'get_positions', lambda: {'ok': True, 'positions': []})
    result = desk._broker_book_cached()
    assert result['settled_cash'] == 0 and result['available_funds'] == -1
    assert result['buying_power'] is None and result['risk_ready'] is False
    metadata['field_status']['settled_cash'] = 'later change'
    assert result['funds_observation']['field_status']['settled_cash'] == 'reported'
