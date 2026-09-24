# Single launcher for the desktop shortcut and legacy commands.
param([switch]$NoBrowser, [switch]$NoBroker, [switch]$NoDialogs)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot

function Get-LaunchSetting([string]$Name, [string]$Default = '') {
    $value = [Environment]::GetEnvironmentVariable($Name, 'Process')
    if ($null -ne $value) { return $value }
    $path = Join-Path $Root '.env'
    $value = $Default
    if (Test-Path -LiteralPath $path) {
        foreach ($line in [IO.File]::ReadAllLines($path)) {
            if ($line -match '^\s*(?:export\s+)?([^#=\s]+)\s*=(.*)$' -and $Matches[1] -eq $Name) {
                $raw = $Matches[2].Trim()
                $quoted = [regex]::Match($raw, '^"((?:\\.|[^"\\])*)"\s*(?:#.*)?$')
                $doubleQuoted = $quoted.Success
                if (-not $quoted.Success) { $quoted = [regex]::Match($raw, '^''((?:\\.|[^''\\])*)''\s*(?:#.*)?$') }
                if ($quoted.Success) {
                    $value = $quoted.Groups[1].Value
                    $pattern = if ($doubleQuoted) { '\\([\\"''abfnrtv])' } else { '\\([\\''])' }
                    $value = [regex]::Replace($value, $pattern, {
                        param($match)
                        $escapes = @{a=[string][char]7;b=[string][char]8;f=[string][char]12;n="`n";r="`r";t="`t";v=[string][char]11}
                        $key = $match.Groups[1].Value
                        if ($escapes.ContainsKey($key)) { $escapes[$key] } else { $key }
                    })
                } else {
                    $value = ($raw -replace '\s+#.*$', '').Trim()
                }
            }
        }
    }
    return $value
}
function Resolve-LaunchPath([string]$Path) {
    if (-not [IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Root $Path }
    return [IO.Path]::GetFullPath($Path)
}
function Get-LaunchDataRoot {
    $data = Get-LaunchSetting 'TOMAHAWK_DATA_DIR' (Join-Path $Root 'data')
    if (-not $data) { $data = Join-Path $Root 'data' }
    return Resolve-LaunchPath $data
}
function Test-DeskResponse($Response, [int]$Port) {
    if ($Response.port -ne $Port -or $Response.app_id -ne 'tomahawk-desk' -or
        -not $Response.instance.source_root -or -not $Response.instance.data_root) { return $false }
    try {
        $expectedRoot = (Resolve-LaunchPath $Root).TrimEnd('\', '/')
        $expectedData = (Get-LaunchDataRoot).TrimEnd('\', '/')
        if (-not [IO.Path]::IsPathRooted($Response.instance.source_root) -or
            -not [IO.Path]::IsPathRooted($Response.instance.data_root)) { return $false }
        $source = [IO.Path]::GetFullPath($Response.instance.source_root).TrimEnd('\', '/')
        $data = [IO.Path]::GetFullPath($Response.instance.data_root).TrimEnd('\', '/')
        return ([string]::Equals($source, $expectedRoot, [StringComparison]::OrdinalIgnoreCase) -and
                [string]::Equals($data, $expectedData, [StringComparison]::OrdinalIgnoreCase))
    } catch { return $false }
}
function Get-DeskHealth([string]$Url, [int]$Port) {
    # Health of THIS checkout's desk, or $null (unreachable or another installation).
    try { $response = Invoke-RestMethod "${Url}api/health" -TimeoutSec 3 } catch { return $null }
    if (Test-DeskResponse $response $Port) { return $response }
    return $null
}
function Test-Desk([string]$Url, [int]$Port) { return ($null -ne (Get-DeskHealth $Url $Port)) }
function Confirm-DeskRestart([string]$Message) {
    # Headless runs (watchdog, -NoDialogs) never restart a running desk.
    if ($NoDialogs) { return $false }
    Add-Type -AssemblyName PresentationFramework
    return ([System.Windows.MessageBox]::Show($Message, 'Daytrade Signal Desk', 'YesNo', 'Question') -eq 'Yes')
}
function Request-DeskRestart([string]$Url, [int]$Port) {
    # The desk refuses while a broker order is unresolved; otherwise it exits and frees the port.
    try {
        Invoke-RestMethod "${Url}api/desk/shutdown" -Method Post -ContentType 'application/json' `
            -Body (@{ confirm = 'RESTART' } | ConvertTo-Json -Compress) -TimeoutSec 10 | Out-Null
    } catch {
        $detail = $null
        try { $detail = ($_.ErrorDetails.Message | ConvertFrom-Json).error } catch {}
        if (-not $detail) { $detail = $_.Exception.Message }
        return @{ ok = $false; message = "The desk kept running the previous version: $detail" }
    }
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)) { return @{ ok = $true } }
        Start-Sleep -Milliseconds 500
    }
    return @{ ok = $false; message = 'The desk accepted the restart but was still running after 20 seconds.' }
}
function Test-BrokerPort([string]$Server, [int]$Port) {
    $socket = New-Object Net.Sockets.TcpClient
    try {
        $connect = $socket.BeginConnect($Server, $Port, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(600)) { return $false }
        $socket.EndConnect($connect)
        return $socket.Connected
    } catch { return $false } finally { $socket.Dispose() }
}
function Find-Gateway {
    $explicit = Get-LaunchSetting 'IB_GATEWAY_EXE'
    if ($explicit) {
        if (Test-Path -LiteralPath $explicit -PathType Leaf) { return (Resolve-Path -LiteralPath $explicit).Path }
        throw 'IB_GATEWAY_EXE does not point to an installed executable.'
    }
    $candidates = @()
    foreach ($base in @('C:\Jts\ibgateway', (Join-Path $env:USERPROFILE 'Jts\ibgateway'))) {
        if (Test-Path -LiteralPath $base) {
            $candidates += Get-ChildItem -LiteralPath $base -Directory | ForEach-Object {
                $candidate = Join-Path $_.FullName 'ibgateway.exe'
                if (Test-Path -LiteralPath $candidate) { Get-Item -LiteralPath $candidate }
            }
        }
    }
    return ($candidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName)
}
function Get-GatewayStampPath { Join-Path (Get-LaunchDataRoot) 'gateway_launch.json' }
function Get-GatewayStamp {
    try { return (Get-Content -LiteralPath (Get-GatewayStampPath) -Raw -ErrorAction Stop | ConvertFrom-Json) } catch { return $null }
}
function Get-GatewayLaunchCount([double]$WindowSec, [string]$Key = 'launches') {
    # Launches recorded by any desk component (broker_ibkr.ensure_gateway, this launcher, Ensure-IBGateway).
    $stamp = Get-GatewayStamp
    if (-not $stamp) { return 0 }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    return @(@($stamp.$Key) | Where-Object { $null -ne $_ -and ($now - [double]$_) -ge 0 -and ($now - [double]$_) -lt $WindowSec }).Count
}
function Test-RecentGatewayLaunch([int]$CooldownSec = 180) {
    # Shared with broker_ibkr.ensure_gateway so no two components launch Gateway back to back.
    $stamp = Get-GatewayStamp
    if (-not $stamp) { return $false }
    try {
        $age = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$stamp.at
        return ($age -ge 0 -and $age -lt $CooldownSec)
    } catch { return $false }
}
function Set-GatewayLaunchStamp([string]$Exe, [string]$Source = 'launcher', [switch]$Automatic) {
    # Merge into the shared record: keep api_seen_at (sign-in history) and the launch counts.
    try {
        $path = Get-GatewayStampPath
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        $data = [ordered]@{}
        $stamp = Get-GatewayStamp
        if ($stamp) { foreach ($property in $stamp.PSObject.Properties) { $data[$property.Name] = $property.Value } }
        $launches = @(@($data['launches']) | Where-Object { $null -ne $_ -and ($now - [double]$_) -lt 86400 })
        $data['launches'] = @($launches) + @($now)
        if ($Automatic) {
            $auto = @(@($data['automatic_launches']) | Where-Object { $null -ne $_ -and ($now - [double]$_) -lt 86400 })
            $data['automatic_launches'] = @($auto) + @($now)
        }
        $data['at'] = $now; $data['exe'] = $Exe; $data['source'] = $Source; $data['pid'] = $null
        $data | ConvertTo-Json -Compress | Set-Content -LiteralPath $path -Encoding ASCII
    } catch {}
}
function Get-GatewayCheck {
    # ok = $false when Windows could not be asked: a failed check must never read as "not running",
    # or every launcher run would open another login window.
    try { $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop) } catch { return @{ ok = $false; running = @() } }
    $running = @($processes | Where-Object {
        $_.Name -in @('ibgateway.exe', 'tws.exe') -or "$($_.Name)" -match '(?i)^(ib(kr)?gateway|tws)[\w.-]*\.exe$' -or
        ($_.Name -in @('java.exe', 'javaw.exe') -and "$($_.CommandLine)" -match '(?i)ibgateway|ibcalpha|\\jts\\|/jts/|jclient|twslaunch')
    })
    # Login windows are titled "IBKR Gateway" (10.51+), "IB Gateway" or "Trader Workstation".
    $running += @(Get-Process -ErrorAction SilentlyContinue | Where-Object { "$($_.Name)" -notin @('chrome','msedge','firefox','brave','opera','explorer','Code','notepad','notepad++','powershell','pwsh','cmd','WindowsTerminal','OUTLOOK','WINWORD') -and "$($_.MainWindowTitle)" -match '(?i)^\s*(IBKR|IB)\s+Gateway\b|^\s*Trader Workstation\b' })
    return @{ ok = $true; running = $running }
}
function Get-RunningGateway { (Get-GatewayCheck).running }
function Test-GatewayAutolaunchDisabled { (Get-LaunchSetting 'IB_GATEWAY_AUTOLAUNCH' '1') -match '^(?i)(0|false|no|off)$' }
function Start-ConfiguredBroker {
    if ($NoBroker -or (Get-LaunchSetting 'BROKER_PROVIDER' 'alpaca') -ne 'ibkr') {
        return @{ state = 'not_needed'; message = 'Desk ready.' }
    }
    $server = Get-LaunchSetting 'IB_GATEWAY_HOST' '127.0.0.1'
    $port = [int](Get-LaunchSetting 'IB_GATEWAY_PORT' '4002')
    if (Test-BrokerPort $server $port) {
        return @{ state = 'port_ready'; message = 'Gateway port is reachable. The desk still verifies your account before any broker order.' }
    }
    if ($server -notin @('127.0.0.1', 'localhost', '::1')) {
        return @{ state = 'remote_unavailable'; message = "Desk ready. Start the configured remote Gateway on ${server}:$port." }
    }
    $signIn = @{ state = 'sign_in_required'; message = "Desk ready. Complete sign-in in IB Gateway. If already signed in, enable its API on port $port. Broker execution stays blocked until the account is verified." }
    if (Test-GatewayAutolaunchDisabled) {
        return @{ state = 'sign_in_required'; message = "Desk ready. Gateway launching is off (IB_GATEWAY_AUTOLAUNCH=0): start IB Gateway yourself and sign in." }
    }
    $gateway = Find-Gateway
    if (-not $gateway) {
        return @{ state = 'not_installed'; message = 'Desk ready. Install IB Gateway from Interactive Brokers, or set IB_GATEWAY_EXE to its installed path.' }
    }
    # Any running Gateway/TWS (any version, path or window) counts: its API port stays closed
    # until sign-in, so a closed port must never stack another login window.
    $check = Get-GatewayCheck
    if (-not $check.ok) {
        return @{ state = 'sign_in_required'; message = "Desk ready. Windows could not list running programs, so IB Gateway was not started again. Sign in to the open Gateway window, or start Gateway yourself." }
    }
    if (@($check.running).Count -gt 0 -or (Test-RecentGatewayLaunch)) { return $signIn }
    if ((Get-GatewayLaunchCount 1800) -ge 3) {
        return @{ state = 'sign_in_required'; message = "Desk ready. IB Gateway was already started 3 times in 30 minutes, so it was not started again. Close extra Gateway windows and sign in to one." }
    }
    # One launch at a time across the desk, this launcher and the daily task (same name in broker_ibkr.py).
    $mutex = New-Object Threading.Mutex($false, 'Local\TomahawkGatewayLaunch')
    $held = $false
    try {
        try { $held = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held -or (Test-RecentGatewayLaunch)) { return $signIn }
        # Interactive sign-in window: the user completes login and 2FA.
        Set-GatewayLaunchStamp $gateway 'launcher'
        Start-Process -FilePath $gateway -WorkingDirectory (Split-Path -Parent $gateway) -WindowStyle Normal | Out-Null
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
    return $signIn
}
function Invoke-SetupCommand([string]$Executable, [string[]]$Arguments, [string]$LogPath) {
    # Capture native stderr without PowerShell treating an expected import failure as fatal.
    $job = Start-Process -FilePath $Executable -ArgumentList $Arguments -WorkingDirectory $Root -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.stderr"
    if (Test-Path -LiteralPath "$LogPath.stderr") {
        Get-Content -LiteralPath "$LogPath.stderr" | Add-Content -LiteralPath $LogPath
    }
    return $job.ExitCode
}
function Ensure-DeskPython([string]$LogDir) {
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        $bootstrap = Get-Command py, python -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $bootstrap) { throw 'Install Python 3.11 or later, then open this same shortcut again.' }
        $code = Invoke-SetupCommand $bootstrap.Source @('-m', 'venv', ('"' + (Join-Path $Root '.venv') + '"')) (Join-Path $LogDir 'setup.log')
        if ($code -ne 0) { throw 'Could not create the Python environment. See data\setup.log.' }
    }
    $check = '"import sys; assert sys.version_info >= (3,11); import flask,waitress,yfinance,pandas,numpy,requests,dotenv,tzdata,anthropic,ib_insync,pydantic; assert (2,13) <= tuple(map(int,pydantic.VERSION.split(''.'')[:2])) < (3,0)"'
    $code = Invoke-SetupCommand $python @('-c', $check) (Join-Path $LogDir 'dependency-check.log')
    if ($code -ne 0) {
        $code = Invoke-SetupCommand $python @('-m', 'pip', 'install', '-r', ('"' + (Join-Path $Root 'requirements.txt') + '"')) (Join-Path $LogDir 'setup.log')
        if ($code -ne 0) { throw 'Dependency setup failed. Check your connection and data\setup.log, then open the shortcut again.' }
        $code = Invoke-SetupCommand $python @('-c', $check) (Join-Path $LogDir 'dependency-check.log')
        if ($code -ne 0) { throw 'Dependencies are still incompatible after setup. Python 3.11+ and Pydantic 2.13+ (below 3) are required. See data\dependency-check.log.' }
    }
    return $python
}
function Invoke-DeskLauncher {
    $port = [int](Get-LaunchSetting 'TOMAHAWK_PORT' '5056')
    if ($port -lt 1 -or $port -gt 65535) { throw 'TOMAHAWK_PORT is invalid.' }
    $url = "http://127.0.0.1:$port/"
    $data = Get-LaunchDataRoot
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $mutex = New-Object Threading.Mutex($false, "Local\TomahawkLauncher-$port")
    $owned = $false
    try {
        try { $owned = $mutex.WaitOne(60000) } catch [Threading.AbandonedMutexException] { $owned = $true }
        if (-not $owned) { throw 'Another launch is still starting the desk. Wait a moment and reopen the shortcut.' }
        # A desk started before an update keeps serving the old code; offer to restart it.
        $updateNote = $null
        $health = Get-DeskHealth $url $port
        if ($health -and $health.code -and $health.code.stale) {
            $question = "This folder has updated desk code, but the running desk started before the update and still serves the old version.`n`n" +
                "Restart the desk now to load it? Orders already at the broker are not affected, and the desk will not stop while a broker order is unresolved."
            if (Confirm-DeskRestart $question) {
                $restart = Request-DeskRestart $url $port
                if (-not $restart.ok) {
                    $updateNote = $restart.message
                    if (-not $NoDialogs) {
                        Add-Type -AssemblyName PresentationFramework
                        [System.Windows.MessageBox]::Show($updateNote, 'Daytrade Signal Desk') | Out-Null
                    }
                }
            } else {
                $updateNote = 'Updated desk code is waiting. Reopen the shortcut and choose Yes to restart the desk and load it.'
            }
        }
        if (-not (Test-Desk $url $port)) {
            if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
                throw "Port $port belongs to another application or a different Tomahawk installation. It was left running. Set a different TOMAHAWK_PORT or close that application."
            }
            $python = Ensure-DeskPython $data
            foreach ($name in @('server.stdout.log', 'server.stderr.log')) {
                $log = Join-Path $data $name
                if (Test-Path -LiteralPath $log) { Copy-Item -LiteralPath $log -Destination "$log.previous" -Force }
            }
            $deskProcess = Start-Process -FilePath $python -ArgumentList @('-u', ('"' + (Join-Path $Root 'app.py') + '"')) -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $data 'server.stdout.log') -RedirectStandardError (Join-Path $data 'server.stderr.log')
            $deadline = (Get-Date).AddSeconds(45)
            while (-not (Test-Desk $url $port)) {
                if ($deskProcess.HasExited -or (Get-Date) -gt $deadline) { throw 'The desk did not start. See data\server.stderr.log; reopening this shortcut will retry.' }
                Start-Sleep -Milliseconds 600
                $deskProcess.Refresh()
            }
        }
        try { $broker = Start-ConfiguredBroker }
        catch { $broker = @{ state = 'startup_error'; message = "Desk ready; Gateway needs attention: $($_.Exception.Message)" } }
        $message = if ($updateNote) { "$($broker.message) $updateNote" } else { $broker.message }
        @{ checked_at = [DateTime]::UtcNow.ToString('o'); gateway = $broker.state; message = $message } |
            ConvertTo-Json | Set-Content -LiteralPath (Join-Path $data 'launcher-status.json') -Encoding UTF8
        if (-not $NoBrowser) { Start-Process $url | Out-Null }
        Write-Output $message
    } finally {
        if ($owned) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
# Tests dot-source functions without starting anything.
if ($MyInvocation.InvocationName -ne '.') {
    try { Invoke-DeskLauncher; exit 0 } catch {
        $message = "Tomahawk could not finish startup.`n`n$($_.Exception.Message)"
        if (-not $NoDialogs) {
            Add-Type -AssemblyName PresentationFramework
            [System.Windows.MessageBox]::Show($message, 'Daytrade Signal Desk') | Out-Null
        }
        Write-Error $message -ErrorAction Continue
        exit 1
    }
}
