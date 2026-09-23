$ErrorActionPreference = "Continue"
$Root = "C:\Users\maher\Projects\daytrade-signal-desk"
Set-Location $Root
Write-Host "Force restart signal desk..."
try {
  Get-NetTCPConnection -LocalPort 5056 -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique |
    ForEach-Object {
      if ($_ -and $_ -ne 0) {
        Write-Host "Killing PID $_"
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
      }
    }
} catch {}
Start-Sleep -Seconds 2
$py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = Join-Path $Root "venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { Write-Host "ERROR: no venv python"; exit 1 }
Write-Host "pip install..."
& $py -m pip install -q -r (Join-Path $Root "requirements.txt")
Write-Host "Starting $py app.py"
Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $Root -WindowStyle Minimized
Start-Sleep -Seconds 4
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:5056/api/health" -UseBasicParsing -TimeoutSec 10
  Write-Host "Health: $($r.Content)"
} catch {
  Write-Host "Health failed: $_"
  exit 1
}
