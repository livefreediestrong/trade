$ErrorActionPreference = "Continue"
out = @()
function Log($m) { $script:out += $m; Write-Output $m }

# 1) Page loads
try {
  $html = Invoke-WebRequest -Uri http://127.0.0.1:5056/ -UseBasicParsing -TimeoutSec 15
  $c = $html.Content
  Log ("page_status=" + $html.StatusCode)
  Log ("has_simple=" + ($c -match "ui-mode|Simple|data-mode=.simple"))
  Log ("has_advanced=" + ($c -match "Advanced|data-mode=.advanced"))
  Log ("has_buzz_pill=" + ($c -match "buzz-simple-pill"))
  Log ("has_buzz_panel=" + ($c -match "Ticker buzz|buzz-panel|id=.buzz"))
  Log ("has_paper=" + ($c -match "PAPER ONLY|paper"))
  Log ("has_start=" + ($c -match "START|session/start|id=.btn-start"))
} catch { Log ("page_err=" + $_.Exception.Message) }

# 2) State for Simple buzz summary
$s = Invoke-RestMethod http://127.0.0.1:5056/api/state
Log ("state_ok loop_running=" + $s.loop.running + " session_active=" + $s.session_active)
if ($s.buzz) {
  Log ("state_buzz_keys=" + (($s.buzz.PSObject.Properties.Name) -join ","))
  if ($s.buzz.watchlist_hits) { Log ("watchlist_hits=" + ($s.buzz.watchlist_hits -join ",")) }
  if ($s.buzz.top) { Log ("buzz_top_n=" + @($s.buzz.top).Count) }
  if ($s.buzz.simple_pill) { Log ("simple_pill=" + $s.buzz.simple_pill) }
  if ($s.buzz.pill) { Log ("pill=" + $s.buzz.pill) }
}

# 3) Force buzz refresh (Advanced refresh button)
$b = Invoke-RestMethod "http://127.0.0.1:5056/api/buzz?force=1"
Log ("buzz_force auth_mode=" + $b.auth_mode + " tickers=" + @($b.tickers).Count + " threads=" + @($b.threads).Count)
$wl = @()
if ($s.watchlist) { $wl = @($s.watchlist | ForEach-Object { if ($_.ticker) { $_.ticker } elseif ($_ -is [string]) { $_ } else { $_.symbol } }) }
# try common shapes
if ($wl.Count -eq 0 -and $s.watchlist_tickers) { $wl = @($s.watchlist_tickers) }
$hits = @()
foreach ($t in @($b.tickers)) {
  if ($wl -contains $t.ticker) { $hits += $t.ticker }
}
Log ("simple_pill_candidates=" + ($hits -join ","))
if (@($b.tickers).Count -gt 0) {
  $top = $b.tickers | Select-Object -First 3
  foreach ($t in $top) { Log ("adv_row " + $t.ticker + " n=" + $t.mentions) }
}

# 4) Live chat paste bridge (Advanced)
$body = @{ text = "WSB live chat export test `$NVDA going crazy also TSLA calls" } | ConvertTo-Json
try {
  $lc = Invoke-RestMethod -Uri http://127.0.0.1:5056/api/buzz/live-chat -Method POST -Body $body -ContentType "application/json"
  Log ("live_chat_ok=" + $lc.ok)
  if ($lc.live_chat) { Log ("live_chat_tickers=" + (($lc.live_chat.tickers | ForEach-Object { $_ }) -join ",")) }
  if ($lc.buzz -and $lc.buzz.tickers) {
    $nv = $lc.buzz.tickers | Where-Object { $_.ticker -eq "NVDA" -or ($_.sources -contains "wsb_live_chat") } | Select-Object -First 3
    foreach ($x in @($nv)) { Log ("after_paste " + $x.ticker + " src=" + (($x.sources | ForEach-Object { $_ }) -join ",")) }
  }
} catch { Log ("live_chat_err=" + $_.Exception.Message) }

# 5) Paper session start then stop (dry)
$startBody = @{ daily_target_usd = 50; beginning_bank_usd = 1000 } | ConvertTo-Json
try {
  $st = Invoke-RestMethod -Uri http://127.0.0.1:5056/api/session/start -Method POST -Body $startBody -ContentType "application/json"
  Log ("session_start_ok=" + $st.ok)
  if ($st.error) { Log ("session_start_error=" + $st.error) }
  if ($st.loop) { Log ("after_start running=" + $st.loop.running + " enabled=" + $st.loop.loop_enabled) }
  Start-Sleep -Seconds 2
  $s2 = Invoke-RestMethod http://127.0.0.1:5056/api/state
  Log ("after_start_state running=" + $s2.loop.running + " session_active=" + $s2.session_active + " last_skip=" + $s2.loop.last_skip)
  $feed = Invoke-RestMethod http://127.0.0.1:5056/api/loop/feed
  Log ("feed_rows=" + @($feed.events).Count)
  if (-not $feed.events) { Log ("feed_keys=" + (($feed.PSObject.Properties.Name) -join ",")) }
  $stop = Invoke-RestMethod -Uri http://127.0.0.1:5056/api/session/stop -Method POST -Body "{}" -ContentType "application/json"
  Log ("session_stop_ok=" + $stop.ok)
  $s3 = Invoke-RestMethod http://127.0.0.1:5056/api/state
  Log ("after_stop running=" + $s3.loop.running + " session_active=" + $s3.session_active)
} catch {
  Log ("session_err=" + $_.Exception.Message)
  try { Invoke-RestMethod -Uri http://127.0.0.1:5056/api/session/stop -Method POST -Body "{}" -ContentType "application/json" | Out-Null } catch {}
}

# 6) Headless screenshot if Edge available
$shot = "C:\Users\maher\Projects\daytrade-signal-desk\_ui_shot.png"
$edge = ${(Get-Command msedge -ErrorAction SilentlyContinue).Source}; if (-not $edge) { $edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" }
if (Test-Path $edge) {
  & $edge --headless --disable-gpu --window-size=1280,800 --screenshot=$shot "http://127.0.0.1:5056/" 2>$null
  if (Test-Path $shot) { Log ("screenshot_ok=" + $shot + " bytes=" + (Get-Item $shot).Length) } else { Log "screenshot_missing" }
} else { Log "edge_not_found" }
