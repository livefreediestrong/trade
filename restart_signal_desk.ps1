# Restart day-trade signal desk on port 5056 (Windows).
# Usage: powershell -NoProfile -File restart_signal_desk.ps1
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Stopping anything on port 5056..."
try {
  $conns = Get-NetTCPConnection -LocalPort 5056 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique
  foreach ($procId in $conns) {
    if ($procId -and $procId -ne 0) {
      Write-Host "  Killing PID $procId"
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    }
  }
} catch {}

Start-Sleep -Seconds 1

if (Test-Path ".\run.bat") {
  Write-Host "Starting via run.bat..."
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "run.bat" -WorkingDirectory $Root -WindowStyle Minimized
} elseif (Test-Path ".\Launch.bat") {
  Write-Host "Starting via Launch.bat..."
  Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "Launch.bat" -WorkingDirectory $Root -WindowStyle Minimized
} else {
  Write-Host "No run.bat/Launch.bat — starting python app.py"
  Start-Process -FilePath "python" -ArgumentList "app.py" -WorkingDirectory $Root -WindowStyle Minimized
}

Start-Sleep -Seconds 3
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:5056/api/health" -UseBasicParsing -TimeoutSec 5
  Write-Host "Health: $($r.Content)"
} catch {
  Write-Host "Health check not ready yet: $_"
}
Write-Host "Done. Open http://127.0.0.1:5056"
