"""Synthetic reference cases computed independently with Wolfram, 2026-09-24."""
from decimal import Decimal
from datetime import datetime, timezone

import pytest

from trade_planner import estimate
from research_metrics import option_value
from quant_models import Portfolio, RiskConfig
from quant_risk import RiskManager, RiskStore


def test_fee_break_even_references():
    base=dict(budget=1000,price=100,exit_price=101,slippage_bps=0,
              fractional=False,order_type='limit',fee_mode='custom',entry_fee=1,exit_fee=1)
    long=estimate(dict(base,direction='long'))
    assert long['shares']==9
    assert long['break_even_price']==pytest.approx(100.222222,abs=1e-6)
    assert long['net_pnl_estimate']==7
    short=estimate(dict(base,direction='short',exit_price=99,borrow_fee=3))
    assert short['shares']==10
    assert short['break_even_price']==99.5
    assert short['net_pnl_estimate']==5
    unknown=estimate(dict(base,fee_mode='unknown'))
    assert unknown['break_even_price'] is None and unknown['net_pnl_estimate'] is None


@pytest.mark.parametrize('right,reference',[('call',10.450583572186),('put',5.5735260222570)])
def test_european_option_reference(right,reference):
    assert option_value(100,100,365,.2,.05,0,right)==pytest.approx(reference,abs=1e-10)


def test_dynamic_drawdown_reference(tmp_path):
    # Exercise the actual kernel in an isolated paper database; no gateway attached.
    store=RiskStore(tmp_path/'reference.sqlite3','paper-reference')
    now=datetime(2026,9,23,15,tzinfo=timezone.utc)
    try:
        risk=RiskManager(RiskConfig(max_drawdown='.05',min_drawdown='.01',target_volatility='.02'),store)
        def book(equity):
            return Portfolio(account='paper-reference',as_of=now,equity=equity,cash=equity,gross_exposure='0',volatility='.04')
        assert risk.update_portfolio(book('100000'),now)
        assert not risk.update_portfolio(book('97000'),now)
        assert risk.drawdown==Decimal('.03')
        assert risk.drawdown_limit==Decimal('.025')
        assert risk.halted=='portfolio_drawdown'
    finally:
        store.close()
