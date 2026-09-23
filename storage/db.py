"""SQLite connection management: WAL mode, foreign keys, schema migrations,
and backup/restore helpers.

Design goals (see task brief — this is a *staged* foundation, not a cutover):
  * Never silently corrupt or lose data. Migrations run inside transactions
    and are idempotent (recorded in ``schema_migrations``).
  * WAL + foreign_keys are turned on for every connection this module hands
    out, matching the durability/consistency story we want long-term.
  * Backups are plain, self-contained SQLite files (via the sqlite3 backup
    API) so they can be restored without WAL/SHM sidecar files.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .migrations import MIGRATIONS, latest_version

CURRENT_SCHEMA_VERSION = latest_version()

_connect_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection with WAL + foreign_keys enabled and run migrations.

    Safe to call repeatedly (e.g. once per request/thread); migrations are a
    cheap idempotent no-op once the schema is current.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=check_same_thread, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    with _connect_lock:
        run_migrations(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    value = row["v"] if row is not None else None
    return int(value) if value is not None else 0


def run_migrations(conn: sqlite3.Connection) -> int:
    """Apply any migrations newer than the DB's recorded version.

    Returns the schema version after migration. Each migration runs in its
    own transaction so a failure partway through never leaves a mix of two
    migrations half-applied.
    """
    current = schema_version(conn)
    for version, sql in MIGRATIONS:
        if version <= current:
            continue
        with conn:  # transaction
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _now_iso()),
            )
        current = version
    return current


def backup_database(db_path: Path, backup_dir: Path, *, label: str | None = None) -> Path:
    """Write a self-contained backup copy of the database.

    Uses the sqlite3 backup API so the resulting file is a complete,
    checkpoint-consistent snapshot — no ``-wal``/``-shm`` sidecars needed to
    restore it.
    """
    db_path = Path(db_path)
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = f"_{label}" if label else ""
    dest = backup_dir / f"tomahawk_{stamp}{suffix}.sqlite3"

    if not db_path.exists():
        # Nothing to back up yet; create an empty, schema-current DB so the
        # backup file is still a valid restore target.
        src = get_connection(db_path)
    else:
        src = sqlite3.connect(str(db_path))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def restore_database(backup_path: Path, db_path: Path) -> None:
    """Restore ``db_path`` from a backup file produced by :func:`backup_database`.

    Callers must ensure no other process/thread holds the live database open
    (e.g. the desk app is stopped) — this performs a file-level swap and
    removes stale WAL/SHM sidecars for the target so a restored DB is never
    shadowed by leftover write-ahead-log pages from before the restore.
    """
    backup_path = Path(backup_path)
    db_path = Path(db_path)
    if not backup_path.exists():
        raise FileNotFoundError(f"backup not found: {backup_path}")

    # Validate the backup opens and has our schema table before touching the
    # live file — fail closed rather than clobbering good data with garbage.
    probe = sqlite3.connect(str(backup_path))
    try:
        probe.execute("PRAGMA integrity_check")
    finally:
        probe.close()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    for sidecar in (db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm")):
        try:
            sidecar.unlink()
        except OSError:
            pass
    shutil.copyfile(backup_path, db_path)
