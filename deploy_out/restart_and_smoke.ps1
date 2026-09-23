# Restart Tomahawk desk then smoke. Prefer this over inline $ mangling.
$ErrorActionPreference = "Continue"
$Root = "C:\Users\maher\Projects\daytrade-signal-desk"
Write-Host "=== force_restart ==="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "force_restart.ps1")
$rc = $LASTEXITCODE
Write-Host "force_restart exit=$rc"
Start-Sleep -Seconds 3
Write-Host "=== smoke_desk ==="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Root "smoke_desk.ps1")
$sc = $LASTEXITCODE
Write-Host "smoke_desk exit=$sc"
if ($rc -ne 0 -or $sc -ne 0) { exit 1 }
exit 0
