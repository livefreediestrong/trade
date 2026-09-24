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
Write-Host "Port down — launching $Exe (full logout still needs human IB Key / 2FA)."
Start-Process -FilePath $Exe -WorkingDirectory (Split-Path -Parent $Exe) -WindowStyle Normal | Out-Null
exit 0
