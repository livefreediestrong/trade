$r = Invoke-RestMethod http://127.0.0.1:5056/api/buzz
Write-Output ("auth_mode=" + $r.auth_mode)
if ($r.reddit_auth) {
  Write-Output ("configured=" + $r.reddit_auth.configured)
  Write-Output ("mode=" + $r.reddit_auth.mode)
}
Write-Output ("tickers=" + @($r.tickers).Count)
