$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
$urls = @(
  "https://www.reddit.com/r/wallstreetbets/hot.json?limit=5",
  "https://old.reddit.com/r/wallstreetbets/hot.json?limit=5",
  "https://api.reddit.com/r/wallstreetbets/hot?limit=5"
)
foreach ($u in $urls) {
  try {
    $resp = Invoke-WebRequest -Uri $u -Headers @{ "User-Agent" = $ua; "Accept" = "application/json" } -UseBasicParsing -TimeoutSec 15
    Write-Output ("OK " + $resp.StatusCode + " " + $u + " len=" + $resp.Content.Length)
  } catch {
    $code = $null
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    Write-Output ("FAIL " + $code + " " + $u)
  }
}
