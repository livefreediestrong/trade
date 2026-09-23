$ErrorActionPreference = "Continue"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\maher\Projects\daytrade-signal-desk\smoke_desk.ps1 | Write-Output
$s = Invoke-RestMethod http://127.0.0.1:5056/api/state
Write-Output ("heat_n=" + @($s.heat).Count)
if ($s.target -and $s.target.pace) { Write-Output ("pace_status=" + $s.target.pace.status + " expected=" + $s.target.pace.expected_usd) }
elseif ($s.pace) { Write-Output ("pace_status=" + $s.pace.status) }
else { Write-Output ("pace_keys_target=" + (($s.target.PSObject.Properties.Name) -join ",")) }
Write-Output ("buzz_heat=" + @($s.buzz.heat).Count)
$html = (Invoke-WebRequest http://127.0.0.1:5056/ -UseBasicParsing).Content
Write-Output ("has_live=" + ($html -match "live-indicator|id=.live|Live"))
Write-Output ("has_heat=" + ($html -match "heat-lane"))
Write-Output ("has_race=" + ($html -match "race-meter|goal-race|pace"))
Write-Output ("has_opp=" + ($html -match "opp-churn|Opportunities"))
Write-Output ("has_spark=" + ($html -match "spark-strip"))
Write-Output ("has_sound=" + ($html -match "alert.sound|alert_sound"))
try {
  $sp = Invoke-RestMethod "http://127.0.0.1:5056/api/sparks?tickers=AAPL,MSFT"
  Write-Output ("sparks_ok=" + $sp.ok + " tickers=" + (($sp.sparks.PSObject.Properties.Name) -join ","))
} catch { Write-Output ("sparks_err=" + $_.Exception.Message) }
# quick SSE peek via .NET HttpClient with short timeout
try {
  $handler = New-Object System.Net.Http.HttpClientHandler
  $client = New-Object System.Net.Http.HttpClient($handler)
  $client.Timeout = [TimeSpan]::FromSeconds(8)
  $stream = $client.GetStreamAsync("http://127.0.0.1:5056/api/loop/stream").Result
  $reader = New-Object System.IO.StreamReader($stream)
  $buf = ""
  $deadline = [DateTime]::UtcNow.AddSeconds(6)
  while ([DateTime]::UtcNow -lt $deadline) {
    $line = $reader.ReadLine()
    if ($null -eq $line) { break }
    $buf += $line + "`n"
    if ($buf -match "state_lite") { break }
  }
  Write-Output ("sse_has_hello=" + ($buf -match "hello"))
  Write-Output ("sse_has_state_lite=" + ($buf -match "state_lite"))
  $client.Dispose()
} catch { Write-Output ("sse_err=" + $_.Exception.Message) }
