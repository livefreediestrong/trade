# Compatibility proposal: ask the running desk to check Gateway on a schedule.
# Does NOT disable 2FA. Prefer Gateway UI Auto restart when possible.
# Run elevated if Task Scheduler requires it. Review before registering.
param(
  [string]$TaskName = 'DaytradeEnsureIBGateway',
  [string]$Time = '08:30',  # local time you are usually around
  [switch]$Register
)
$Root = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $PSScriptRoot 'Ensure-IBGateway.ps1'
Write-Host @"
IB Gateway Auto restart (preferred, no Task Scheduler):
  Gateway → Configure → Lock and Exit → Auto restart
  Pick a daily time you can approve IB Key if weekly reauth is due.

The helper delegates to the running desk's automatic launch policy.
Outside an active live session and its permitted hours it will not launch Gateway.
The existing five-minute watchdog already makes this check; a second task is normally unnecessary.
Soft desk reconnect (Refresh broker P&L) should be tried first while the port is up.
IBC (https://github.com/IbcAlpha/IBC) is optional later for unattended Auto-Restart — not installed by the desk.
"@
if (-not $Register) {
  Write-Host "Dry run. Re-run with -Register to create task '$TaskName' at $Time daily calling:`n  $Script"
  exit 0
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description 'Ask the running desk to apply its guarded automatic Gateway check; sign-in may be required' -Force | Out-Null
Write-Host "Registered $TaskName daily at $Time."
