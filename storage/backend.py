"""Opt-in mirroring flag + best-effort write hooks.

``TOMAHAWK_STORAGE_BACKEND`` controls what happens on every save:

  * ``json``   (default) — SQLite is never touched. Zero behavior change.
  * ``dual``   — JSON stays authoritative; every save is *also* mirrored
                 into SQLite on a best-effort basis (mirror failures are
                 logged, never raised, and never block the JSON write).
  * ``sqlite`` — same best-effort mirroring as ``dual``. Reads still come
                 from JSON in this staged foundation; ``sqlite`` is reserved
                 for a future cutover once dual-run has proven parity via
                 ``storage.verify_parity`` / the ``tools/db_admin.py verify``
                 command.

This keeps the rollout risk-free: nothing changes for anyone until they
explicitly opt in, and even then a mirror failure can never take down a
save that already succeeded on disk.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from enum import Enum
from pathlib import Path
from typing import Any

from .db import get_connection
from .repositories import (
    ConfigRepository,
    DecisionsRepository,
    FillsRepository,
    JournalRepository,
    LedgerMetaRepository,
    PositionsRepository,
    SignalsRepository,
)

logger = logging.getLogger(__name__)

_conn_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None


class StorageBackend(str, Enum):
    JSON = "json"
    DUAL = "dual"
    SQLITE = "sqlite"


def get_backend_mode() -> StorageBackend:
    raw = (os.environ.get("TOMAHAWK_STORAGE_BACKEND") or "json").strip().lower()
    try:
        return StorageBackend(raw)
    except ValueError:
        logger.warning("unknown TOMAHAWK_STORAGE_BACKEND=%r, falling back to json", raw)
        return StorageBackend.JSON


def get_data_dir() -> Path:
    app_dir = Path(__file__).resolve().parent.parent
    return Path(os.environ.get("TOMAHAWK_DATA_DIR") or (app_dir / "data"))


def get_db_path() -> Path:
    return get_data_dir() / "tomahawk.sqlite3"


def _connection() -> sqlite3.Connection:
    """Process-wide connection, reopened if TOMAHAWK_DATA_DIR changes (tests)."""
    global _conn, _conn_path
    path = get_db_path()
    with _conn_lock:
        if _conn is None or _conn_path != path:
            if _conn is not None:
                try:
                    _conn.close()
                except sqlite3.Error:
                    pass
            _conn = get_connection(path, check_same_thread=False)
            _conn_path = path
        return _conn


def reset_connection_cache() -> None:
    """Test helper: force the next mirror call to reopen (new TOMAHAWK_DATA_DIR)."""
    global _conn, _conn_path
    with _conn_lock:
        if _conn is not None:
            try:
                _conn.close()
            except sqlite3.Error:
                pass
        _conn = None
        _conn_path = None


def _mirroring_enabled() -> bool:
    return get_backend_mode() != StorageBackend.JSON


def _guarded(fn_name: str, fn) -> None:
    if not _mirroring_enabled():
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 - mirroring must never break the JSON path
        logger.exception("sqlite mirror failed in %s (JSON write already succeeded)", fn_name)


def mirror_config(config: dict[str, Any]) -> None:
    _guarded("mirror_config", lambda: ConfigRepository(_connection()).replace_all(config))


def mirror_signals(signals: list[dict[str, Any]]) -> None:
    _guarded("mirror_signals", lambda: SignalsRepository(_connection()).replace_all(signals))


def mirror_ledger(ledger: dict[str, Any]) -> None:
    def _do() -> None:
        conn = _connection()
        LedgerMetaRepository(conn).replace(ledger)
        PositionsRepository(conn).replace_all(ledger.get("positions") or [])
        FillsRepository(conn).replace_all(ledger.get("fills") or [])

    _guarded("mirror_ledger", _do)


def mirror_journal(journal: list[dict[str, Any]]) -> None:
    _guarded("mirror_journal", lambda: JournalRepository(_connection()).replace_all(journal))


def mirror_decision_event(seq: int, events: list[dict[str, Any]]) -> None:
    _guarded("mirror_decision_event", lambda: DecisionsRepository(_connection()).replace_all(seq, events))
