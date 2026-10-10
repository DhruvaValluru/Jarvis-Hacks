@echo off
setlocal
cd /d "%~dp0"
rem Usage: setup.bat        (interactive)
rem        setup.bat /ci    (no prompts; used by CI and the Sandbox test)
set "CI_MODE="
if /i "%~1"=="/ci" set "CI_MODE=1"
echo === Jarvis setup (test spec: Python 3.12 + requirements.lock.txt) ===

set "PY="
py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY ( python -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo Python 3.12 not found. Installing it with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  echo.
  echo Python 3.12 installed. Close this window and run setup.bat again.
  if not defined CI_MODE pause
  exit /b 1
)
echo Using %PY%

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -c "import sys; assert sys.version_info[:2] == (3,12)" >nul 2>&1 || rmdir /s /q .venv
)
if not exist .venv (
  %PY% -m venv .venv || ( echo Could not create the virtual environment. & exit /b 1 )
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing the exact package versions from requirements.lock.txt...
pip install -r requirements.lock.txt
if errorlevel 1 (
  echo Package install failed.
  if not defined CI_MODE pause
  exit /b 1
)

if not exist .env copy .env.example .env >nul
if not defined CI_MODE (
  findstr /r "^ANTHROPIC_API_KEY=." .env >nul || set /p KEY="Paste your Anthropic API key (or press Enter to skip): "
)
if defined KEY (
  powershell -NoProfile -Command "(Get-Content .env) -replace '^ANTHROPIC_API_KEY=.*', 'ANTHROPIC_API_KEY=%KEY%' | Set-Content .env"
)
echo.
python -m jarvis --doctor
echo.
echo Done. Start Jarvis with jarvis.bat, run the tests with test.bat
if not defined CI_MODE pause
exit /b 0
