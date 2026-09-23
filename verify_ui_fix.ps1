$ErrorActionPreference = "Continue"
$sw = [System.Diagnostics.Stopwatch]::StartNew()
$code = curl.exe -s --max-time 8 -o "$env:TEMP\desk_state.json" -w "%{http_code}" http://127.0.0.1:5056/api/state
$sw.Stop()
Write-Host ("STATE http=" + $code + " sec=" + [math]::Round($sw.Elapsed.TotalSeconds, 3))
if ($code -ne "200") { exit 1 }
$j = Get-Content "$env:TEMP\desk_state.json" -Raw | ConvertFrom-Json
Write-Host ("watchlist=" + @($j.config.watchlist).Count)
Write-Host ("mode=" + $j.config.mode + " session=" + $j.config.session_active)
Write-Host ("outside_rth=" + $j.loop.outside_rth + " rth_ok=" + $j.loop.rth_ok + " running=" + $j.loop.running)
$ld = $j.loop.last_decision
if ($ld) { Write-Host ("last=" + $ld.ticker + " " + $ld.decision + " late=" + $ld.late + " err=" + $ld.error) }
$code2 = curl.exe -s --max-time 5 -o "$env:TEMP\desk_feed.json" -w "%{http_code}" "http://127.0.0.1:5056/api/loop/feed?limit=5"
Write-Host ("FEED http=" + $code2)
$f = Get-Content "$env:TEMP\desk_feed.json" -Raw | ConvertFrom-Json
foreach ($e in @($f.events | Select-Object -First 5)) { Write-Host ("row " + $e.seq + " " + $e.ticker + " " + $e.decision + " late=" + $e.late + " data_error=" + $e.data_error) }
Write-Host ("js_err=" + [bool](Select-String -Path "C:\Users\maher\Projects\daytrade-signal-desk\static\app.js" -Pattern "badge-err" -SimpleMatch))
Write-Host ("css_hidden=" + [bool](Select-String -Path "C:\Users\maher\Projects\daytrade-signal-desk\static\app.css" -Pattern "display: none !important" -SimpleMatch))
