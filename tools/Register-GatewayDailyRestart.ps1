# PROPOSAL: register a daily Task Scheduler job that relaunches IB Gateway.
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
  Gateway → Configure → Lock and Exit → Never lock + Auto restart
  Pick a daily time you can approve IB Key if weekly reauth is due.

This script only relaunches ibgateway.exe when the API port is down.
Soft desk reconnect (Refresh broker P&L) should be tried first while the port is up.
IBC (https://github.com/IbcAlpha/IBC) is optional later for unattended Auto-Restart — not installed by the desk.
"@
if (-not $Register) {
  Write-Host "Dry run. Re-run with -Register to create task '$TaskName' at $Time daily calling:`n  $Script"
  exit 0
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Description 'Relaunch IB Gateway if API port down (2FA may still be required)' -Force | Out-Null
Write-Host "Registered $TaskName daily at $Time."
