@echo off
powershell.exe -NoProfile -File "%~dp0Start-Tomahawk.ps1" %*
exit /b %errorlevel%
