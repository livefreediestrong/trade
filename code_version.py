"""Fingerprint of the desk's own source files.

Python stays loaded until restart. Templates auto-reload and static files are
served from disk, so browser-only edits need a page reload, not a broker
reconnection. Full-source hashes still identify automated-test receipts.
"""
from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PATTERNS = ("*.py", "templates/**/*.html", "static/**/*.js", "static/**/*.css")
BACKEND_PATTERNS = ("*.py",)
SKIP_DIRS = {".git", ".venv", "venv", "data", "tests", "_archive", "__pycache__", "node_modules", "releases"}
CHECK_EVERY_SEC = 30.0

_lock = threading.Lock()
_digests: dict[str, tuple[int, int, str]] = {}  # absolute path -> (size, mtime_ns, sha1)
_test_cache: dict[str, tuple[tuple, str]] = {}  # root -> (file stat signature, fingerprint)
_state: dict[str, Any] = {"started": None, "current": None, "checked": 0.0,
                          "backend_started": None, "backend_current": None}


def _files(root: Path, patterns=PATTERNS) -> list[Path]:
    found = set()
    for pattern in patterns:
        for path in root.glob(pattern):
            rel = path.relative_to(root)
            if path.is_file() and not (set(rel.parts[:-1]) & SKIP_DIRS):
                found.add(path)
    return sorted(found)


def fingerprint(root: Path = ROOT) -> str:
    """Content hash of source files; unchanged files reuse their cached digest."""
    return _fingerprint(root, PATTERNS)


def backend_fingerprint(root: Path = ROOT) -> str:
    """Only loaded Python files require the desk process to restart."""
    return _fingerprint(root, BACKEND_PATTERNS)


def _fingerprint(root: Path, patterns) -> str:
    total = hashlib.sha1()
    for path in _files(root, patterns):
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        key = str(path.resolve())
        cached = _digests.get(key)
        if cached and cached[:2] == (stat.st_size, stat.st_mtime_ns):
            digest = cached[2]
        else:
            try:
                digest = hashlib.sha1(path.read_bytes()).hexdigest()
            except OSError:
                continue
            _digests[key] = (stat.st_size, stat.st_mtime_ns, digest)
        total.update(f"{rel}\0{digest}\n".encode("utf-8"))
    return total.hexdigest()[:16]


def mark_started() -> str:
    with _lock:
        if _state["started"] is None:
            _state["started"] = _state["current"] = fingerprint()
            _state["backend_started"] = _state["backend_current"] = backend_fingerprint()
            _state["checked"] = time.monotonic()
        return _state["started"]


def test_fingerprint(root: Path = ROOT) -> str:
    """Full-suite receipts also depend on tests, runner and dependency pins."""
    paths = {p for p in (root / "tests").rglob("*") if p.is_file() and p.suffix in (".py", ".cjs", ".js")}
    paths.update(root.glob("requirements*.txt"))
    paths.update(root / name for name in ("pytest.ini", "tools/desk_upkeep.py", "tools/pnl_probe.py"))
    files = []
    for path in sorted(paths):
        try:
            stat = path.stat()
        except OSError:
            continue
        files.append((path, stat.st_size, stat.st_mtime_ns))
    # The maintenance page polls this; re-read contents only when a file changed.
    signature = tuple((str(p), size, mtime) for p, size, mtime in files)
    with _lock:
        cached = _test_cache.get(str(root))
        if cached and cached[0] == signature:
            return cached[1]
    total = hashlib.sha256()
    for path, _, _ in files:
        total.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    result = total.hexdigest()[:16]
    with _lock:
        _test_cache[str(root)] = (signature, result)
    return result


def status(*, force: bool = False) -> dict[str, Any]:
    """Report restart needs and full source available to the next page load.

    `started` is immutable evidence about process startup. `serving` identifies
    current files only when loaded Python matches disk; it does not assert that
    an already-open tab has fetched the latest browser files.
    """
    started = mark_started()
    with _lock:
        if force or time.monotonic() - _state["checked"] >= CHECK_EVERY_SEC:
            _state["current"] = fingerprint()
            _state["backend_current"] = backend_fingerprint()
            _state["checked"] = time.monotonic()
        current = _state["current"]
        backend_started = _state.get("backend_started")
        backend_current = _state.get("backend_current")
    # No baseline means a source change cannot safely be called browser-only.
    stale = (backend_current != backend_started if backend_started and backend_current
             else current != started)
    return {
        "started": started,
        "on_disk": current,
        "stale": stale,
        "serving": None if stale else current,
        "browser_files_changed": current != started and not stale,
        "message": ("This folder has updated desk code that the running desk has not loaded. "
                    "Reopen the desktop shortcut to restart the desk and load it.") if stale else None,
    }
