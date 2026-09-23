$ErrorActionPreference = "Continue"
$h = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/health"
Write-Host ("loop_running=" + $h.loop.running + " rth_ok=" + $h.loop.rth_ok + " interval=" + $h.loop.interval_sec)
# start a tiny paper session
$body = '{"make_today_usd":25,"beginning_bank_usd":1000}'
$s = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/session/start" -Method POST -ContentType "application/json" -Body $body
Write-Host ("start_ok=" + $s.ok + " mode=" + $s.config.mode + " loop_enabled=" + $s.config.loop_enabled)
Start-Sleep -Seconds 2
$st = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/state"
Write-Host ("session_active=" + $st.config.session_active + " loop_running=" + $st.loop.running + " outside_rth=" + $st.loop.outside_rth)
try {
  $f = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/loop/feed"
  Write-Host ("feed_ok events=" + @($f.events).Count)
} catch {
  Write-Host ("feed_err=" + $_)
}
