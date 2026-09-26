"""Verified local recovery snapshots. No restore or broker replay is exposed by the app."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from datetime import datetime, timezone
from contextlib import closing

from flask import Blueprint, jsonify

LOCK = threading.Lock()
VERSION = "desk-state-v1"
SUFFIXES = {".json", ".sqlite3", ".sqlite", ".db"}


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify(path):
    with tempfile.TemporaryDirectory() as staging, zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        info = json.loads(archive.read("state-manifest.json"))
        files = info.get("files")
        if (info.get("version") != VERSION or not isinstance(files, dict) or not files or
                len(names) != len(set(names)) or set(names) != set(files) | {"state-manifest.json"} or
                sum(i.file_size for i in archive.infolist()) > 2_000_000_000):
            raise ValueError("Invalid state archive")
        for name, sha in files.items():
            p = Path(name)
            if p.name != name or "/" in name or "\\" in name or ":" in name or name.startswith(".") or p.suffix not in SUFFIXES:
                raise ValueError("Unsafe state member")
            target = Path(staging)/name
            with archive.open(name) as src, target.open("wb") as dst:
                import shutil
                shutil.copyfileobj(src, dst)
            if digest(target) != sha:
                raise ValueError("State checksum mismatch")
            if p.suffix == ".json":
                json.loads(target.read_text(encoding="utf-8-sig"))
            else:
                with closing(sqlite3.connect(target.as_uri()+"?mode=ro", uri=True)) as db:
                    if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise ValueError("Database integrity check failed")
    return info


def create(desk):
    if not LOCK.acquire(blocking=False):
        raise ValueError("A backup is already running")
    try:
        data = Path(desk.DATA_DIR).resolve()
        destination = data/"backups"
        destination.mkdir(exist_ok=True)
        if destination.is_symlink() or destination.resolve().parent != data:
            raise ValueError("Backup directory must stay inside the desk data directory")
        started = datetime.now(timezone.utc).isoformat()
        name = "state-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+uuid.uuid4().hex[:8]+".zip"
        with tempfile.TemporaryDirectory(dir=destination) as staging:
            staging = Path(staging)
            with desk._lock:
                if desk._CORRUPT_PATHS:
                    raise ValueError("Desk state needs recovery; no healthy backup can be declared")
                paths = sorted(p for p in data.iterdir() if p.is_file() and not p.is_symlink() and
                               not p.name.startswith(".") and p.suffix in SUFFIXES)
                for path in paths:
                    if path.suffix == ".json":
                        content = path.read_bytes()
                        json.loads(content.decode("utf-8-sig"))
                        (staging/path.name).write_bytes(content)
            # Online SQLite backup includes committed WAL data without copying a live WAL file.
            for path in paths:
                if path.suffix == ".json":
                    continue
                deadline = time.monotonic()+30
                def progress(*_):
                    if time.monotonic() > deadline:
                        raise TimeoutError("Database backup timed out; previous backups retained")
                with closing(sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=5)) as src:
                    with closing(sqlite3.connect(staging/path.name)) as dst:
                        src.backup(dst, pages=512, progress=progress, sleep=.05)
            files = {p.name: digest(staging/p.name) for p in paths}
            info = {"version": VERSION, "started_at": started, "files": files,
                    "consistency": "JSON captured under the desk lock; each database is an online snapshot. Stores may have different capture times.",
                    "restore_policy": "Manual recovery only while stopped, followed by broker reconciliation. Never replay saved orders.",
                    "coverage": "Top-level JSON and SQLite stores. Credentials, logs, media and nested caches excluded."}
            temporary = staging/"state.zip"
            with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in paths:
                    archive.write(staging/path.name, path.name)
                archive.writestr("state-manifest.json", json.dumps(info, indent=2))
            verify(temporary)
            os.link(temporary, destination/name)
        receipt = {"ok": True, "archive": name, "verified_at": datetime.now(timezone.utc).isoformat(),
                   "sha256": digest(destination/name), "bytes": (destination/name).stat().st_size,
                   "files": len(files), "coverage": info["coverage"], "restore_policy": info["restore_policy"]}
        desk._save_json(destination/"latest.json", receipt)
        return receipt
    finally:
        LOCK.release()


def status(desk):
    path = Path(desk.DATA_DIR)/"backups"/"latest.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        name = row["archive"]
        if Path(name).name != name or not name.startswith("state-") or not name.endswith(".zip"):
            raise ValueError("Invalid backup receipt")
        archive = path.parent/name
        if archive.is_symlink() or not archive.is_file() or archive.stat().st_size != row["bytes"]:
            raise ValueError("Last backup is missing or changed; create a new verified snapshot")
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(row["verified_at"])).total_seconds()
        failure = {}
        try:
            failure = json.loads((path.parent/"last_failure.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        last_error = failure.get("error") if isinstance(failure, dict) and str(failure.get("at") or "") > row["verified_at"] else None
        return {**row, "stale": age > 36*3600, "busy": LOCK.locked(), "last_error": last_error}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"ok": False, "busy": LOCK.locked(), "error": "No verified state backup" if not path.exists() else str(exc)[:160]}


def register(app, desk):
    bp = Blueprint("backups", __name__)

    @bp.get("/api/backups")
    def get_status():
        return jsonify(status(desk))

    @bp.post("/api/backups")
    def make_backup():
        try:
            return jsonify(create(desk))
        except Exception as exc:
            error = f"Backup failed: {type(exc).__name__}: {str(exc)[:160]}"
            try:
                destination = Path(desk.DATA_DIR)/"backups"
                if destination.is_dir() and not destination.is_symlink():
                    desk._save_json(destination/"last_failure.json", {"at": datetime.now(timezone.utc).isoformat(), "error": error})
            except OSError:
                pass
            return jsonify(ok=False, error=error), 503

    app.register_blueprint(bp)
