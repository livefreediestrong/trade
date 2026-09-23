# Install deps + restart day-trade signal desk (Windows).
# Usage: powershell -NoProfile -File install_and_restart.ps1
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "Project root: $Root"

$py = $null
$candidates = @(
  (Join-Path $Root ".venv\Scripts\python.exe"),
  (Join-Path $Root "venv\Scripts\python.exe"),
  (Join-Path $Root ".venv\Scripts\python3.exe"),
  (Join-Path $Root "venv\Scripts\python3.exe")
)
foreach ($c in $candidates) {
  if (Test-Path $c) {
    $py = $c
    break
  }
}

if (-not $py) {
  Write-Host "No project venv found — trying py/python on PATH to create .venv"
  $bootstrap = $null
  foreach ($name in @("py", "python", "python3")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { $bootstrap = $cmd.Source; break }
  }
  if (-not $bootstrap) {
    Write-Host "ERROR: no Python found to create venv"
    exit 1
  }
  & $bootstrap -m venv (Join-Path $Root ".venv")
  $py = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) {
    Write-Host "ERROR: venv created but python.exe missing at $py"
    exit 1
  }
}

Write-Host "Using Python: $py"
Write-Host "pip install -r requirements.txt ..."
& $py -m pip install -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
  Write-Host "WARN: pip install exit code $LASTEXITCODE"
}

$restart = Join-Path $Root "restart_signal_desk.ps1"
if (Test-Path $restart) {
  Write-Host "Calling restart_signal_desk.ps1 ..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File $restart
} else {
  Write-Host "restart_signal_desk.ps1 missing — starting app.py directly"
  Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $Root -WindowStyle Minimized
  Start-Sleep -Seconds 3
}

Write-Host "Verifying health..."
try {
  $r = Invoke-WebRequest -Uri "http://127.0.0.1:5056/api/health" -UseBasicParsing -TimeoutSec 8
  Write-Host "Health: $($r.Content)"
} catch {
  Write-Host "Health check failed: $_"
}

try {
  $s = Invoke-WebRequest -Uri "http://127.0.0.1:5056/api/state" -UseBasicParsing -TimeoutSec 8
  $snippet = $s.Content
  if ($snippet.Length -gt 400) { $snippet = $snippet.Substring(0, 400) + "..." }
  Write-Host "State snippet: $snippet"
} catch {
  Write-Host "State check failed: $_"
}

Write-Host "Done. Open http://127.0.0.1:5056"
