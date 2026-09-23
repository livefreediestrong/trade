# Desktop launcher: start the desk if it isn't running, wait for health, open the browser.
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Url = "http://127.0.0.1:5056/"

function Test-Desk {
  try {
    $r = Invoke-WebRequest -Uri "${Url}api/health" -UseBasicParsing -TimeoutSec 3
    return $r.StatusCode -eq 200
  } catch { return $false }
}

Add-Type -AssemblyName PresentationFramework

if (-not (Test-Desk)) {
  $py = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path $py)) { $py = Join-Path $Root "venv\Scripts\python.exe" }
  if (-not (Test-Path $py)) {
    [System.Windows.MessageBox]::Show("No Python venv found in $Root.`nRun force_restart.ps1 once to set it up.", "Tomahawk") | Out-Null
    exit 1
  }
  # Minimized console = the desk server. Close that window to stop the desk.
  Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $Root -WindowStyle Minimized
  $deadline = (Get-Date).AddSeconds(45)
  while (-not (Test-Desk)) {
    if ((Get-Date) -gt $deadline) {
      [System.Windows.MessageBox]::Show("Tomahawk didn't start within 45 seconds.`nCheck the minimized Python window for errors.", "Tomahawk") | Out-Null
      exit 1
    }
    Start-Sleep -Milliseconds 700
  }
}

Start-Process $Url
