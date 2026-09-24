# Create/refresh the "Daytrade Signal Desk" shortcut (Desktop + Start menu) with the desk icon.
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\Install-DeskShortcut.ps1
# Runs Launch.vbs (hidden, reuses a running desk). Changes no trading settings.
param([string]$Name = 'Daytrade Signal Desk')
$Root = Split-Path -Parent $PSScriptRoot
$Vbs = Join-Path $Root 'Launch.vbs'
$Icon = Join-Path $Root 'static\desk.ico'
if (-not (Test-Path $Vbs)) { Write-Error "Launch.vbs not found under $Root"; exit 1 }
if (-not (Test-Path $Icon)) { Write-Warning "Icon missing at $Icon; using the default icon" }
$shell = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path ([Environment]::GetFolderPath('Desktop')) "$Name.lnk"),
  (Join-Path ([Environment]::GetFolderPath('Programs')) "$Name.lnk")
)
foreach ($path in $targets) {
  $lnk = $shell.CreateShortcut($path)
  $lnk.TargetPath = Join-Path $env:WINDIR 'System32\wscript.exe'
  $lnk.Arguments = "`"$Vbs`""
  $lnk.WorkingDirectory = $Root
  $lnk.Description = 'Start or open the Tomahawk trading desk'
  if (Test-Path $Icon) { $lnk.IconLocation = "$Icon,0" }
  $lnk.Save()
  Write-Host "Shortcut: $path"
}
# Ask Explorer to refresh cached icons so the new one shows immediately.
try { & ie4uinit.exe -show } catch {}
