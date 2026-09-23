"""Staged SQLite persistence foundation for the Signal Desk.

This package adds an *opt-in* schema-versioned SQLite store that sits
alongside the existing JSON files under ``data/``. JSON remains the
authoritative source of truth unless an operator explicitly switches
``TOMAHAWK_STORAGE_BACKEND`` to ``dual`` or ``sqlite`` — see
``storage.backend`` for the flag semantics and ``README.md`` /
``DEPLOY_NOTES.md`` for the migration story.

Nothing in this package is imported by ``app.py`` at module load time in a
way that changes existing behavior: importing ``storage`` has zero side
effects on the JSON files, and no database file is created until a caller
explicitly asks for a connection.
"""

from __future__ import annotations

from .backend import (
    StorageBackend,
    get_backend_mode,
    get_db_path,
    mirror_config,
    mirror_decision_event,
    mirror_journal,
    mirror_ledger,
    mirror_signals,
)
from .db import (
    CURRENT_SCHEMA_VERSION,
    backup_database,
    get_connection,
    restore_database,
    run_migrations,
    schema_version,
)
from .json_bridge import export_all_to_json, import_all_from_json, verify_parity

__all__ = [
    "StorageBackend",
    "get_backend_mode",
    "get_db_path",
    "mirror_config",
    "mirror_decision_event",
    "mirror_journal",
    "mirror_ledger",
    "mirror_signals",
    "CURRENT_SCHEMA_VERSION",
    "backup_database",
    "get_connection",
    "restore_database",
    "run_migrations",
    "schema_version",
    "export_all_to_json",
    "import_all_from_json",
    "verify_parity",
]
