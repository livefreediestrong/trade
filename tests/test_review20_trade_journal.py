"""Retained journal isolation and missingness, using in-memory synthetic records."""
import copy
from datetime import datetime, timezone

import real_trade_journal as journal


class Desk:
    _CORRUPT_PATHS = set()

    def __init__(self, root):
        self.DATA_DIR = root
        self.saved = {"executions": []}

    def _load_json(self, path, default):
        return copy.deepcopy(self.saved)

    def _save_json(self, path, data):
        self.saved = copy.deepcopy(data)

    def load_signals(self):
        return []


def row(account, eid, pnl, paper=False, **updates):
    result = dict(account_id=account, execution_id=eid, paper_mode=paper, verified=True,
        ts=datetime.now(timezone.utc).isoformat(), ticker="TEST", side="buy", shares=1,
        price=100., currency="USD", commission_currency="USD", commission=.35,
        broker_realized_pnl=pnl, permanent_order_id=123)
    return dict(result, **updates)


def sync(j, account, rows, paper=False):
    j.merge({"ok": True, "identity": {"account_id": account, "paper_mode": paper}, "executions": rows})


def test_account_switch_keeps_current_totals_separate_from_history(tmp_path):
    j = journal.TradeJournal(Desk(tmp_path))
    sync(j, "U1111", [row("U1111", "a.01", 100)])
    sync(j, "U2222", [row("U2222", "b.01", 20)])
    current = j.status()
    assert current["account_suffix"] == "2222"
    assert current["real"]["by_currency"]["USD"]["broker_realized_pnl"] == 20
    assert {r["account_id"] for r in current["executions"]} == {"U2222"}
    assert len(current["accounts"]) == 2 and len(j.load()["executions"]) == 2
    sync(j, "DU3333", [row("DU3333", "c.01", 7, paper=True)], paper=True)
    current = j.status()
    assert current["real"]["executions"] == 0 and current["executions"] == []
    assert current["broker_paper"]["by_currency"]["USD"]["broker_realized_pnl"] == 7


def test_explicit_unreported_pnl_replaces_legacy_zero(tmp_path):
    j = journal.TradeJournal(Desk(tmp_path))
    sync(j, "U1111", [row("U1111", "a.01", 0)])
    sync(j, "U1111", [row("U1111", "a.01", None, broker_realized_pnl_reported=False)])
    summary = j.status()["real"]["by_currency"]["USD"]
    assert summary["pnl_missing"] == 1
    sync(j, "U1111", [row("U1111", "a.01", 0, broker_realized_pnl_reported=True)])
    assert j.status()["real"]["by_currency"]["USD"]["pnl_missing"] == 0
    # A later snapshot with no matched commission preserves a genuinely known zero.
    sync(j, "U1111", [row("U1111", "a.01", None, broker_realized_pnl_reported=None)])
    assert j.status()["real"]["by_currency"]["USD"]["pnl_missing"] == 0


def test_zero_execution_bust_retained_without_phantom_pnl(tmp_path):
    j = journal.TradeJournal(Desk(tmp_path))
    sync(j, "U1111", [row("U1111", "a.01", 20)])
    sync(j, "U1111", [row("U1111", "a.02", 0, shares=0, price=0, voided=True)])
    assert j.status()["real"]["executions"] == 0
    rows = {r["execution_id"]: r for r in j.load()["executions"]}
    assert rows["a.01"]["superseded_by"] == "a.02" and rows["a.02"]["voided"]
