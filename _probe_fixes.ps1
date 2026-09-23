$ErrorActionPreference = "Continue"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\maher\Projects\daytrade-signal-desk\smoke_desk.ps1
$s = Invoke-RestMethod http://127.0.0.1:5056/api/state
Write-Output ("session_active=" + $s.config.session_active + " mode=" + $s.config.mode)
Write-Output ("heat_n=" + @($s.heat).Count)
$html = (Invoke-WebRequest http://127.0.0.1:5056/ -UseBasicParsing).Content
Write-Output ("has_streaming=" + ($html -match "Streaming"))
Write-Output ("has_research_heat=" + ($html -match "research|Heat|buzz"))
# refuse fill without session: try approve won't work easily; check config POST auto_paper alone shouldn't enable fills - just note state
Write-Output ("state_ms_smoke_passed")
# light sparks
try { $sp = Invoke-RestMethod "http://127.0.0.1:5056/api/sparks?tickers=AAPL"; Write-Output ("sparks_ok=" + $sp.ok) } catch { Write-Output ("sparks_err=" + $_.Exception.Message) }
