# Restart day-trade signal desk (Windows).
# Usage: powershell -NoProfile -File restart_signal_desk.ps1
#
# Process-safe: only stops the desk process it can positively verify (by PID
# file + command-line identity), never everything listening on the port.
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
. (Join-Path $Root "desk_launcher_lib.ps1")

Write-Host "Repo root: $Root"
$stop = Stop-DeskProcessSafely -Root $Root
Write-Host $stop.Message
if (-not $stop.Stopped) {
    Write-Host "Not starting a new instance until the conflicting process is resolved manually."
    exit 1
}

$logDir = Get-DeskLogDir -Root $Root
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
if (Test-Path (Join-Path $Root "run.bat")) {
    $logPath = Join-Path $logDir "desk_${stamp}_runbat.log"
    Write-Host "Starting via run.bat (logging to $logPath)..."
    # NOTE: use ".\run.bat", never a bare "run.bat" - on machines with the
    # NoDefaultCurrentDirectoryInExePath security setting enabled, cmd.exe
    # will NOT search the current directory for a bare command name and
    # silently fails with "'run.bat' is not recognized...".
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ".\run.bat" -WorkingDirectory $Root -WindowStyle Minimized `
        -RedirectStandardOutput $logPath -RedirectStandardError (Join-Path $logDir "desk_${stamp}_runbat_err.log")
} elseif (Test-Path (Join-Path $Root "Launch.bat")) {
    $logPath = Join-Path $logDir "desk_${stamp}_launchbat.log"
    Write-Host "Starting via Launch.bat (logging to $logPath)..."
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c", ".\Launch.bat" -WorkingDirectory $Root -WindowStyle Minimized `
        -RedirectStandardOutput $logPath -RedirectStandardError (Join-Path $logDir "desk_${stamp}_launchbat_err.log")
} else {
    $start = Start-DeskProcess -Root $Root
    Write-Host $start.Message
    if (-not $start.Started -and -not $start.LogPath) {
        exit 1
    }
}

$health = Wait-DeskHealthy -Root $Root -TimeoutSec 45
if ($health.Healthy) {
    Write-Host "Health: $($health.Body)"
    Write-Host "Done. Open $(Get-DeskHealthUrl -Root $Root)"
} else {
    Write-Warning $health.Message
    Write-Host "Check the newest file under $(Get-DeskLogDir -Root $Root) for startup errors."
    exit 1
}
