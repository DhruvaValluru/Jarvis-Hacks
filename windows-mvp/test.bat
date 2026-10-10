@echo off
setlocal
cd /d "%~dp0"
rem The one test command everyone runs. Same settings for every person, agent and CI.
if not exist .venv\Scripts\python.exe ( echo Run setup.bat first. & exit /b 1 )
set "PY=.venv\Scripts\python.exe"
set JARVIS_MODEL=claude-opus-5-5
set JARVIS_EFFORT=low
set JARVIS_WHISPER_MODEL=base.en
set JARVIS_FALLBACKS=1
set JARVIS_SPEAK=0
set JARVIS_COMPUTER_USE=1
set JARVIS_MAX_STEPS=40
rem Tests never read this PC's saved Alfred settings or Credential Manager secrets.
set JARVIS_IGNORE_USER_SETTINGS=1
set FAILED=0

echo === 1/6 Environment check ===
%PY% -m jarvis --doctor || ( echo Your setup does not match the spec. Run setup.bat, then test again. & exit /b 1 )
echo.
echo === 2/6 Unit tests (everything in tests\: core, computer use, Telegram worker, multi-PC, settings, app) ===
%PY% -m pytest -q tests || set FAILED=1
echo.
echo === 3/6 Windows smoke test (Notepad via UI Automation) ===
%PY% tests\smoke_windows.py || set FAILED=1
echo.
echo === 4/6 Voice pipeline (offline) ===
%PY% tests\voice_pipeline.py || set FAILED=1
echo.
echo === 5/6 Telegram bridge (Node) ===
if exist telegram\node_modules (
  pushd telegram
  call npm test || set FAILED=1
  popd
) else (
  echo SKIPPED: run setup-telegram.bat to include it. CI always runs it.
)
echo.
echo === 6/6 App self-test (imports, bundled files, engine start, Stop; nothing on the PC changes) ===
%PY% -m jarvis.selftest || set FAILED=1
echo.
if "%FAILED%"=="1" ( echo RESULT: FAILED & exit /b 1 )
echo RESULT: ALL PASSED
exit /b 0
