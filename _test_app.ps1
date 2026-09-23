$ErrorActionPreference = "Stop"
$h = Invoke-RestMethod http://127.0.0.1:5056/api/health
$s = Invoke-RestMethod http://127.0.0.1:5056/api/state
$b = Invoke-RestMethod http://127.0.0.1:5056/api/buzz
Write-Output ("health_ok=" + $h.ok + " port=" + $h.port + " banner=" + $h.banner)
Write-Output ("llm=" + $h.llm.provider + "/" + $h.llm.model + " configured=" + $h.llm.configured)
Write-Output ("state_ms_approx=via_smoke loop_running=" + $s.loop.running + " session_active=" + $s.session_active)
Write-Output ("watchlist=" + @($s.watchlist).Count + " focus=" + $s.watchlist_focus)
Write-Output ("paper_only_hint=" + ($null -ne $s.live_locked))
Write-Output ("buzz_auth_mode=" + $b.auth_mode)
if ($b.reddit_auth) { Write-Output ("reddit_configured=" + $b.reddit_auth.configured + " mode=" + $b.reddit_auth.mode) }
Write-Output ("buzz_tickers=" + @($b.tickers).Count + " threads=" + @($b.threads).Count)
if (@($b.tickers).Count -gt 0) {
  $b.tickers | Select-Object -First 5 | ForEach-Object { Write-Output ("  " + $_.ticker + " n=" + $_.mentions + " src=" + (($_.sources | ForEach-Object { $_ }) -join ",")) }
}
if (@($b.errors).Count -gt 0) {
  Write-Output ("buzz_errors=" + (($b.errors | Select-Object -First 3) -join " | "))
}
Write-Output ("ui_simple_advanced=check_browser hard-refresh")
