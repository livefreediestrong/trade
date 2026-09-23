# Desk smoke test — no conda. powershell -NoProfile -File this script
$ErrorActionPreference = "Continue"
$Base = "http://127.0.0.1:5056"
$Fail = 0
$HFile = Join-Path $env:TEMP "desk_smoke_health.json"
$SFile = Join-Path $env:TEMP "desk_smoke_state.json"

Write-Host "=== smoke_desk (Tomahawk) ===
# health + state only; peer UI/Simple-Advanced not asserted here"

$hCode = curl.exe -s -m 8 -o $HFile -w "%{http_code}" "$Base/api/health"
if ($hCode -ne "200") {
  Write-Host "FAIL health: HTTP $hCode"
  $Fail = 1
} else {
  try {
    $h = Get-Content $HFile -Raw | ConvertFrom-Json
    if (-not $h.ok) { throw "ok != true" }
    Write-Host "health: OK (200, ok=true)"
    Write-Host ("llm.configured=" + $h.llm.configured)
  } catch {
    Write-Host "FAIL health parse: $_"
    $Fail = 1
  }
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$sCode = curl.exe -s -m 5 -o $SFile -w "%{http_code}" "$Base/api/state"
$sw.Stop()
$stateMs = [int]$sw.ElapsedMilliseconds
if ($sCode -ne "200") {
  Write-Host "FAIL state: HTTP $sCode"
  $Fail = 1
} else {
  try {
    $st = Get-Content $SFile -Raw | ConvertFrom-Json
    Write-Host ("state: OK (200, {0} ms)" -f $stateMs)
    Write-Host ("watchlist_count={0}" -f @($st.config.watchlist).Count)
    Write-Host ("watchlist_focus={0}" -f $st.config.watchlist_focus)
    Write-Host ("loop.running={0}" -f $st.loop.running)
    Write-Host ("llm.configured={0}" -f $st.llm.configured)
    Write-Host ("session_active={0}" -f $st.config.session_active)
    if ($stateMs -gt 3000) {
      Write-Host "FAIL state slower than 3s"
      $Fail = 1
    } elseif ($stateMs -gt 1000) {
      Write-Host ("WARN state {0} ms (ideal under 1s)" -f $stateMs)
    }
  } catch {
    Write-Host "FAIL state parse: $_"
    $Fail = 1
  }
}

if ($Fail -ne 0) {
  Write-Host "=== smoke_desk FAILED ==="
  exit 1
}
Write-Host "=== smoke_desk PASSED ==="
exit 0
