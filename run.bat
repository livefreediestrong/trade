@echo off
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat (
  call .venv\Scripts\activate.bat
) else (
  echo Run Launch.bat first to create the .venv.
  pause
  exit /b 1
)
python app.py
