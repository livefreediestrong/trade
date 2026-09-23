"""Tests for the staged SQLite persistence foundation (storage/*).

These never touch the real repo `data/` directory — everything runs against
a temp dir passed explicitly or via TOMAHAWK_DATA_DIR, matching the
convention used by tests/test_safety.py.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import storage  # noqa: E402
from storage import db as storage_db  # noqa: E402
from storage.repositories import (  # noqa: E402
    ConfigRepository,
    DecisionsRepository,
    FillsRepository,
    JournalRepository,
    LedgerMetaRepository,
    MarketCacheRepository,
    PositionsRepository,
    SignalsRepository,
)


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "tomahawk.sqlite3"


@pytest.fixture
def conn(db_path):
    connection = storage.get_connection(db_path)
    yield connection
    connection.close()


# --------------------------------------------------------------------- schema

def test_schema_creates_all_domain_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    }
    expected = {
        "schema_migrations",
        "config_kv",
        "signals",
        "decisions",
        "decisions_meta",
        "orders",
        "fills",
        "positions",
        "journal",
        "market_cache",
        "ledger_meta",
    }
    assert expected.issubset(tables)


def test_wal_and_foreign_keys_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrations_are_idempotent(db_path):
    conn1 = storage.get_connection(db_path)
    v1 = storage.schema_version(conn1)
    conn1.close()

    conn2 = storage.get_connection(db_path)
    v2 = storage.schema_version(conn2)
    applied = conn2.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    conn2.close()

    assert v1 == v2 == storage.CURRENT_SCHEMA_VERSION
    assert applied == storage.CURRENT_SCHEMA_VERSION


def test_fk_violation_is_rejected(conn):
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO fills (id, order_id, seq, ticker, side, shares, price, ts, source, payload, created_at) "
            "VALUES ('f1', 'no-such-order', 0, 'AAPL', 'buy', 1, 1, 'ts', 'x', '{}', 'ts')"
        )


# --------------------------------------------------------------- repositories

def test_config_repository_round_trip(conn):
    cfg = {"mode": "manual", "watchlist": ["AAPL", "MSFT"], "kill_switch": {"armed": False}}
    ConfigRepository(conn).replace_all(cfg)
    assert ConfigRepository(conn).as_dict() == cfg


def test_signals_repository_preserves_order(conn):
    signals = [{"id": "s2", "ticker": "MSFT", "status": "pending"}, {"id": "s1", "ticker": "AAPL", "status": "rejected"}]
    SignalsRepository(conn).replace_all(signals)
    assert SignalsRepository(conn).as_list() == signals


def test_decisions_repository_ring_round_trip(conn):
    events = [{"id": "e2", "seq": 2, "ts": "t2"}, {"id": "e1", "seq": 1, "ts": "t1"}]
    DecisionsRepository(conn).replace_all(2, events)
    seq, out = DecisionsRepository(conn).as_ring()
    assert seq == 2
    assert out == events


def test_positions_keyed_by_ticker_and_side(conn):
    positions = [
        {"ticker": "AAPL", "side": "long", "shares": 10, "avg_price": 100.0},
        {"ticker": "AAPL", "side": "short", "shares": 5, "avg_price": 200.0},
    ]
    PositionsRepository(conn).replace_all(positions)
    out = PositionsRepository(conn).as_list()
    assert len(out) == 2
    assert {(p["ticker"], p["side"]) for p in out} == {("AAPL", "long"), ("AAPL", "short")}


def test_journal_repository_round_trip(conn):
    journal = [{"id": "j1", "action": "start"}, {"id": "j2", "action": "stop"}]
    JournalRepository(conn).replace_all(journal)
    assert JournalRepository(conn).as_list() == journal


def test_market_cache_repository_get_set(conn):
    repo = MarketCacheRepository(conn)
    repo.set("buzz", {"rows": [{"ticker": "AAPL", "mentions": 3}]})
    assert repo.get("buzz") == {"rows": [{"ticker": "AAPL", "mentions": 3}]}
    assert repo.get("missing") is None


def test_ledger_meta_repository_round_trip(conn):
    ledger = {
        "equity": 100_000.0,
        "cash": 90_000.0,
        "daily": {"2026-09-22": {"trades": 2, "pnl": 10.0}},
        "equity_curve": [100000.0, 100010.0],
        "equity_curve_day": "2026-09-22",
        "broker_daily": {"2026-09-22": {"trades": 1}},
    }
    LedgerMetaRepository(conn).replace(ledger)
    out = LedgerMetaRepository(conn).as_dict()
    assert out["equity"] == 100_000.0
    assert out["daily"] == ledger["daily"]
    assert out["broker_daily"] == ledger["broker_daily"]  # extra key preserved


def test_fills_repository_allows_null_order(conn):
    fills = [{"id": "f1", "ticker": "AAPL", "shares": 10, "price": 100.0, "order_id": None}]
    FillsRepository(conn).replace_all(fills)
    assert FillsRepository(conn).as_list() == fills


# ---------------------------------------------------------------- json bridge

@pytest.fixture
def data_dir(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    return d


def _write(data_dir: Path, name: str, payload) -> None:
    (data_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_import_from_json_is_read_only_and_populates_db(data_dir, conn):
    _write(data_dir, "config.json", {"mode": "manual", "watchlist": ["AAPL"]})
    _write(data_dir, "signals.json", [{"id": "s1", "ticker": "AAPL", "status": "pending"}])
    _write(
        data_dir,
        "ledger.json",
        {
            "equity": 100000.0,
            "cash": 100000.0,
            "positions": [{"ticker": "AAPL", "side": "long", "shares": 5, "avg_price": 150.0}],
            "fills": [{"id": "f1", "ticker": "AAPL", "shares": 5, "price": 150.0}],
            "daily": {},
            "equity_curve": [],
            "equity_curve_day": None,
        },
    )
    _write(data_dir, "journal.json", [{"id": "j1", "action": "start"}])
    _write(data_dir, "decisions.json", {"seq": 1, "events": [{"id": "e1", "seq": 1}]})
    _write(data_dir, "buzz_cache.json", {"rows": [], "ts": 1})

    before = {name: (data_dir / name).read_bytes() for name in os.listdir(data_dir)}

    counts = storage.import_all_from_json(conn, data_dir)

    # JSON files are byte-for-byte untouched by import.
    for name, content in before.items():
        assert (data_dir / name).read_bytes() == content

    assert counts["config"] == 2
    assert counts["signals"] == 1
    assert counts["positions"] == 1
    assert counts["fills"] == 1
    assert counts["journal"] == 1
    assert counts["decisions"] == 1
    assert counts["market_cache"] == 1

    assert ConfigRepository(conn).as_dict()["watchlist"] == ["AAPL"]
    assert SignalsRepository(conn).as_list()[0]["ticker"] == "AAPL"


def test_import_missing_files_is_harmless(data_dir, conn):
    counts = storage.import_all_from_json(conn, data_dir)
    assert counts["config"] == 0
    assert counts["signals"] == 0
    assert counts["journal"] == 0
    assert counts["decisions"] == 0


def test_export_round_trips_through_json(data_dir, conn):
    cfg = {"mode": "manual", "watchlist": ["AAPL", "TSLA"]}
    ConfigRepository(conn).replace_all(cfg)
    signals = [{"id": "s1", "ticker": "AAPL", "status": "pending"}]
    SignalsRepository(conn).replace_all(signals)
    ledger = {"equity": 100000.0, "cash": 99000.0, "daily": {}, "equity_curve": [], "equity_curve_day": None}
    LedgerMetaRepository(conn).replace(ledger)
    PositionsRepository(conn).replace_all([{"ticker": "AAPL", "side": "long", "shares": 5, "avg_price": 100.0}])
    FillsRepository(conn).replace_all([{"id": "f1", "ticker": "AAPL", "shares": 5, "price": 100.0}])
    JournalRepository(conn).replace_all([{"id": "j1", "action": "start"}])
    DecisionsRepository(conn).replace_all(1, [{"id": "e1", "seq": 1}])

    written = storage.export_all_to_json(conn, data_dir)
    assert set(written) >= {"config.json", "signals.json", "ledger.json", "journal.json", "decisions.json"}

    assert json.loads((data_dir / "config.json").read_text()) == cfg
    assert json.loads((data_dir / "signals.json").read_text()) == signals
    ledger_out = json.loads((data_dir / "ledger.json").read_text())
    assert ledger_out["equity"] == 100000.0
    assert ledger_out["positions"][0]["ticker"] == "AAPL"
    assert ledger_out["fills"][0]["id"] == "f1"
    decisions_out = json.loads((data_dir / "decisions.json").read_text())
    assert decisions_out == {"seq": 1, "events": [{"id": "e1", "seq": 1}]}


def test_import_then_export_is_a_fixed_point(data_dir, conn):
    """Importing a JSON snapshot then exporting it back reproduces the same data."""
    original = {
        "config": {"mode": "manual", "watchlist": ["AAPL"]},
        "signals": [{"id": "s1", "ticker": "AAPL", "status": "pending"}],
        "ledger": {
            "equity": 50000.0,
            "cash": 40000.0,
            "positions": [{"ticker": "AAPL", "side": "long", "shares": 3, "avg_price": 90.0}],
            "fills": [{"id": "f1", "ticker": "AAPL", "shares": 3, "price": 90.0}],
            "daily": {"2026-09-22": {"trades": 1}},
            "equity_curve": [50000.0],
            "equity_curve_day": "2026-09-22",
        },
        "journal": [{"id": "j1", "action": "start"}],
        "decisions": {"seq": 3, "events": [{"id": "e1", "seq": 3}]},
    }
    for name, payload in original.items():
        _write(data_dir, f"{name}.json", payload)

    storage.import_all_from_json(conn, data_dir)
    parity = storage.verify_parity(conn, data_dir)
    assert all(info["match"] for info in parity.values()), parity


def test_verify_parity_detects_drift(data_dir, conn):
    _write(data_dir, "config.json", {"mode": "manual"})
    storage.import_all_from_json(conn, data_dir)
    # Legacy JSON changes after the import ran (simulating a save that
    # hasn't been mirrored yet).
    _write(data_dir, "config.json", {"mode": "auto_paper"})
    parity = storage.verify_parity(conn, data_dir)
    assert parity["config"]["match"] is False


# --------------------------------------------------------------- backup/restore

def test_backup_and_restore_round_trip(tmp_path):
    db_path = tmp_path / "tomahawk.sqlite3"
    conn = storage.get_connection(db_path)
    ConfigRepository(conn).replace_all({"mode": "manual"})
    conn.close()

    backup_dir = tmp_path / "backups"
    backup_file = storage.backup_database(db_path, backup_dir, label="test")
    assert backup_file.exists()

    # Corrupt/replace the live DB, then restore from backup.
    db_path.write_bytes(b"not a sqlite file")
    storage.restore_database(backup_file, db_path)

    restored = storage.get_connection(db_path)
    assert ConfigRepository(restored).as_dict() == {"mode": "manual"}
    restored.close()


def test_restore_rejects_missing_backup(tmp_path):
    with pytest.raises(FileNotFoundError):
        storage.restore_database(tmp_path / "nope.sqlite3", tmp_path / "db.sqlite3")


def test_backup_of_nonexistent_db_creates_empty_schema(tmp_path):
    db_path = tmp_path / "does_not_exist.sqlite3"
    backup_dir = tmp_path / "backups"
    dest = storage.backup_database(db_path, backup_dir)
    assert dest.exists()
    conn = storage_db.get_connection(dest)
    assert storage_db.schema_version(conn) == storage.CURRENT_SCHEMA_VERSION
    conn.close()


# ----------------------------------------------------------- opt-in flag/mirror

def test_backend_mode_defaults_to_json(monkeypatch):
    monkeypatch.delenv("TOMAHAWK_STORAGE_BACKEND", raising=False)
    assert storage.get_backend_mode() == storage.StorageBackend.JSON


def test_backend_mode_reads_env(monkeypatch):
    monkeypatch.setenv("TOMAHAWK_STORAGE_BACKEND", "dual")
    assert storage.get_backend_mode() == storage.StorageBackend.DUAL
    monkeypatch.setenv("TOMAHAWK_STORAGE_BACKEND", "bogus")
    assert storage.get_backend_mode() == storage.StorageBackend.JSON


def test_mirror_is_noop_when_backend_is_json(monkeypatch, tmp_path):
    monkeypatch.delenv("TOMAHAWK_STORAGE_BACKEND", raising=False)
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    from storage import backend as storage_backend

    storage_backend.reset_connection_cache()
    storage_backend.mirror_config({"mode": "manual"})
    # No database file should have been created — JSON mode must be a
    # complete no-op, not "write to sqlite but don't read from it".
    assert not (tmp_path / "tomahawk.sqlite3").exists()


def test_mirror_writes_when_dual_mode_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("TOMAHAWK_STORAGE_BACKEND", "dual")
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    from storage import backend as storage_backend

    storage_backend.reset_connection_cache()
    storage_backend.mirror_config({"mode": "auto_paper"})
    storage_backend.mirror_signals([{"id": "s1", "ticker": "AAPL"}])
    storage_backend.mirror_journal([{"id": "j1", "action": "start"}])
    storage_backend.mirror_ledger(
        {
            "equity": 1000.0,
            "cash": 900.0,
            "positions": [{"ticker": "AAPL", "side": "long", "shares": 1, "avg_price": 100.0}],
            "fills": [{"id": "f1", "ticker": "AAPL", "shares": 1, "price": 100.0}],
            "daily": {},
            "equity_curve": [],
            "equity_curve_day": None,
        }
    )
    storage_backend.mirror_decision_event(1, [{"id": "e1", "seq": 1}])

    db_path = tmp_path / "tomahawk.sqlite3"
    assert db_path.exists()
    conn = storage.get_connection(db_path)
    assert ConfigRepository(conn).as_dict()["mode"] == "auto_paper"
    assert SignalsRepository(conn).as_list()[0]["ticker"] == "AAPL"
    assert JournalRepository(conn).as_list()[0]["action"] == "start"
    assert PositionsRepository(conn).as_list()[0]["ticker"] == "AAPL"
    seq, events = DecisionsRepository(conn).as_ring()
    assert seq == 1 and events[0]["id"] == "e1"
    conn.close()
    storage_backend.reset_connection_cache()


def test_mirror_failure_never_raises(monkeypatch, tmp_path):
    """A broken mirror must never surface to the JSON-writing caller."""
    monkeypatch.setenv("TOMAHAWK_STORAGE_BACKEND", "dual")
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path))
    from storage import backend as storage_backend

    storage_backend.reset_connection_cache()

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(storage_backend, "_connection", boom)
    storage_backend.mirror_config({"mode": "manual"})  # must not raise
    storage_backend.reset_connection_cache()


# -------------------------------------------------------- app.py integration

def test_app_save_config_mirrors_when_dual_mode(monkeypatch, tmp_path):
    """End-to-end: app.py's save_config mirrors into SQLite when opted in."""
    monkeypatch.setenv("TOMAHAWK_DATA_DIR", str(tmp_path / "app_data"))
    monkeypatch.setenv("TOMAHAWK_NO_BG", "1")
    monkeypatch.setenv("TOMAHAWK_STORAGE_BACKEND", "dual")

    # Import app fresh isn't practical inside one test session (module is a
    # singleton), so reuse the already-imported module from conftest-style
    # sys.modules and just repoint the storage backend's data dir + cache.
    import app as desk
    from storage import backend as storage_backend

    storage_backend.reset_connection_cache()
    cfg_path = tmp_path / "app_data2" / "config.json"
    monkeypatch.setattr(desk, "CONFIG_PATH", cfg_path)
    desk._CORRUPT_PATHS.clear()

    cfg = dict(desk.DEFAULT_CONFIG, mode="auto_paper")
    desk.save_config(cfg)

    conn = storage.get_connection(storage_backend.get_db_path())
    assert ConfigRepository(conn).as_dict()["mode"] == "auto_paper"
    conn.close()
    storage_backend.reset_connection_cache()
