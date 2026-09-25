"""Versioned source recovery. Archives never contain credentials or trading state."""
from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import zipfile

VERSION = "source-release-v1"
FOLDERS = {"static", "templates", "tools", "tests", "docs"}
EXTENSIONS = {".py", ".js", ".cjs", ".css", ".html", ".md", ".ps1", ".cmd", ".bat", ".vbs", ".png", ".svg", ".ico", ".txt", ".ttf", ".woff2"}
ROOT_FILES = {"README.md", "requirements.txt", "Launch.vbs", "Launch.bat", "Start-Tomahawk.ps1",
              ".env.example", ".gitignore"}


def allowed(name):
    path = Path(name)
    if name in {".env.example", ".gitignore"}:
        return True
    return (not path.is_absolute() and ".." not in path.parts and ":" not in name and "\\" not in name
            and not any(p.startswith(".") or p == "__pycache__" for p in path.parts)
            and (path.parts[0] in FOLDERS if len(path.parts) > 1 else
                 name in ROOT_FILES or (path.suffix == ".py" and not path.name.startswith("_")))
            and path.suffix.lower() in EXTENSIONS
            and (path.suffix != ".txt" or name == "requirements.txt"))


def source_files(root):
    root = Path(root).resolve()
    candidates = list(root.iterdir())
    for folder in FOLDERS:
        base = root/folder
        if base.is_dir() and not base.is_symlink():
            candidates.extend(base.rglob("*"))
    return sorted(p for p in candidates if p.is_file() and not p.is_symlink()
                  and p.resolve().is_relative_to(root) and allowed(p.relative_to(root).as_posix()))


def manifest(root):
    root = Path(root)
    files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files(root)}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
    return {"version": VERSION, "sha256": digest, "file_count": len(files), "files": files,
            "data_policy": "Credentials, config, journals, databases and fills excluded. Recovery never rewinds trades."}


def pack(root, output):
    root, output = Path(root), Path(output)
    info = manifest(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents overwriting a known-good release.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("release-manifest.json", json.dumps(info, indent=2))
        for name in info["files"]:
            archive.write(root/name, name)
    return info


def verify(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or "release-manifest.json" not in names:
            raise ValueError("Duplicate members or missing manifest")
        if sum(i.file_size for i in archive.infolist()) > 150_000_000:
            raise ValueError("Unexpectedly large source archive")
        info = json.loads(archive.read("release-manifest.json"))
        files = info.get("files", {})
        if set(names) != set(files)|{"release-manifest.json"} or info.get("version") != VERSION:
            raise ValueError("Archive membership/version mismatch")
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        if info.get("sha256") != digest:
            raise ValueError("Manifest fingerprint mismatch")
        for name, expected in files.items():
            if not allowed(name):
                raise ValueError("Archive contains forbidden code or state path")
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("Release content fingerprint mismatch")
            if name.endswith(".py"):
                ast.parse(content.decode("utf-8-sig"), filename=name)
    return info


def _recovery_settings(root, port=None, host=None):
    from dotenv import dotenv_values
    values = dict(dotenv_values(root/".env", encoding="utf-8-sig", interpolate=False)) if (root/".env").exists() else {}
    values.update(os.environ)  # Match application startup: process values win.
    server = str(host if host is not None else values.get("TOMAHAWK_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    try:
        number = int(port if port is not None else values.get("TOMAHAWK_PORT", "5056"))
    except (ValueError, TypeError) as exc:
        raise ValueError("Configured TOMAHAWK_PORT is invalid; recovery cannot verify the desk is stopped") from exc
    if not 1 <= number <= 65535:
        raise ValueError("Configured TOMAHAWK_PORT is outside 1-65535")
    data = Path(values.get("TOMAHAWK_DATA_DIR") or root/"data")
    if not data.is_absolute():
        data = root/data
    lock = Path(values.get("TOMAHAWK_INSTANCE_LOCK") or data/"tomahawk.pid")
    if not lock.is_absolute():
        lock = root/lock
    return server, number, lock.resolve()


def _pid_is_running(pid):
    if pid <= 0:
        raise ValueError("Invalid instance PID; recovery cannot verify the desk is stopped")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Unknown/access denied stays blocked.
        try:
            return kernel.WaitForSingleObject(handle, 0) != 0
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        raise ValueError("Unable to verify the instance PID; stop the desk before recovery") from exc
    return True


def _require_stopped(root, port=None, host=None):
    server, number, lock = _recovery_settings(root, port, host)
    if lock.exists():
        try:
            pid = int(lock.read_text(encoding="ascii").strip())
        except (OSError, ValueError) as exc:
            raise ValueError("Instance lock is unreadable; recovery cannot verify the desk is stopped") from exc
        if _pid_is_running(pid):
            raise ValueError("Stop the desk before recovery. Its configured instance PID is still running or unverifiable.")
    server = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(server, server)
    try:
        with socket.socket(socket.AF_INET6 if ":" in server else socket.AF_INET) as client:
            client.settimeout(1)
            result = client.connect_ex((server, number))
    except OSError as exc:
        raise ValueError("Unable to check the configured endpoint; recovery cannot verify the desk is stopped") from exc
    if result == 0:
        raise ValueError("Stop the desk before recovery. The tool never stops a running broker or application.")
    if result not in (errno.ECONNREFUSED, 10061):
        raise ValueError("Configured endpoint did not confirm a closed port; recovery cannot verify the desk is stopped")


def restore(archive_path, root, *, apply=False, port=None, host=None):
    info = verify(archive_path)
    root = Path(root).resolve()
    for name in info["files"]:
        target = (root/name).resolve()
        if not target.is_relative_to(root):
            raise ValueError("Recovery target escapes application directory")
    if apply:
        # Never stop processes or replay orders as a side effect of recovery.
        _require_stopped(root, port, host)
        backup = root/"releases"/("before-recovery-"+manifest(root)["sha256"][:16]+".zip")
        if not backup.exists():
            pack(root, backup)
        with zipfile.ZipFile(archive_path) as archive:
            # Validate everything above before replacing any file.
            for name in info["files"]:
                target = root/name
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
                    temp.write(archive.read(name))
                    temporary = Path(temp.name)
                temporary.replace(target)
    return {"applied": apply, "files": list(info["files"]), "release": info["sha256"],
            "note": "Only archived source files are replaced. Extra files and all durable state are retained. Restart with the existing launcher, then reconcile broker state."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["manifest", "pack", "verify", "restore"])
    parser.add_argument("--root", type=Path, default=Path(__file__).parent)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--apply", action="store_true", help="Restore source only; default is a reviewable dry run")
    parser.add_argument("--port", type=int, help="Override the configured desk port for the stopped-instance check")
    parser.add_argument("--host", help="Override the configured desk host for the stopped-instance check")
    args = parser.parse_args()
    if args.action != "manifest" and not args.archive:
        parser.error("--archive is required")
    if args.action == "manifest":
        result = manifest(args.root)
    elif args.action == "pack":
        result = pack(args.root, args.archive)
    elif args.action == "verify":
        result = verify(args.archive)
    else:
        result = restore(args.archive, args.root, apply=args.apply, port=args.port, host=args.host)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
