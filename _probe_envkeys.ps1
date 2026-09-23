if (Test-Path C:\Users\maher\Projects\daytrade-signal-desk\.env) {
  Select-String -Path C:\Users\maher\Projects\daytrade-signal-desk\.env -Pattern "REDDIT|STOCKTWITS|GEMINI" | ForEach-Object { $_.Line -replace "=(.+)$","=***" }
} else { Write-Output "no .env" }
