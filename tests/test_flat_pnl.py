"""Flat-account daily P&L: a proven $0 only with no holdings and no trades today (owner decision 2026-09-28)."""
from types import SimpleNamespace as NS

import pytest

import broker_ibkr as broker
from test_pnl_stream_safety import stream, assert_blocked  # noqa: F401 - fixture

pytestmark = pytest.mark.flat_pnl


def position(qty, account="TEST"):
    return NS(account=account, position=qty, contract=NS(symbol="LCID", conId=1))


def execution(account="TEST"):
    return NS(execution=NS(acctNumber=account))


@pytest.fixture
def flat(stream, monkeypatch):  # noqa: F811 - pytest fixture chaining
    state = NS(positions=[], fills=[], executions=[], reads=0, error=None)
    def req_executions(_filter):
        state.reads += 1
        if state.error:
            raise state.error
        return list(state.executions)
    stream.fake.positions = lambda account: list(state.positions)
    stream.fake.fills = lambda: list(state.fills)
    stream.fake.reqExecutions = req_executions
    monkeypatch.setattr(broker, "_FLAT_EXECUTIONS", {})
    stream.ready("true")
    stream.account()  # subscribe; IBKR sends nothing
    stream.clock[0] += broker.FLAT_PNL_MIN_WAIT_SEC
    return NS(stream=stream, state=state)


def test_flat_account_with_silent_feed_derives_zero(flat):
    result = flat.stream.account()
    assert result["risk_ready"] is True and result["account"]["day_pnl"] == 0.0
    assert result["pnl_diagnostics"]["source"] == "derived_flat_account"
    assert result["pnl_diagnostics"]["flat_evidence"]["executions_today"] == 0


def test_real_callback_wins_and_derivation_needs_a_waiting_period(stream):  # noqa: F811
    stream.fake.positions = lambda account: []
    stream.fake.fills = lambda: []
    stream.fake.reqExecutions = lambda _filter: []
    stream.ready("true")
    assert_blocked(stream.account())  # just subscribed: give IBKR its chance first
    stream.callback(-3.0)
    result = stream.account()
    assert result["account"]["day_pnl"] == -3.0 and result["pnl_diagnostics"]["source"] == "ibkr_daily_pnl"


@pytest.mark.parametrize("change", ["holding", "executions_today", "session_fill", "read_error", "not_ready"])
def test_anything_unproven_keeps_new_risk_blocked(flat, change):
    state = flat.state
    if change == "holding":
        state.positions = [position(1)]
    elif change == "executions_today":
        state.executions = [execution()]
    elif change == "session_fill":
        state.fills = [execution()]
    elif change == "read_error":
        state.error = TimeoutError()
    else:
        flat.stream.ready("false")
    assert_blocked(flat.stream.account())


def test_other_accounts_do_not_count(flat):
    flat.state.positions = [position(5, account="OTHER")]
    flat.state.executions = [execution(account="OTHER")]
    assert flat.stream.account()["account"]["day_pnl"] == 0.0


def test_submission_recheck_uses_caches_only(flat):
    assert flat.stream.account()["risk_ready"] is True
    reads = flat.state.reads
    assert broker._flat_evidence_cached(flat.stream.fake, "TEST") is True
    flat.state.positions = [position(2)]
    assert broker._flat_evidence_cached(flat.stream.fake, "TEST") is False
    assert flat.state.reads == reads  # no broker request on the pre-send path


def test_execution_read_is_refreshed_and_expires(flat):
    flat.stream.account()
    flat.stream.clock[0] += broker.FLAT_EXECUTIONS_MAX_AGE_SEC + 1
    flat.state.executions = [execution()]
    assert_blocked(flat.stream.account())  # a re-read found today's trade
    assert broker._flat_evidence_cached(flat.stream.fake, "TEST") is False
