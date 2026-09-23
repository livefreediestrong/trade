# Force-restart the signal desk (Windows) - reinstalls deps then restarts.
# Usage: powershell -NoProfile -File force_restart.ps1
#
# Resolves the repo root from this script's own location (never a hardcoded
# absolute path) and only stops a process it can positively verify as this
# repo's app.py - it will not kill unrelated services on the port.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
. (Join-Path $Root "desk_launcher_lib.ps1")

Write-Host "Force restart signal desk (repo root: $Root)..."
$stop = Stop-DeskProcessSafely -Root $Root
Write-Host $stop.Message
if (-not $stop.Stopped) {
  Write-Host "Refusing to start a new instance while an unverified process holds the lock. Resolve manually, then re-run."
  exit 1
}

$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $Root "venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { Write-Host "ERROR: no venv python found under $Root"; exit 1 }

Write-Host "pip install..."
& $py -m pip install -q -r (Join-Path $Root "requirements.txt")

$start = Start-DeskProcess -Root $Root -PythonExe $py
Write-Host $start.Message
if (-not $start.Started -and -not $start.LogPath) {
  exit 1
}

$health = Wait-DeskHealthy -Root $Root -TimeoutSec 30
if ($health.Healthy) {
  Write-Host "Health: $($health.Body)"
} else {
  Write-Warning $health.Message
  Write-Host "Check the newest file under $(Get-DeskLogDir -Root $Root) for startup errors."
  exit 1
}
