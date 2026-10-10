@echo off
setlocal
cd /d "%~dp0"
if not exist telegram\node_modules (
  echo Run setup-telegram.bat first.
  pause
  exit /b 1
)
node telegram\index.mjs
if errorlevel 1 pause
