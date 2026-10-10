@echo off
setlocal
cd /d "%~dp0"
echo === Jarvis setup ===

set "PY="
for %%V in (3.12 3.13 3.11 3.10) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V" )
)
if not defined PY ( python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>&1 && set "PY=python" )
if not defined PY (
  echo Python 3.10+ not found. Installing Python 3.12 with winget...
  winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
  echo.
  echo Python installed. Close this window and run setup.bat again.
  pause
  exit /b 1
)
echo Using %PY%

if not exist .venv (
  %PY% -m venv .venv || ( echo Could not create the virtual environment. & pause & exit /b 1 )
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing core packages...
pip install -r requirements.txt || ( echo Core install failed. & pause & exit /b 1 )
echo Installing voice packages (speech-to-text)...
pip install -r requirements-voice.txt || echo WARNING: voice packages failed; Jarvis will run in typing mode.

if not exist .env (
  copy .env.example .env >nul
  set /p KEY="Paste your Anthropic API key (or press Enter to skip): "
)
if defined KEY (
  powershell -NoProfile -Command "(Get-Content .env) -replace '^ANTHROPIC_API_KEY=.*', 'ANTHROPIC_API_KEY=%KEY%' | Set-Content .env"
)
echo.
echo Done. Start Jarvis with:  jarvis.bat
pause
