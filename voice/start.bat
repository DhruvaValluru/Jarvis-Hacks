@echo off
rem Windows: installs what's needed the first time, starts Jarvis and opens the page.
cd /d "%~dp0"
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up, this takes a minute...
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
)
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" server.py --open
pause
goto :eof

:nopython
echo.
echo Python 3.10 or newer is needed. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then double-click start.bat again.
pause
goto :eof

:failed
echo.
echo Installing the requirements failed. See the messages above.
pause
