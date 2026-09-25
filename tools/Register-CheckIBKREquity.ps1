# Register Tomahawk-Check-IBKR-Equity: mornings Oct 1-3 2026 (local).
# Prefer: powershell -NoProfile -ExecutionPolicy Bypass -File tools\Register-CheckIBKREquity.ps1 -Register
# If Access Denied, run elevated (Admin PowerShell) or use Manual fallback below.
param(
  [string]$TaskName = 'Tomahawk-Check-IBKR-Equity',
  [string]$Time = '09:00',
  [switch]$Register
)
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'Notify-CheckIBKREquity.ps1'
$Dates = @('2026-10-01', '2026-10-02', '2026-10-03')
Write-Host @"
CHECK reminder for IBKR equity / market-data resubscribe.
  Task:    $TaskName
  Times:   $Time local on $($Dates -join ', ')
  Action:  powershell -NoProfile -ExecutionPolicy Bypass -File "$Script"
  Flag:    $Root\data\tasks\DUE-check-ibkr-equity.txt
  Details: $Root\data\tasks\check-ibkr-equity-market-data.md

Manual fallback (elevated cmd/PowerShell) if Register-ScheduledTask / schtasks fails:
  schtasks /Create /TN "$TaskName" /SC ONCE /SD 10/01/2026 /ST $Time /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`"" /F
  Reminder: also create ONCE tasks for 10/02/2026 and 10/03/2026, or a daily task with /SD 10/01/2026 /ED 10/03/2026:
  schtasks /Create /TN "$TaskName" /SC DAILY /SD 10/01/2026 /ED 10/03/2026 /ST $Time /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`"" /F
  Verify: schtasks /Query /TN "$TaskName"
  Delete: schtasks /Delete /TN "$TaskName" /F
"@
if (-not $Register) {
  Write-Host "Dry run. Re-run with -Register to create the task."
  exit 0
}
if (-not (Test-Path $Script)) {
  Write-Error "Missing notify script: $Script"
  exit 1
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""
$triggers = foreach ($d in $Dates) {
  New-ScheduledTaskTrigger -Once -At ([datetime]::ParseExact("$d $Time", 'yyyy-MM-dd HH:mm', $null))
}
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
try {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description 'CHECK: after $500 ACH lands, verify IBKR U28575784 NLV>=500 and resubscribe Snapshot + Equity/Options Add-On' -Force | Out-Null
  Write-Host "Registered $TaskName for $($Dates -join ', ') at $Time."
  Write-Host "Verify: schtasks /Query /TN $TaskName"
} catch {
  Write-Warning "Register-ScheduledTask failed: $($_.Exception.Message)"
  Write-Host "Trying schtasks /Create (DAILY 10/01-10/03)..."
  $tr = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$Script`""
  $create = schtasks /Create /TN $TaskName /SC DAILY /SD 10/01/2026 /ED 10/03/2026 /ST $Time /TR $tr /F
  if ($LASTEXITCODE -ne 0) {
    Write-Error "schtasks also failed (exit $LASTEXITCODE). Run this script elevated, or use Manual fallback above."
    exit 1
  }
  Write-Host ($create | Out-String)
  Write-Host "Registered via schtasks. Verify: schtasks /Query /TN $TaskName"
}
