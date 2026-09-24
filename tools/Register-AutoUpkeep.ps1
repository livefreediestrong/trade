# Register Tomahawk auto upkeep (two Windows scheduled tasks, current user).
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Register-AutoUpkeep.ps1 -Register
#   ... -Unregister     removes both tasks
# Watchdog: every 5 min - relaunches the desk only if unreachable, or runs the
#           normal launcher (Gateway check) if the broker socket is down.
# Daily:    16:40 local - archive root clutter, prune old _archive items,
#           roll oversized logs, SQLite checkpoint/quick_check, run tests,
#           write data\upkeep\last_daily.json, flag problems in TASKS.md.
# Neither task edits config, orders, sessions or broker settings.
param(
  [string]$DailyTime = '16:40',
  [int]$WatchdogMinutes = 5,
  [switch]$Register,
  [switch]$Unregister
)
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'desk_upkeep.py'
$Pyw = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Pyw)) { $Pyw = Join-Path $Root '.venv\Scripts\python.exe' }
$Names = @{ Watchdog = 'Tomahawk-Desk-Watchdog'; Daily = 'Tomahawk-Desk-Upkeep' }

if ($Unregister) {
  foreach ($n in $Names.Values) { Unregister-ScheduledTask -TaskName $n -Confirm:$false -ErrorAction SilentlyContinue }
  Write-Host "Removed $($Names.Values -join ', ')."
  exit 0
}
Write-Host "Watchdog: $($Names.Watchdog) every $WatchdogMinutes min -> $Pyw `"$Script`" watchdog"
Write-Host "Daily:    $($Names.Daily) at $DailyTime -> $Pyw `"$Script`" daily"
if (-not $Register) { Write-Host 'Dry run. Re-run with -Register.'; exit 0 }
if (-not (Test-Path $Script)) { Write-Error "Missing $Script"; exit 1 }

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$wdAction = New-ScheduledTaskAction -Execute $Pyw -Argument "`"$Script`" watchdog" -WorkingDirectory $Root
$wdTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $WatchdogMinutes)
$dAction = New-ScheduledTaskAction -Execute $Pyw -Argument "`"$Script`" daily" -WorkingDirectory $Root
$dTrigger = New-ScheduledTaskTrigger -Daily -At $DailyTime
try {
  Register-ScheduledTask -TaskName $Names.Watchdog -Action $wdAction -Trigger $wdTrigger -Settings $settings `
    -Description 'Tomahawk desk watchdog: relaunch if unreachable; never touches trading state' -Force | Out-Null
  Register-ScheduledTask -TaskName $Names.Daily -Action $dAction -Trigger $dTrigger -Settings $settings `
    -Description 'Tomahawk desk daily upkeep: archive, prune, logs, SQLite, tests, report' -Force | Out-Null
  Write-Host "Registered $($Names.Watchdog) and $($Names.Daily)."
} catch {
  Write-Warning "Register-ScheduledTask failed: $($_.Exception.Message) - trying schtasks"
  schtasks /Create /TN $Names.Watchdog /SC MINUTE /MO $WatchdogMinutes /TR "`"$Pyw`" `"$Script`" watchdog" /F | Out-Null
  schtasks /Create /TN $Names.Daily /SC DAILY /ST $DailyTime /TR "`"$Pyw`" `"$Script`" daily" /F | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Error 'schtasks failed; run this script from an elevated PowerShell.'; exit 1 }
  Write-Host 'Registered via schtasks.'
}
Write-Host "Status: $Pyw `"$Script`" status"
