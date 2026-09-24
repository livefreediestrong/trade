"""Run Windows launcher branches with process/browser side effects intercepted."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[1] / 'Start-Tomahawk.ps1'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows launcher')


def run_ps(tmp_path, body):
    script = tmp_path / 'check.ps1'
    launcher = str(LAUNCHER).replace("'", "''")
    root = str(tmp_path).replace("'", "''")
    script.write_text(f". '{launcher}'\n$Root = '{root}'\n$ErrorActionPreference = 'Stop'\n" + body, encoding='utf-8')
    result = subprocess.run(['powershell.exe','-NoProfile','-File',str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_health_is_app_specific(tmp_path):
    run_ps(tmp_path, r"""
if (Test-DeskResponse @{ok=$true;port=5056} 5056) { throw 'Accepted unrelated server' }
if (Test-DeskResponse @{app_id='tomahawk-desk';port=5057} 5056) { throw 'Wrong port accepted' }
if (-not (Test-DeskResponse @{app_id='tomahawk-desk';port=5056} 5056)) { throw 'Desk not recognized' }
""")


def test_reuse_does_not_install_or_spawn_server(tmp_path):
    run_ps(tmp_path, r"""
$NoBrowser = $true
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'TOMAHAWK_PORT') { return '59431' }; return $Default }
function Test-Desk { return $true }
function Ensure-DeskPython { throw 'Already running: do not install' }
function Start-Process { throw 'Already running: do not spawn' }
function Start-ConfiguredBroker { return @{state='port_ready';message='ready'} }
Invoke-DeskLauncher
Invoke-DeskLauncher
if (-not (Test-Path -LiteralPath (Join-Path $Root 'data\launcher-status.json'))) { throw 'Missing status' }
""")


def test_occupied_port_is_never_killed(tmp_path):
    run_ps(tmp_path, r"""
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'TOMAHAWK_PORT') { return '59432' }; return $Default }
function Test-Desk { return $false }
function Get-NetTCPConnection { return @{OwningProcess=1234} }
function Stop-Process { throw 'Must not kill another app' }
function Start-Process { throw 'Must not spawn on occupied port' }
$blocked=$false
try { Invoke-DeskLauncher } catch { $blocked = $_.Exception.Message -like '*another application*' }
if (-not $blocked) { throw 'Occupied port not rejected' }
""")


def test_gateway_reuses_running_instance_and_reports_login(tmp_path):
    run_ps(tmp_path, r"""
function Get-LaunchSetting($Name,$Default) {
    if ($Name -eq 'BROKER_PROVIDER') { return 'ibkr' }; return $Default
}
function Test-BrokerPort { return $false }
function Find-Gateway { return 'C:\Installed\ibgateway.exe' }
$script:started=0
function Get-CimInstance { if ($script:started) { return @{ExecutablePath='C:\Installed\ibgateway.exe'} } }
function Start-Process { $script:started++ }
$first=Start-ConfiguredBroker
$second=Start-ConfiguredBroker
if ($script:started -ne 1) { throw 'Duplicate Gateway' }
if ($first.state -ne 'sign_in_required' -or $second.state -ne 'sign_in_required') { throw 'Login falsely reported ready' }
""")


def test_gateway_missing_and_remote_failures_remain_explicit(tmp_path):
    run_ps(tmp_path, r"""
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'BROKER_PROVIDER') { return 'ibkr' }; return $Default }
function Test-BrokerPort { return $false }
function Find-Gateway { return $null }
function Start-Process { throw 'Must not spawn missing Gateway' }
if ((Start-ConfiguredBroker).state -ne 'not_installed') { throw 'Missing install not reported' }
function Get-LaunchSetting($Name,$Default) {
    if ($Name -eq 'BROKER_PROVIDER') { return 'ibkr' }
    if ($Name -eq 'IB_GATEWAY_HOST') { return '192.0.2.1' }; return $Default
}
if ((Start-ConfiguredBroker).state -ne 'remote_unavailable') { throw 'Remote failure not reported' }
""")


def test_gateway_ready_port_does_not_spawn_or_claim_verified_account(tmp_path):
    run_ps(tmp_path, r"""
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'BROKER_PROVIDER') { return 'ibkr' }; return $Default }
function Test-BrokerPort { return $true }
function Find-Gateway { throw 'Do not look for another installation' }
function Start-Process { throw 'Do not start twice' }
$result=Start-ConfiguredBroker
if ($result.state -ne 'port_ready' -or $result.message -notlike '*still verifies*') { throw 'Port is not account verification' }
""")


def test_native_import_failure_returns_code_and_traceback(tmp_path):
    executable = sys.executable.replace("'", "''")
    run_ps(tmp_path, f"$executable = '{executable}'\n" + r'''
$log = Join-Path $Root 'import.log'
$code = Invoke-SetupCommand $executable @('-c', '"import tomahawk_intentionally_missing_dependency"') $log
if ($code -eq 0) { throw 'Missing dependency was reported installed' }
if ((Get-Content -LiteralPath $log -Raw) -notlike '*ModuleNotFoundError*') { throw 'Failure lost its diagnostic' }
''')


def test_missing_dependency_runs_install_and_reports_failure(tmp_path):
    run_ps(tmp_path, r'''
New-Item -ItemType Directory -Force -Path (Join-Path $Root '.venv\Scripts') | Out-Null
New-Item -ItemType File -Path (Join-Path $Root '.venv\Scripts\python.exe') | Out-Null
$script:calls = @()
function Invoke-SetupCommand($Executable,$Arguments,$LogPath) {
    $script:calls += ($Arguments -join ' ')
    return 1
}
$failed = $false
try { Ensure-DeskPython $Root } catch { $failed = $_.Exception.Message -like '*Dependency setup failed*' }
if (-not $failed -or $script:calls.Count -ne 2 -or $script:calls[1] -notlike '*pip install -r*') {
    throw 'Missing dependency did not reach the installer with an actionable failure'
}
''')


def test_gateway_start_error_still_opens_healthy_desk(tmp_path):
    run_ps(tmp_path, r'''
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'TOMAHAWK_PORT') { return '59433' }; return $Default }
function Test-Desk { return $true }
function Start-ConfiguredBroker { throw 'Gateway executable unavailable' }
$script:opened = $null
function Start-Process($FilePath) { $script:opened = $FilePath }
Invoke-DeskLauncher
$status = Get-Content -LiteralPath (Join-Path $Root 'data\launcher-status.json') -Raw | ConvertFrom-Json
if ($status.gateway -ne 'startup_error' -or $script:opened -ne 'http://127.0.0.1:59433/') { throw 'Gateway failure hid the healthy desk' }
''')
