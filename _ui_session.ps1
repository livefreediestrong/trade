$ErrorActionPreference = "Stop"
# Paper START then STOP with correct body keys
$startBody = @{ make_today_usd = 50; beginning_bank_usd = 1000 } | ConvertTo-Json
$st = Invoke-RestMethod -Uri http://127.0.0.1:5056/api/session/start -Method POST -Body $startBody -ContentType "application/json"
Write-Output ("session_start_ok=" + $st.ok)
Write-Output ("loop_running=" + $st.loop.running + " loop_enabled=" + $st.loop.loop_enabled)
Write-Output ("session_active=" + $st.config.session_active + " mode=" + $st.config.mode)
if ($st.signal) { Write-Output ("first_signal=" + $st.signal.ticker + " " + $st.signal.side + " " + $st.signal.verdict) }
Start-Sleep -Seconds 3
$s2 = Invoke-RestMethod http://127.0.0.1:5056/api/state
Write-Output ("state_running=" + $s2.loop.running + " session_active=" + $s2.config.session_active + " last_skip=" + $s2.loop.last_skip)
$feed = Invoke-RestMethod "http://127.0.0.1:5056/api/loop/feed"
$ev = $feed.events; if (-not $ev) { $ev = $feed.items }; if (-not $ev) { $ev = $feed.feed }
Write-Output ("feed_count=" + @($ev).Count + " feed_keys=" + (($feed.PSObject.Properties.Name) -join ","))
if (@($ev).Count -gt 0) {
  $ev | Select-Object -First 3 | ForEach-Object { Write-Output ("feed " + $_.event + " " + $_.ticker + " " + $_.decision + $_.action) }
}
$stop = Invoke-RestMethod -Uri http://127.0.0.1:5056/api/session/stop -Method POST -Body "{}" -ContentType "application/json"
Write-Output ("session_stop_ok=" + $stop.ok)
$s3 = Invoke-RestMethod http://127.0.0.1:5056/api/state
Write-Output ("after_stop running=" + $s3.loop.running + " session_active=" + $s3.config.session_active + " mode=" + $s3.config.mode)

# Edge screenshot
$shot = "C:\Users\maher\Projects\daytrade-signal-desk\_ui_shot.png"
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) { $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) {
  Start-Process -FilePath $edge -ArgumentList @("--headless","--disable-gpu","--window-size=1280,800","--screenshot=$shot","http://127.0.0.1:5056/") -Wait -NoNewWindow
  if (Test-Path $shot) { Write-Output ("screenshot_ok bytes=" + (Get-Item $shot).Length) } else { Write-Output "screenshot_missing" }
} else { Write-Output "edge_missing" }
