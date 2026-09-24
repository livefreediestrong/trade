# Requires: desk checkout. Never disables 2FA. Soft path preferred when port is up.
# Unattended (Task Scheduler): follows the same launch rules as broker_ibkr.ensure_gateway —
# never while any Gateway/TWS process or window exists or when that cannot be checked, never
# reopen a Gateway closed before it signed in, one launch at a time, at most 3 launches in
# 30 minutes and 2 unattended launches a day. IB_GATEWAY_AUTOLAUNCH=0 turns it off.
param(
  [string]$HostName = $(if ($env:IB_GATEWAY_HOST) { $env:IB_GATEWAY_HOST } else { '127.0.0.1' }),
  [int]$Port = $(if ($env:IB_GATEWAY_PORT) { [int]$env:IB_GATEWAY_PORT } else { 4002 }),
  [string]$Exe = $env:IB_GATEWAY_EXE
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
function Get-EnvSetting([string]$Name) {
  $value = [Environment]::GetEnvironmentVariable($Name)
  if ($value) { return $value }
  $envFile = Join-Path $root '.env'
  if (Test-Path -LiteralPath $envFile) {
    $line = Get-Content -LiteralPath $envFile | Where-Object { $_ -match "^\s*$Name\s*=" } | Select-Object -Last 1
    if ($line) { return (($line -split '=', 2)[1]).Trim().Trim('"').Trim("'") }
  }
  return $null
}
function Test-Port([string]$Server, [int]$P) {
  $socket = New-Object Net.Sockets.TcpClient
  try {
    $c = $socket.BeginConnect($Server, $P, $null, $null)
    if (-not $c.AsyncWaitHandle.WaitOne(600)) { return $false }
    $socket.EndConnect($c)
    return $socket.Connected
  } catch { return $false } finally { $socket.Dispose() }
}
function Get-GatewayCheck {
  # ok = $false when Windows could not be asked: never read a failed check as "not running".
  try { $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop) } catch { return @{ ok = $false; running = @() } }
  $running = @($processes | Where-Object {
    $_.Name -in @('ibgateway.exe', 'tws.exe') -or "$($_.Name)" -match '(?i)^(ib(kr)?gateway|tws)[\w.-]*\.exe$' -or
    ($_.Name -in @('java.exe', 'javaw.exe') -and "$($_.CommandLine)" -match '(?i)ibgateway|ibcalpha|\\jts\\|/jts/|jclient|twslaunch')
  })
  # Login windows are titled "IBKR Gateway" (10.51+), "IB Gateway" or "Trader Workstation".
  $running += @(Get-Process -ErrorAction SilentlyContinue | Where-Object { "$($_.MainWindowTitle)" -match '(?i)\bIB(KR)?\s*Gateway\b|Trader Workstation' })
  return @{ ok = $true; running = $running }
}
function Stop-Here([string]$Message) { Write-Host $Message; exit 0 }

if ((Get-EnvSetting 'IB_GATEWAY_AUTOLAUNCH') -match '^(?i)(0|false|no|off)$') {
  Stop-Here 'Desk Gateway launching is off (IB_GATEWAY_AUTOLAUNCH=0).'
}
if (Test-Port $HostName $Port) {
  Stop-Here "Gateway port ${HostName}:$Port is open. Prefer desk soft Refresh; 2FA not needed for API bounce."
}
if (-not $Exe) {
  $candidates = @()
  foreach ($base in @('C:\Jts\ibgateway', (Join-Path $env:USERPROFILE 'Jts\ibgateway'))) {
    if (Test-Path $base) {
      $candidates += Get-ChildItem $base -Directory | ForEach-Object {
        $p = Join-Path $_.FullName 'ibgateway.exe'
        if (Test-Path $p) { Get-Item $p }
      }
    }
  }
  $Exe = ($candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName)
}
if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) {
  Write-Host "ibgateway.exe not found. Set IB_GATEWAY_EXE."
  exit 2
}
$check = Get-GatewayCheck
if (-not $check.ok) { Stop-Here 'Could not list running programs; not starting IB Gateway (it may already be open).' }
if (@($check.running).Count -gt 0) {
  Stop-Here "$(@($check.running)[0].Name) is already running; complete sign-in or enable its API on port $Port. Not launching another copy."
}
$dataRoot = if ($env:TOMAHAWK_DATA_DIR) { $env:TOMAHAWK_DATA_DIR } else { Join-Path $root 'data' }
$stampPath = Join-Path $dataRoot 'gateway_launch.json'
$stamp = $null
try { $stamp = Get-Content -LiteralPath $stampPath -Raw -ErrorAction Stop | ConvertFrom-Json } catch {}
$now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
function Get-RecentCount($Values, [double]$WindowSec) {
  @(@($Values) | Where-Object { $null -ne $_ -and ($now - [double]$_) -ge 0 -and ($now - [double]$_) -lt $WindowSec }).Count
}
if ($stamp) {
  $age = $now - [double]$stamp.at
  if ($age -ge 0 -and $age -lt 180) { Stop-Here "Gateway launched ${age}s ago; waiting for sign-in." }
  # Unattended run: never reopen a Gateway that was closed before it signed in
  # (the desk records api_seen_at once Gateway serves the API).
  if ([double]$stamp.at -gt 0 -and [double]$stamp.api_seen_at -lt [double]$stamp.at) {
    Stop-Here 'IB Gateway was closed before it signed in; not reopening it automatically. Use Ensure Gateway on the desk or start it yourself when you want to sign in.'
  }
  if ((Get-RecentCount $stamp.launches 1800) -ge 3) { Stop-Here 'IB Gateway was started 3 times in 30 minutes; not starting another.' }
  if ((Get-RecentCount $stamp.automatic_launches 86400) -ge 2) { Stop-Here 'IB Gateway was reopened automatically twice today; not again.' }
}
# Gateway's own auto-restart leaves no process for a few seconds; confirm the absence first.
Start-Sleep -Seconds 90
$check = Get-GatewayCheck
if (-not $check.ok -or @($check.running).Count -gt 0 -or (Test-Port $HostName $Port)) {
  Stop-Here 'Gateway came back on its own (or could not be checked); not launching another copy.'
}
$mutex = New-Object Threading.Mutex($false, 'Local\TomahawkGatewayLaunch')
$held = $false
try {
  try { $held = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
  if (-not $held) { Stop-Here 'Another desk component is starting IB Gateway right now.' }
  try {
    $data = [ordered]@{}
    $latest = $null
    try { $latest = Get-Content -LiteralPath $stampPath -Raw -ErrorAction Stop | ConvertFrom-Json } catch {}
    if ($latest) {
      if (($now - [double]$latest.at) -ge 0 -and ($now - [double]$latest.at) -lt 180) { Stop-Here 'Gateway was just started by the desk.' }
      foreach ($property in $latest.PSObject.Properties) { $data[$property.Name] = $property.Value }
    }
    $data['launches'] = @(@($data['launches']) | Where-Object { $null -ne $_ -and ($now - [double]$_) -lt 86400 }) + @($now)
    $data['automatic_launches'] = @(@($data['automatic_launches']) | Where-Object { $null -ne $_ -and ($now - [double]$_) -lt 86400 }) + @($now)
    $data['at'] = $now; $data['exe'] = $Exe; $data['source'] = 'Ensure-IBGateway'; $data['pid'] = $null
    New-Item -ItemType Directory -Force -Path $dataRoot | Out-Null
    $data | ConvertTo-Json -Compress | Set-Content -LiteralPath $stampPath -Encoding ASCII
  } catch {}
  Write-Host "Port down — launching $Exe (full logout still needs human IB Key / 2FA)."
  Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe) -WindowStyle Normal | Out-Null
} finally {
  if ($held) { $mutex.ReleaseMutex() }
  $mutex.Dispose()
}
exit 0
