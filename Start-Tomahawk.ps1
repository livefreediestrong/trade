# Desktop launcher: start the desk if it isn't already running, wait for a
# fingerprinted health check, then open the browser. Safe to use as a desktop
# shortcut target - set the shortcut's "Target" to:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "<repo>\Start-Tomahawk.ps1"
# and "Start in" to the repo root so relative paths resolve correctly even if
# the shortcut is copied elsewhere.
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
Set-Location $Root
. (Join-Path $Root "desk_launcher_lib.ps1")

Add-Type -AssemblyName PresentationFramework

$existing = Get-VerifiedDeskProcess -Root $Root
if (-not $existing.Found) {
    $start = Start-DeskProcess -Root $Root
    if (-not $start.Started -and -not $start.LogPath) {
        [System.Windows.MessageBox]::Show($start.Message, "Tomahawk") | Out-Null
        exit 1
    }
    if (-not $start.Started) {
        # Already running (duplicate-instance guard) - fall through to health wait.
        Write-Host $start.Message
    }
    $health = Wait-DeskHealthy -Root $Root -TimeoutSec 45
    if (-not $health.Healthy) {
        $logDir = Get-DeskLogDir -Root $Root
        [System.Windows.MessageBox]::Show(
            "Tomahawk didn't start correctly.`n$($health.Message)`nCheck the newest log in $logDir for errors.",
            "Tomahawk"
        ) | Out-Null
        exit 1
    }
}

Start-Process (Get-DeskHealthUrl -Root $Root).Replace("api/health", "")
