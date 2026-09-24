"""Fingerprint of the desk's own source files.

A running desk keeps serving the code it started with. After an update
(git pull, copied files) the launcher and the page compare the fingerprint
loaded at startup with the files on disk and offer a restart instead of
silently serving stale code.
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PATTERNS = ("*.py", "templates/**/*.html", "static/**/*.js", "static/**/*.css")
SKIP_DIRS = {".git", ".venv", "venv", "data", "tests", "_archive", "__pycache__", "node_modules", "releases"}
CHECK_EVERY_SEC = 30.0

_lock = threading.Lock()
_digests: dict[str, tuple[int, int, str]] = {}  # relpath -> (size, mtime_ns, sha1)
_state: dict[str, Any] = {"started": None, "current": None, "checked": 0.0}


def _files(root: Path) -> list[Path]:
    found = set()
    for pattern in PATTERNS:
        for path in root.glob(pattern):
            rel = path.relative_to(root)
            if path.is_file() and not (set(rel.parts[:-1]) & SKIP_DIRS):
                found.add(path)
    return sorted(found)


def fingerprint(root: Path = ROOT) -> str:
    """Content hash of source files; unchanged files reuse their cached digest."""
    total = hashlib.sha1()
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = _digests.get(rel)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            digest = cached[2]
        else:
            try:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                continue
            _digests[rel] = (stat.st_size, stat.st_mtime_ns, digest)
        total.update(f"{rel}\0{digest}\n".encode("utf-8"))
    return total.hexdigest()[:16]


def mark_started() -> str:
    with _lock:
        if _state["started"] is None:
            _state["started"] = _state["current"] = fingerprint()
            _state["checked"] = time.monotonic()
        return _state["started"]


def status(*, force: bool = False) -> dict[str, Any]:
    """{'started', 'on_disk', 'stale'}; re-reads the files at most every CHECK_EVERY_SEC."""
    started = mark_started()
    with _lock:
        if force or time.monotonic() - _state["checked"] >= CHECK_EVERY_SEC:
            _state["current"] = fingerprint()
            _state["checked"] = time.monotonic()
        current = _state["current"]
    stale = current != started
    return {
        "started": started,
        "on_disk": current,
        "stale": stale,
        "message": ("This folder has updated desk code that the running desk has not loaded. "
                    "Reopen the desktop shortcut to restart the desk and load it.") if stale else None,
    }
