# Writes due-flag + optional popup for IBKR equity / market-data CHECK.
# Invoked by scheduled task Tomahawk-Check-IBKR-Equity (Oct 1-3 2026 mornings).
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$TasksDir = Join-Path $Root 'data\tasks'
$Flag = Join-Path $TasksDir 'DUE-check-ibkr-equity.txt'
$Detail = Join-Path $TasksDir 'check-ibkr-equity-market-data.md'
New-Item -ItemType Directory -Force -Path $TasksDir | Out-Null
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz'
@"
DUE: CHECK IBKR equity / market-data resubscribe
Fired: $stamp
Account: U28575784
Action: Confirm Portfolio NLV >= 500, then subscribe Snapshot + Equity/Options Add-On (~`$4.50).
Reply in chat: continue subscribe
Details: $Detail
"@ | Set-Content -Path $Flag -Encoding UTF8
$msg = "Tomahawk CHECK: IBKR U28575784 — verify NLV>=500 after `$500 ACH, then resubscribe Snapshot + `$4.50 Equity/Options Add-On. Flag: $Flag"
try {
  msg.exe * $msg 2>$null
} catch {}
try {
  Add-Type -AssemblyName System.Windows.Forms -ErrorAction SilentlyContinue
  [System.Windows.Forms.MessageBox]::Show($msg, 'Tomahawk-Check-IBKR-Equity', 'OK', 'Information') | Out-Null
} catch {}
Write-Host $msg
