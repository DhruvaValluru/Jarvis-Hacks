@echo off
setlocal
cd /d "%~dp0"
node -e "if (Number(process.versions.node.split('.')[0]) < 22) process.exit(1)" >nul 2>&1
if errorlevel 1 (
  echo Install Node.js 22 or newer from https://nodejs.org then run this again.
  pause
  exit /b 1
)
if not exist .venv\Scripts\python.exe (
  echo Run setup.bat first to install the Windows MVP.
  pause
  exit /b 1
)
cd telegram
call npm ci
if errorlevel 1 (
  echo Telegram package installation failed.
  pause
  exit /b 1
)
echo Configure windows-mvp\.env using TELEGRAM.md, then run jarvis-telegram.bat.
pause
