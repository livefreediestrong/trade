$ErrorActionPreference = "Stop"
$r = Invoke-RestMethod http://127.0.0.1:5056/api/buzz
Write-Output ("tickers=" + $r.tickers.Count)
Write-Output ("threads=" + $r.threads.Count)
Write-Output ("errors=" + (($r.errors | ForEach-Object { $_ }) -join "; "))
Write-Output ("fetched_at=" + $r.fetched_at)
if ($r.tickers.Count -gt 0) {
  $r.tickers | Select-Object -First 8 | ForEach-Object {
    Write-Output ($_.ticker + " score=" + $_.score + " n=" + $_.mentions + " " + (($_.sources | ForEach-Object { $_ }) -join ","))
  }
}
if ($r.threads.Count -gt 0) {
  $r.threads | ForEach-Object {
    Write-Output ("thread " + $_.key + " " + $_.title)
  }
}
