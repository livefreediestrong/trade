# Compatibility entry point for old scheduled tasks. All unattended Gateway
# decisions belong to the running desk: session, hours, process check and budget.
# Never launches, kills, signs in, changes the API port or edits broker settings.
param()
$DeskRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $DeskRoot 'Start-Tomahawk.ps1')

function Invoke-AutomaticGatewayCheck {
    $port = [int](Get-LaunchSetting 'TOMAHAWK_PORT' '5056')
    $url = "http://127.0.0.1:$port/"
    $verified = Get-DeskHealth $url $port
    if (-not $verified) {
        return @{ ok = $false; launched = $false; note = 'The expected desk is unavailable. Open its normal shortcut; no Gateway was started.' }
    }
    try {
        return Invoke-RestMethod "${url}api/broker-ensure-gateway" -Method Post -ContentType 'application/json' `
            -Body (@{ launch_if_down = $true; automatic = $true } | ConvertTo-Json -Compress) -TimeoutSec 30
    } catch {
        return @{ ok = $false; launched = $false; note = 'The desk could not complete its Gateway check. No separate launch was attempted.' }
    }
}

if ($MyInvocation.InvocationName -ne '.') {
    $result = Invoke-AutomaticGatewayCheck
    Write-Host $result.note
}
