@echo off
setlocal
cd /d "%~dp0"
if not exist telegram\node_modules (
  echo Run setup-telegram.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import sys; from jarvis import consent; sys.exit(0 if consent.ensure(True) else 1)"
if errorlevel 1 (
  pause
  exit /b 1
)
node telegram\index.mjs
if errorlevel 1 pause
