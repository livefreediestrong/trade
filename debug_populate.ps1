$ErrorActionPreference = "Continue"
Write-Host "=== HEALTH ==="
try { (Invoke-RestMethod http://127.0.0.1:5056/api/health) | ConvertTo-Json -Depth 6 } catch { Write-Host $_ }
Write-Host "=== STATE KEYS ==="
try {
  $s = Invoke-RestMethod http://127.0.0.1:5056/api/state
  Write-Host ("mode=" + $s.config.mode)
  Write-Host ("session_active=" + $s.config.session_active)
  Write-Host ("loop_enabled=" + $s.config.loop_enabled)
  Write-Host ("watchlist_count=" + @($s.config.watchlist).Count)
  Write-Host ("watchlist_first10=" + (($s.config.watchlist | Select-Object -First 10) -join ","))
  Write-Host ("pending=" + @($s.signals.pending).Count + " approved=" + @($s.signals.approved).Count)
  Write-Host ("loop=" + ($s.loop | ConvertTo-Json -Compress -Depth 5))
  Write-Host ("target=" + ($s.daily_target | ConvertTo-Json -Compress))
} catch { Write-Host $_ }
Write-Host "=== LOOP FEED ==="
try { (Invoke-RestMethod http://127.0.0.1:5056/api/loop/feed) | ConvertTo-Json -Depth 6 } catch { Write-Host $_ }
Write-Host "=== LLM STATUS ==="
try { (Invoke-RestMethod http://127.0.0.1:5056/api/llm/status) | ConvertTo-Json -Depth 4 } catch { Write-Host $_ }
