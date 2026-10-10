@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-Tomahawk.ps1" %*
exit /b %errorlevel%
