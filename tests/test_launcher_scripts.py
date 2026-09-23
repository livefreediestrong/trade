"""Static validation for the Windows desktop launcher/restart scripts.

These are PowerShell scripts (not importable Python), so we validate them by
parsing with PowerShell's own language parser (syntax only, never executed)
and by asserting a few hard safety invariants via source inspection:
  - no hardcoded absolute machine-specific paths
  - no "kill everything listening on the port" patterns
  - the shared identity-check library is used by every restart/launch script
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")

LAUNCHER_SCRIPTS = [
    "desk_launcher_lib.ps1",
    "restart_signal_desk.ps1",
    "install_and_restart.ps1",
    "force_restart.ps1",
    "Start-Tomahawk.ps1",
]


@pytest.mark.skipif(POWERSHELL is None, reason="no powershell/pwsh on PATH")
@pytest.mark.parametrize("name", LAUNCHER_SCRIPTS)
def test_launcher_script_parses_without_errors(name: str):
    path = ROOT / name
    assert path.exists(), f"missing launcher script: {name}"
    cmd = (
        "$errors = $null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$errors); "
        "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Output $_.ToString() } }"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", cmd],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0 and not result.stdout.strip(), (
        f"{name} failed to parse: stdout={result.stdout!r} stderr={result.stderr!r}"
    )


@pytest.mark.parametrize("name", ["restart_signal_desk.ps1", "force_restart.ps1"])
def test_restart_scripts_use_shared_identity_check(name: str):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "desk_launcher_lib.ps1" in text
    assert "Stop-DeskProcessSafely" in text
    # Regression guard: must not blindly kill every PID on the port anymore.
    assert "Get-NetTCPConnection" not in text


def test_force_restart_has_no_hardcoded_repo_path():
    text = (ROOT / "force_restart.ps1").read_text(encoding="utf-8")
    assert "$Root = Split-Path -Parent $MyInvocation.MyCommand.Path" in text
    assert "C:\\Users\\" not in text


def test_launcher_lib_verifies_identity_before_stopping():
    text = (ROOT / "desk_launcher_lib.ps1").read_text(encoding="utf-8")
    assert "Test-ProcessIsThisDesk" in text
    assert "app.py" in text
    # Fail-closed: mismatched identity must be refused, not force-killed.
    assert "Refusing to stop" in text


def test_restart_script_uses_relative_prefixed_batch_paths():
    """Regression guard: bare `run.bat`/`Launch.bat` silently fail to resolve
    on machines with the NoDefaultCurrentDirectoryInExePath security setting
    enabled (cmd.exe then refuses to search the cwd for a bare command name).
    The Start-Process -ArgumentList must always pass an explicit ".\\"
    (or absolute) path instead of a bare name."""
    text = (ROOT / "restart_signal_desk.ps1").read_text(encoding="utf-8")
    arg_lines = [ln for ln in text.splitlines() if "-ArgumentList" in ln]
    assert arg_lines, "expected at least one Start-Process -ArgumentList line"
    for ln in arg_lines:
        assert '"run.bat"' not in ln and '"Launch.bat"' not in ln
    assert '".\\run.bat"' in text
    assert '".\\Launch.bat"' in text


def test_start_tomahawk_guards_against_duplicate_instances():
    text = (ROOT / "Start-Tomahawk.ps1").read_text(encoding="utf-8")
    assert "Get-VerifiedDeskProcess" in text
