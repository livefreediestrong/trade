"""Exercise the actual legacy PowerShell entry point with all effects intercepted."""
import os
from pathlib import Path
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows helper')
HELPER = Path(__file__).resolve().parents[1] / 'tools' / 'Ensure-IBGateway.ps1'


def test_legacy_helper_uses_verified_desk_and_automatic_policy(tmp_path):
    script = tmp_path / 'test.ps1'
    script.write_text(". '" + str(HELPER).replace("'", "''") + "'\n" + r'''
$ErrorActionPreference='Stop'
function Start-Process { throw 'No independent Gateway launch allowed' }
function Stop-Process { throw 'No stopping Gateway allowed' }
function Get-LaunchSetting($Name,$Default) { if ($Name -eq 'TOMAHAWK_PORT') {return '5059'}; return $Default }
$script:requests=0
function Get-DeskHealth { return $null }
function Invoke-RestMethod { throw 'Unverified app must not receive a request' }
if ((Invoke-AutomaticGatewayCheck).ok) { throw 'Unknown desk accepted' }
function Get-DeskHealth($Url,$Port) { if ($Url -ne 'http://127.0.0.1:5059/' -or $Port -ne 5059) { throw 'Wrong configured port' }; return @{ok=$true} }
function Invoke-RestMethod($Uri,$Method,$ContentType,$Body,$TimeoutSec) {
    $script:requests++
    $payload=$Body | ConvertFrom-Json
    if ($Uri -ne 'http://127.0.0.1:5059/api/broker-ensure-gateway' -or $payload.automatic -ne $true -or $payload.launch_if_down -ne $true) { throw 'Wrong operation' }
    return @{ok=$false;launched=$false;automatic_launch_held=$true;note='No active live session'}
}
$reply=Invoke-AutomaticGatewayCheck
if ($script:requests -ne 1 -or -not $reply.automatic_launch_held -or $reply.launched) { throw 'Ignored desk hold' }
''', encoding='utf-8')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-File', str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
