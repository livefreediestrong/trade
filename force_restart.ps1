# Compatibility entry point: reuse components; never kill a port owner.
& (Join-Path $PSScriptRoot 'Start-Tomahawk.ps1') @args
