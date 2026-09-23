"""Command-line tool for the SQLite persistence foundation.

Usage (run from the repo root, same venv as the desk):

    python tools/db_admin.py status
    python tools/db_admin.py import
    python tools/db_admin.py export
    python tools/db_admin.py verify
    python tools/db_admin.py backup [--label LABEL]
    python tools/db_admin.py restore <backup-file>

All commands honor TOMAHAWK_DATA_DIR the same way the desk app does, so you
can safely dry-run against a copy of `data/` before touching the real one:

    $env:TOMAHAWK_DATA_DIR = "C:\\path\\to\\scratch-data"
    python tools/db_admin.py import

Nothing here deletes or overwrites a JSON file unless you explicitly run
`export`, and `import`/`verify` never write to the JSON files at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import storage  # noqa: E402


def _data_dir() -> Path:
    return storage.get_db_path().parent


def cmd_status(_: argparse.Namespace) -> int:
    data_dir = _data_dir()
    db_path = storage.get_db_path()
    conn = storage.get_connection(db_path)
    version = storage.schema_version(conn)
    mode = storage.get_backend_mode().value
    print(f"data dir:        {data_dir}")
    print(f"db path:         {db_path} ({'exists' if db_path.exists() else 'not yet created'})")
    print(f"schema version:  {version} (current: {storage.CURRENT_SCHEMA_VERSION})")
    print(f"backend mode:    {mode} (TOMAHAWK_STORAGE_BACKEND)")
    conn.close()
    return 0


def cmd_import(_: argparse.Namespace) -> int:
    data_dir = _data_dir()
    conn = storage.get_connection(storage.get_db_path())
    counts = storage.import_all_from_json(conn, data_dir)
    conn.close()
    print("Imported JSON -> SQLite:")
    for domain, count in counts.items():
        print(f"  {domain:14s} {count}")
    return 0


def cmd_export(_: argparse.Namespace) -> int:
    data_dir = _data_dir()
    conn = storage.get_connection(storage.get_db_path())
    written = storage.export_all_to_json(conn, data_dir)
    conn.close()
    print("Exported SQLite -> JSON:")
    for name in written:
        print(f"  {name}")
    return 0


def cmd_verify(_: argparse.Namespace) -> int:
    data_dir = _data_dir()
    conn = storage.get_connection(storage.get_db_path())
    result = storage.verify_parity(conn, data_dir)
    conn.close()
    ok = True
    print("JSON <-> SQLite parity:")
    for domain, info in result.items():
        status = "OK" if info["match"] else "MISMATCH"
        if not info["match"]:
            ok = False
        print(f"  {domain:12s} {status:8s} json={info['json_count']} db={info['db_count']}")
    if not ok:
        print("\nRun `import` (or `export`) to reconcile before relying on SQLite.")
    return 0 if ok else 1


def cmd_backup(args: argparse.Namespace) -> int:
    data_dir = _data_dir()
    dest = storage.backup_database(storage.get_db_path(), data_dir / "backups", label=args.label)
    print(f"Backup written: {dest}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    backup_path = Path(args.backup_file)
    storage.restore_database(backup_path, storage.get_db_path())
    print(f"Restored {storage.get_db_path()} from {backup_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show data dir, db path, schema version, backend mode").set_defaults(func=cmd_status)
    sub.add_parser("import", help="import data/*.json into SQLite (JSON untouched)").set_defaults(func=cmd_import)
    sub.add_parser("export", help="write SQLite contents back out as data/*.json").set_defaults(func=cmd_export)
    sub.add_parser("verify", help="diff JSON vs SQLite per domain, no writes").set_defaults(func=cmd_verify)

    p_backup = sub.add_parser("backup", help="write a timestamped SQLite backup file")
    p_backup.add_argument("--label", default=None, help="optional suffix, e.g. pre-cutover")
    p_backup.set_defaults(func=cmd_backup)

    p_restore = sub.add_parser("restore", help="restore SQLite from a backup file")
    p_restore.add_argument("backup_file", help="path to a file produced by `backup`")
    p_restore.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
