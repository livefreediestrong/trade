# Requires: desk checkout. Never disables 2FA. Soft path preferred when port is up.
param(
  [string]$HostName = $(if ($env:IB_GATEWAY_HOST) { $env:IB_GATEWAY_HOST } else { '127.0.0.1' }),
  [int]$Port = $(if ($env:IB_GATEWAY_PORT) { [int]$env:IB_GATEWAY_PORT } else { 4002 }),
  [string]$Exe = $env:IB_GATEWAY_EXE
)
$ErrorActionPreference = 'Continue'
function Test-Port([string]$Server, [int]$P) {
  $socket = New-Object Net.Sockets.TcpClient
  try {
    $c = $socket.BeginConnect($Server, $P, $null, $null)
    if (-not $c.AsyncWaitHandle.WaitOne(600)) { return $false }
    $socket.EndConnect($c)
    return $socket.Connected
  } catch { return $false } finally { $socket.Dispose() }
}
if (Test-Port $HostName $Port) {
  Write-Host "Gateway port ${HostName}:$Port is open. Prefer desk soft Refresh; 2FA not needed for API bounce."
  exit 0
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
$running = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
  $_.Name -in @('ibgateway.exe', 'tws.exe') -or
  ($_.Name -in @('java.exe', 'javaw.exe') -and "$($_.CommandLine)" -match '(?i)ibgateway|ibcalpha|\\jts\\|/jts/|jclient|twslaunch')
})
if ($running.Count -gt 0) {
  Write-Host "$($running[0].Name) is already running; complete sign-in or enable its API on port $Port. Not launching another copy."
  exit 0
}
$dataRoot = if ($env:TOMAHAWK_DATA_DIR) { $env:TOMAHAWK_DATA_DIR } else { Join-Path (Split-Path -Parent $PSScriptRoot) 'data' }
$stampPath = Join-Path $dataRoot 'gateway_launch.json'
try {
  $stamp = Get-Content -LiteralPath $stampPath -Raw -ErrorAction Stop | ConvertFrom-Json
  $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$stamp.at
  if ($age -ge 0 -and $age -lt 180) { Write-Host "Gateway launched ${age}s ago; waiting for sign-in."; exit 0 }
  # Unattended run: never reopen a Gateway that was closed before it signed in
  # (the desk records api_seen_at once Gateway serves the API).
  if ([double]$stamp.at -gt 0 -and [double]$stamp.api_seen_at -lt [double]$stamp.at) {
    Write-Host "IB Gateway was closed before it signed in; not reopening it automatically. Use Ensure Gateway on the desk or start it yourself when you want to sign in."
    exit 0
  }
} catch {}
try { @{ at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); exe = $Exe; source = 'Ensure-IBGateway' } | ConvertTo-Json -Compress | Set-Content -LiteralPath $stampPath -Encoding ASCII } catch {}
Write-Host "Port down — launching $Exe (full logout still needs human IB Key / 2FA)."
Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe) -WindowStyle Normal | Out-Null
exit 0
