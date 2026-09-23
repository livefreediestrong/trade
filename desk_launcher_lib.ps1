# Shared helpers for the Windows desktop launcher/restart scripts.
# Dot-source this from Start-Tomahawk.ps1 / restart_signal_desk.ps1 /
# install_and_restart.ps1 / force_restart.ps1 - never invoke it directly.
#
# Design goals (see README "Desktop launcher"):
#  - Resolve the repo/data paths from the *script's own location*, never a
#    hardcoded absolute path, so the same script works in any checkout.
#  - Identify "the desk process" by PID + command-line identity (python.exe
#    running this repo's app.py), never "whatever is listening on the port" -
#    a shared machine/dev box can have unrelated services on 5056.
#  - Fail CLOSED: if identity can't be confirmed, do not kill anything; report
#    the mismatch so a human can look.
#  - Poll health with a repo/version fingerprint, not just "port answered".
#  - Preserve prior run logs (timestamped files, never overwritten/deleted).

$ErrorActionPreference = "Stop"

function Get-DeskRoot {
    param([Parameter(Mandatory)][string]$InvocationPath)
    return (Split-Path -Parent $InvocationPath)
}

function Get-DeskPort {
    if ($env:TOMAHAWK_PORT) {
        try { return [int]$env:TOMAHAWK_PORT } catch { return 5056 }
    }
    return 5056
}

function Get-DeskHealthUrl {
    param([string]$Root)
    $port = Get-DeskPort
    return "http://127.0.0.1:$port/api/health"
}

function Get-DeskPidFile {
    param([string]$Root)
    if ($env:TOMAHAWK_INSTANCE_LOCK) { return $env:TOMAHAWK_INSTANCE_LOCK }
    $dataDir = if ($env:TOMAHAWK_DATA_DIR) { $env:TOMAHAWK_DATA_DIR } else { Join-Path $Root "data" }
    return (Join-Path $dataDir "tomahawk.pid")
}

function Get-DeskLogDir {
    param([string]$Root)
    $dir = Join-Path $Root "logs"
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    return $dir
}

function Test-ProcessIsThisDesk {
    <#
      Fail-closed identity check: the candidate PID must (a) still be alive,
      (b) be a python(.exe)/pythonw(.exe) process, and (c) have "app.py" on
      its command line. Combined with the fact that the PID came from THIS
      repo's own data\tomahawk.pid file (only this checkout's app.py writes
      there - see acquire_instance_lock in app.py), this is enough to avoid
      accidentally targeting an unrelated process on the machine.

      Note: run.bat/Launch.bat activate the venv then invoke a bare `python
      app.py`, so the OS command line rarely contains an absolute repo path
      - we deliberately do NOT require a path match here, only the process
      kind + app.py, to avoid false negatives on the normal launch path.
    #>
    param(
        [Parameter(Mandatory)][int]$ProcId,
        [Parameter(Mandatory)][string]$Root
    )
    if ($ProcId -le 0) { return $false }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcId" -ErrorAction SilentlyContinue
    if (-not $proc) { return $false }
    $name = [string]$proc.Name
    if ($name -notmatch '^python(w)?(\.exe)?$') { return $false }
    $cmd = [string]$proc.CommandLine
    if (-not $cmd) { return $false }
    if ($cmd -notlike "*app.py*") { return $false }
    return $true
}

function Get-VerifiedDeskProcess {
    <#
      Returns a hashtable: @{ Found = bool; ProcId = int; Reason = string }
      Found=$true only when the PID file's PID is alive AND verified as this
      repo's app.py. Never returns a PID it hasn't verified.
    #>
    param([Parameter(Mandatory)][string]$Root)
    $pidFile = Get-DeskPidFile -Root $Root
    if (-not (Test-Path $pidFile)) {
        return @{ Found = $false; ProcId = 0; Reason = "no pid file at $pidFile" }
    }
    $raw = (Get-Content -Path $pidFile -Raw -ErrorAction SilentlyContinue)
    $parsedPid = 0
    if (-not [int]::TryParse(($raw -as [string]).Trim(), [ref]$parsedPid) -or $parsedPid -le 0) {
        return @{ Found = $false; ProcId = 0; Reason = "pid file unreadable/invalid: '$raw'" }
    }
    if (-not (Get-Process -Id $parsedPid -ErrorAction SilentlyContinue)) {
        return @{ Found = $false; ProcId = $parsedPid; Reason = "pid $parsedPid in lock file is not running" }
    }
    if (-not (Test-ProcessIsThisDesk -ProcId $parsedPid -Root $Root)) {
        return @{ Found = $false; ProcId = $parsedPid; Reason = "pid $parsedPid is running but is NOT this repo's app.py (identity mismatch - refusing to touch it)" }
    }
    return @{ Found = $true; ProcId = $parsedPid; Reason = "verified" }
}

function Stop-DeskProcessSafely {
    <#
      Stops the desk ONLY if it can be positively identified via the PID
      file + command-line check. Never kills "everything on port 5056".
      Returns @{ Stopped=bool; Message=string }
    #>
    param([Parameter(Mandatory)][string]$Root)
    $info = Get-VerifiedDeskProcess -Root $Root
    if ($info.Found) {
        Write-Host "Stopping verified desk process PID $($info.ProcId)..."
        try {
            Stop-Process -Id $info.ProcId -Force -ErrorAction Stop
            Start-Sleep -Milliseconds 500
            return @{ Stopped = $true; Message = "Stopped PID $($info.ProcId)" }
        } catch {
            return @{ Stopped = $false; Message = "Failed to stop verified PID $($info.ProcId): $_" }
        }
    }
    if ($info.ProcId -gt 0 -and $info.Reason -like "*is not running*") {
        # Stale lock file pointing at a dead PID - safe to clear, nothing to kill.
        Write-Host "Clearing stale lock file ($($info.Reason))."
        Remove-Item -Path (Get-DeskPidFile -Root $Root) -ErrorAction SilentlyContinue
        return @{ Stopped = $true; Message = "No live process; cleared stale lock" }
    }
    if ($info.ProcId -gt 0) {
        # PID exists but failed identity verification - FAIL CLOSED.
        Write-Warning "Refusing to stop PID $($info.ProcId): $($info.Reason)"
        return @{ Stopped = $false; Message = $info.Reason }
    }
    return @{ Stopped = $true; Message = "Nothing to stop ($($info.Reason))" }
}

function Wait-DeskHealthy {
    <#
      Polls /api/health until it returns 200 with a body that looks like THIS
      checkout AND this repo's own PID lock file (data\tomahawk.pid) is held
      by a verified, live app.py process. Both checks matter: multiple
      worktrees of the same repo can share the same machine/port, so a bare
      "port answers with the right banner" can false-positive against a
      DIFFERENT checkout that already grabbed the port before we even tried
      to start. The pid-lock cross-check is decisive here because it is
      written only by an app.py instance whose DATA_DIR resolves under this
      exact repo root (see acquire_instance_lock in app.py) - a foreign
      checkout answering the port never holds our lock file.
      Returns @{ Healthy=bool; Body=string; Message=string }
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [int]$TimeoutSec = 45,
        [int]$PollMs = 700
    )
    $url = Get-DeskHealthUrl -Root $Root
    $expectedPort = Get-DeskPort
    $normRoot = $Root.TrimEnd('\')
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $lastError = $null
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($r.StatusCode -eq 200) {
                try {
                    $payload = $r.Content | ConvertFrom-Json
                } catch {
                    $payload = $null
                }
                $bannerOk = $payload -and ($payload.banner -like "*Tomahawk*")
                $portOk = -not $payload.port -or ($payload.port -eq $expectedPort)
                $fingerprint = $payload.fingerprint
                if ($fingerprint -and $fingerprint.repo_root) {
                    $fpRoot = ([string]$fingerprint.repo_root).TrimEnd('\')
                    $repoOk = $fpRoot -ieq $normRoot
                } else {
                    # Older server without a fingerprint field - defer to the
                    # pid-lock cross-check below instead of assuming OK.
                    $repoOk = $true
                }
                $lockInfo = Get-VerifiedDeskProcess -Root $Root
                $lockOk = $lockInfo.Found
                if ($bannerOk -and $portOk -and $repoOk -and $lockOk) {
                    return @{ Healthy = $true; Body = $r.Content; Message = "healthy at $url (verified PID $($lockInfo.ProcId))" }
                }
                if ($bannerOk -and $portOk -and (-not $repoOk -or -not $lockOk)) {
                    $lastError = "port $expectedPort answers, but it is NOT verified as this checkout ($Root) - a different worktree/instance likely owns it ($($lockInfo.Reason))"
                } else {
                    $lastError = "responded but fingerprint mismatch (banner/port) - a different service may own $url"
                }
            }
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds $PollMs
    }
    return @{ Healthy = $false; Body = $null; Message = "timed out after ${TimeoutSec}s waiting for $url ($lastError)" }
}

function Start-DeskProcess {
    <#
      Starts `python app.py` for this repo, logging stdout/stderr to a
      timestamped file under logs\ (never overwrites/deletes prior logs).
      Returns @{ Started=bool; LogPath=string; Message=string }
    #>
    param(
        [Parameter(Mandatory)][string]$Root,
        [string]$PythonExe
    )
    $existing = Get-VerifiedDeskProcess -Root $Root
    if ($existing.Found) {
        return @{ Started = $false; LogPath = $null; Message = "Already running as PID $($existing.ProcId) - not starting a duplicate instance" }
    }
    $py = $PythonExe
    if (-not $py) {
        foreach ($candidate in @(
            (Join-Path $Root ".venv\Scripts\python.exe"),
            (Join-Path $Root "venv\Scripts\python.exe")
        )) {
            if (Test-Path $candidate) { $py = $candidate; break }
        }
    }
    if (-not $py -or -not (Test-Path $py)) {
        return @{ Started = $false; LogPath = $null; Message = "No Python venv found under $Root (.venv or venv). Run install_and_restart.ps1 first." }
    }
    $logDir = Get-DeskLogDir -Root $Root
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $logPath = Join-Path $logDir "desk_$stamp.log"
    try {
        $proc = Start-Process -FilePath $py -ArgumentList "app.py" -WorkingDirectory $Root -WindowStyle Minimized `
            -RedirectStandardOutput $logPath -RedirectStandardError (Join-Path $logDir "desk_${stamp}_err.log") -PassThru
        return @{ Started = $true; LogPath = $logPath; Message = "Started $py app.py (PID $($proc.Id), logging to $logPath)"; ProcId = $proc.Id }
    } catch {
        return @{ Started = $false; LogPath = $logPath; Message = "Failed to start desk: $_"; ProcId = 0 }
    }
}
