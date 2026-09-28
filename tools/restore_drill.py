"""Restore a backup COPY, exercise core readers offline, and retain a receipt.

This never starts a server, resumes a worker, or restores production state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desk_backups


def probe(destination):
    # Install transport denial BEFORE importing application/provider modules.
    import socket
    def denied(*args, **kwargs):
        raise RuntimeError("restore_drill_transport_blocked")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.create_connection = denied
    os.environ.update(TOMAHAWK_DATA_DIR=str(destination), TOMAHAWK_NO_BG="1",
                      BROKER_PROVIDER="alpaca", ALPACA_PAPER="true", ALPACA_API_KEY="",
                      ALPACA_API_SECRET="", ALPACA_SECRET_KEY="")
    import app as desk
    if desk.DATA_DIR.resolve() != destination or os.environ.get("TOMAHAWK_NO_BG") != "1":
        raise RuntimeError("Isolated application configuration was not honored")
    try:
        socket.create_connection(("127.0.0.1", 4001))
    except RuntimeError as exc:
        if str(exc) != "restore_drill_transport_blocked":
            raise
    else:
        raise RuntimeError("Transport denial failed")
    config = desk.load_config()
    ledger = desk.load_ledger()
    signals = desk.load_signals()
    journal = desk.load_journal()
    agent = desk._live_agent.load()
    conversations = desk._companion_chat.load()
    weekly = desk._research_companion.weekly.load()
    import option_activity
    option_evidence = option_activity.load(desk)
    if desk._CORRUPT_PATHS:
        raise RuntimeError("Restored core state failed application readers")
    # These are counts and flags, not account IDs, credentials or positions.
    return {"core_readers": "passed", "transport": "blocked before app import; denial exercised",
            "workers": "disabled", "saved_mode": config.get("mode"),
            "signals": len(signals), "journal_entries": len(journal),
            "pending_broker_orders": len(ledger.get("pending_broker_orders") or []),
            "agent_days": len(agent.get("days") or {}),
            "companion_conversations": len(conversations.get("conversations") or {}),
            "weekly_reports": len(weekly.get("reports") or {}),
            "option_activity_versions": len(option_evidence["events"]),
            "saved_orders_are_evidence_only": True}


def restore_copy(archive, destination):
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    from dotenv import dotenv_values
    settings = dotenv_values(ROOT / ".env", interpolate=False)
    live_data = Path(os.environ.get("TOMAHAWK_DATA_DIR") or settings.get("TOMAHAWK_DATA_DIR") or ROOT / "data")
    if not live_data.is_absolute():
        live_data = ROOT / live_data
    if destination == live_data.resolve() or destination.is_relative_to(live_data.resolve()):
        raise ValueError("Restore drill destination must be outside active data")
    if destination.exists():
        raise ValueError("Restore drill requires a new destination; never overwrite state")
    info = desk_backups.verify(archive)
    archive_hash = desk_backups.digest(archive)
    destination.mkdir(parents=True, exist_ok=False)
    stores = {}
    with zipfile.ZipFile(archive) as bundle:
        for name, expected in info["files"].items():
            path = destination / name
            with bundle.open(name) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst)
            if desk_backups.digest(path) != expected:
                raise ValueError("Restored copy checksum mismatch")
            if path.suffix == ".json":
                value = json.loads(path.read_text(encoding="utf-8-sig"))
                stores[name] = {"sha256": expected, "type": type(value).__name__,
                                "top_level_entries": len(value) if isinstance(value, (list, dict)) else None}
            else:
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Restored database failed integrity check")
                    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
                    counts = {t: db.execute('SELECT COUNT(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in tables}
                stores[name] = {"sha256": expected, "tables": counts, "quick_check": "ok"}
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--probe", str(destination)],
                          cwd=ROOT, capture_output=True, text=True, timeout=90)
    if proc.returncode:
        # Do not echo arbitrary provider initialization logs or state values.
        raise RuntimeError("Isolated application reader probe failed (exit " + str(proc.returncode) + ")")
    line = next((x[8:] for x in proc.stdout.splitlines() if x.startswith("RECEIPT:")), None)
    if line is None:
        raise RuntimeError("Missing isolated-reader receipt")
    reader = json.loads(line)
    unchanged = all(desk_backups.digest(destination / name) == sha for name, sha in info["files"].items())
    if not unchanged:
        raise RuntimeError("Application readers modified restored stores")
    if desk_backups.digest(archive) != archive_hash:
        raise RuntimeError("Source backup changed during drill")
    return {"ok": True, "archive_sha256": archive_hash, "stores": stores,
            "application": reader, "restored_stores_unchanged": unchanged,
            "production_restore": False,
            "next_before_production_recovery": "Stop desk; preserve current state; reconcile all orders, fills, positions and counters against broker evidence. Never replay old intents."}


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--probe":
        print("RECEIPT:" + json.dumps(probe(Path(sys.argv[2]).resolve())))
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--archive", required=True)
        parser.add_argument("--destination", required=True)
        parser.add_argument("--report", required=True)
        args = parser.parse_args()
        result = restore_copy(args.archive, args.destination)
        Path(args.report).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({"ok": result["ok"], "stores": len(result["stores"]), "report": args.report}))
