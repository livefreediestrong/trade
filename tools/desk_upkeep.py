"""Tomahawk desk auto upkeep. Stdlib only; run with the desk's .venv python.

    python tools/desk_upkeep.py watchdog   # every few minutes
    python tools/desk_upkeep.py daily      # once a day after the close
    python tools/desk_upkeep.py status     # print the last report

Hard rules (owner decision): upkeep never blocks or changes live trading.
It never edits config, orders, positions, sessions or broker settings, never
stops or restarts a running desk, and never logs Gateway out. The watchdog
only (re)runs the normal launcher (without opening Gateway) when the desk is
unreachable. When the broker socket is down it asks the running desk, as an
automatic caller, whether Gateway should be reopened; the desk never reopens a
Gateway window the owner closed. It never submits orders.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
LAUNCHER = ROOT / "Start-Tomahawk.ps1"
ARCHIVE = ROOT / "_archive"
KEEP_AT_ROOT = {"_archive", "__pycache__"}
NEVER_TOUCH_DATA = {"config.json", "ledger.json", "journal.json", "live_agent.json", "decisions.json",
                    "broker_execution_journal.json", ".gitkeep"}


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is not None:
        return value
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            key, sep, raw = line.partition("=")
            if sep and key.strip() == name:
                raw = raw.split(" #", 1)[0].strip().strip("'\"")
                return raw or default
    return default


PORT = int(_env("TOMAHAWK_PORT", "5056") or 5056)
DATA = Path(_env("TOMAHAWK_DATA_DIR", str(ROOT / "data")))
if not DATA.is_absolute():
    DATA = ROOT / DATA
UPKEEP = DATA / "upkeep"
BACKUP_DAYS = int(_env("UPKEEP_BACKUP_DAYS", "30") or 30)
LOG_MAX_MB = float(_env("UPKEEP_LOG_MAX_MB", "25") or 25)


def now() -> datetime:
    return datetime.now().astimezone()


def log(message: str) -> None:
    UPKEEP.mkdir(parents=True, exist_ok=True)
    line = f"{now().isoformat(timespec='seconds')} {message}"
    print(line)
    path = UPKEEP / "upkeep.log"
    try:
        if path.exists() and path.stat().st_size > 2_000_000:
            path.replace(path.with_suffix(".log.1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load(name: str) -> dict:
    try:
        return json.loads((UPKEEP / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(name: str, data: dict) -> None:
    UPKEEP.mkdir(parents=True, exist_ok=True)
    tmp = UPKEEP / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(UPKEEP / name)


def health(timeout: float = 10.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=timeout) as resp:
            data = json.load(resp)
        return data if isinstance(data, dict) and data.get("app_id") == "tomahawk-desk" else None
    except Exception:
        return None


def run_launcher(reason: str) -> dict:
    """Normal launcher, headless, desk only. Reuses a healthy desk; never places orders.

    -NoBroker: Gateway launches are decided by the desk's automatic-launch
    policy (ensure_gateway), never by an unattended launcher run.
    """
    cmd = ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
           "-File", str(LAUNCHER), "-NoBrowser", "-NoDialogs", "-NoBroker"]
    log(f"watchdog: running launcher ({reason})")
    try:
        done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=240,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"exit": done.returncode, "tail": (done.stdout + done.stderr)[-400:]}
    except Exception as exc:  # launcher problems are reported, never fatal
        return {"exit": None, "error": f"{type(exc).__name__}: {exc}"[:300]}


def ensure_gateway_via_desk(timeout: float = 30.0) -> dict:
    """Ask the running desk to apply its automatic Gateway policy."""
    body = json.dumps({"launch_if_down": True, "automatic": True}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/broker-ensure-gateway", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except Exception as exc:  # reported, never fatal
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if not isinstance(data, dict):
        return {"ok": False, "error": "unexpected response"}
    return {k: data.get(k) for k in ("ok", "launched", "port_open", "automatic_launch_held", "note")}


def watchdog() -> dict:
    state = _load("watchdog.json")
    last_launch = float(state.get("last_launch_ts") or 0)
    result = {"at": now().isoformat(), "action": "none"}
    h = health()
    if h is None:
        time.sleep(20)  # ride out a restart already in progress
        h = health()
    cooldown_ok = time.time() - last_launch >= 10 * 60
    if h is None:
        result["desk"] = "down"
        if cooldown_ok:
            result["action"], result["launcher"] = "relaunch_desk", run_launcher("desk unreachable")
            state["last_launch_ts"] = time.time()
        else:
            result["action"] = "cooldown"
    else:
        broker = h.get("broker") or {}
        result["desk"] = "ok"
        result["broker"] = {k: broker.get(k) for k in ("status", "connected", "connected_label")}
        if broker.get("configured") and not broker.get("connected"):
            # The desk decides: it relaunches only a Gateway that signed in and
            # then went away, never one the owner closed. Sign-in / 2FA stays
            # with the owner.
            result["action"], result["gateway"] = "ensure_gateway", ensure_gateway_via_desk()
    state["last"] = result
    _save("watchdog.json", state)
    if result["action"] != "none":
        log(f"watchdog: {result['action']} desk={result.get('desk')}")
    return result


def _unique(dest: Path) -> Path:
    if not dest.exists():
        return dest
    return dest.with_name(dest.name + "_" + now().strftime("%Y%m%d%H%M%S"))


def archive_root_clutter() -> list[str]:
    """Move root-level `_*` backups/scratch into _archive (never deletes)."""
    moved = []
    for item in ROOT.iterdir():
        if not item.name.startswith("_") or item.name in KEEP_AT_ROOT:
            continue
        bucket = "backups" if item.is_dir() and item.name.startswith("_backup_") else "scratch"
        dest = _unique(ARCHIVE / bucket / item.name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(item), str(dest))
            moved.append(item.name)
        except OSError as exc:
            log(f"daily: could not archive {item.name}: {exc}")
    # One-off probe dumps written into data/ (e.g. _pnl_probe_*.json).
    for item in DATA.glob("_*"):
        if item.is_file() and item.name not in NEVER_TOUCH_DATA:
            dest = _unique(ARCHIVE / "scratch" / "data_probes" / item.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(item), str(dest))
                moved.append("data/" + item.name)
            except OSError:
                pass
    return moved


def prune_archive() -> list[str]:
    """Only inside _archive: drop entries untouched for UPKEEP_BACKUP_DAYS."""
    if BACKUP_DAYS <= 0:
        return []
    cutoff = time.time() - BACKUP_DAYS * 86400
    removed = []
    for bucket in ("backups", "scratch"):
        base = ARCHIVE / bucket
        if not base.is_dir():
            continue
        for item in base.iterdir():
            try:
                newest = max([item.stat().st_mtime] + [p.stat().st_mtime for p in item.rglob("*")] if item.is_dir()
                             else [item.stat().st_mtime])
                if newest >= cutoff:
                    continue
                shutil.rmtree(item) if item.is_dir() else item.unlink()
                removed.append(f"{bucket}/{item.name}")
            except OSError:
                continue
    return removed


def rotate_logs() -> dict:
    """Roll oversized data/*.log. A log the running desk holds open is left alone."""
    out = {"rotated": [], "in_use": []}
    for path in DATA.glob("*.log"):
        try:
            if path.stat().st_size < LOG_MAX_MB * 1_000_000:
                continue
            path.replace(path.with_name(path.name + ".1"))
            out["rotated"].append(path.name)
        except PermissionError:
            out["in_use"].append(path.name)  # launcher keeps .previous on next start
        except OSError:
            continue
    return out


def clean_caches() -> int:
    count = 0
    for cache in list(ROOT.rglob("__pycache__")) + [ROOT / ".pytest_cache"]:
        if ".venv" in cache.parts or "_archive" in cache.parts or not cache.is_dir():
            continue
        shutil.rmtree(cache, ignore_errors=True)
        count += 1
    return count


def sqlite_maintenance() -> dict:
    """Checkpoint WAL and refresh planner stats; safe while the desk is running."""
    out = {}
    for path in sorted(DATA.glob("*.sqlite3")):
        row = {"mb": round(path.stat().st_size / 1e6, 1)}
        try:
            con = sqlite3.connect(str(path), timeout=5)
            try:
                row["quick_check"] = con.execute("PRAGMA quick_check").fetchone()[0]
                row["checkpoint"] = list(con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone())
                con.execute("PRAGMA optimize")
            finally:
                con.close()
        except sqlite3.Error as exc:
            row["error"] = str(exc)[:160]  # busy/locked: try again tomorrow
        out[path.name] = row
    return out


def data_usage(top: int = 8) -> list[tuple[str, float]]:
    sizes = []
    for item in DATA.iterdir():
        try:
            size = sum(p.stat().st_size for p in item.rglob("*") if p.is_file()) if item.is_dir() else item.stat().st_size
            sizes.append((item.name, round(size / 1e6, 1)))
        except OSError:
            continue
    return sorted(sizes, key=lambda x: -x[1])[:top]


def run_tests() -> dict:
    if not PYTHON.exists():
        return {"skipped": "no .venv python"}
    env = dict(os.environ)
    # Tests must never see the live data dir or live broker selection.
    for key in ("TOMAHAWK_DATA_DIR", "BROKER_PROVIDER", "IBKR_LIVE", "IBKR_ACCOUNT"):
        env.pop(key, None)
    try:
        done = subprocess.run([str(PYTHON), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", "-rf",
                               "--tb=no", "tests"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"ok": False, "summary": "test suite timed out (900s)"}
    lines = [l for l in done.stdout.splitlines() if l.strip()]
    summary = next((l for l in reversed(lines) if " passed" in l or " failed" in l), lines[-1] if lines else "")
    failed = [l[7:] for l in lines if l.startswith("FAILED ")][:20]
    return {"ok": done.returncode == 0, "summary": summary.strip("= "), "failed": failed}


TASK_MARK = "<!-- auto-upkeep -->"


def update_tasks(problems: list[str]) -> None:
    """Surface problems in the visible TASKS.md checklist; clear when healthy."""
    path = ROOT / "TASKS.md"
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else ["# Desk tasks (visible checklist)", ""]
    except OSError:
        return
    lines = [l for l in lines if TASK_MARK not in l]
    if problems:
        stamp = now().strftime("%Y-%m-%d %H:%M")
        lines.append(f"- **UPKEEP {stamp}** — " + "; ".join(problems)[:600]
                     + f" · report: `data/upkeep/last_daily.json` {TASK_MARK}")
    try:
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError:
        pass


def daily(with_tests: bool = True) -> dict:
    started = time.time()
    report = {"at": now().isoformat(), "rules": "never touches config, orders, sessions or broker"}
    steps = [("archived", archive_root_clutter), ("pruned", prune_archive), ("logs", rotate_logs),
             ("caches_removed", clean_caches), ("sqlite", sqlite_maintenance), ("data_usage_mb", data_usage)]
    for name, fn in steps:
        try:
            report[name] = fn()
        except Exception as exc:
            report[name] = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    h = health()
    report["health"] = None if h is None else {
        "ok": h.get("ok"), "broker": (h.get("broker") or {}).get("connected_label"),
        "connected": (h.get("broker") or {}).get("connected"), "corrupt_files": h.get("corrupt_files"),
        "automation": (h.get("loop") or {}).get("automation_health")}
    report["tests"] = run_tests() if with_tests else {"skipped": "--no-tests"}
    problems = []
    if h is None:
        problems.append("desk unreachable at the daily check")
    elif h.get("corrupt_files"):
        problems.append("corrupt data files: " + ", ".join(map(str, h["corrupt_files"])))
    if report["tests"].get("ok") is False:
        problems.append("tests: " + str(report["tests"].get("summary")))
    for name, row in (report.get("sqlite") or {}).items():
        if isinstance(row, dict) and row.get("quick_check") not in (None, "ok"):
            problems.append(f"{name} integrity: {row['quick_check']}")
    report["problems"] = problems
    report["seconds"] = round(time.time() - started, 1)
    _save("last_daily.json", report)
    update_tasks(problems)
    log(f"daily: archived={len(report.get('archived') or [])} pruned={len(report.get('pruned') or [])} "
        f"tests={report['tests'].get('summary', report['tests'].get('skipped'))} problems={len(problems)}")
    return report


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "status"
    if mode == "watchdog":
        watchdog()
    elif mode == "daily":
        daily(with_tests="--no-tests" not in argv)
    elif mode == "status":
        print(json.dumps({"watchdog": _load("watchdog.json").get("last"),
                          "daily": _load("last_daily.json")}, indent=2, default=str))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
