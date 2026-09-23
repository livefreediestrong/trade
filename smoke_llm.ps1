$ErrorActionPreference = "Stop"
$r = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/llm/status"
Write-Host ("configured=" + $r.configured + " model=" + $r.model)
$body = '{"ticker":"AAPL"}'
$t = Invoke-RestMethod -Uri "http://127.0.0.1:5056/api/llm/thesis" -Method POST -ContentType "application/json" -Body $body
Write-Host ("thesis_ok=" + $t.ok)
if ($t.thesis) {
  Write-Host ("side=" + $t.thesis.side + " conf=" + $t.thesis.confidence)
  $th = [string]$t.thesis.thesis
  if ($th.Length -gt 180) { $th = $th.Substring(0,180) + "..." }
  Write-Host ("thesis=" + $th)
} elseif ($t.signal) {
  Write-Host ("llm_side=" + $t.signal.llm_side)
  $th = [string]$t.signal.llm_thesis
  if ($th.Length -gt 180) { $th = $th.Substring(0,180) + "..." }
  Write-Host ("thesis=" + $th)
} else {
  Write-Host (($t | ConvertTo-Json -Depth 5).Substring(0, [Math]::Min(500, ($t | ConvertTo-Json -Depth 5).Length)))
}
