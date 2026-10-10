@echo off
rem Runs setup + all tests inside Windows Sandbox: a brand-new, identical Windows every time.
rem Needs Windows 10/11 Pro or Enterprise with the "Windows Sandbox" feature turned on.
setlocal
set "MVP=%~dp0.."
for %%I in ("%MVP%") do set "MVP=%%~fI"
if not exist "%~dp0results" mkdir "%~dp0results"
set "WSB=%TEMP%\jarvis-test.wsb"
(
  echo ^<Configuration^>
  echo   ^<MappedFolders^>
  echo     ^<MappedFolder^>^<HostFolder^>%MVP%^</HostFolder^>^<SandboxFolder^>C:\mvp^</SandboxFolder^>^<ReadOnly^>true^</ReadOnly^>^</MappedFolder^>
  echo     ^<MappedFolder^>^<HostFolder^>%~dp0results^</HostFolder^>^<SandboxFolder^>C:\results^</SandboxFolder^>^<ReadOnly^>false^</ReadOnly^>^</MappedFolder^>
  echo   ^</MappedFolders^>
  echo   ^<LogonCommand^>^<Command^>powershell -NoExit -ExecutionPolicy Bypass -File C:\mvp\sandbox\inside-sandbox.ps1^</Command^>^</LogonCommand^>
  echo ^</Configuration^>
) > "%WSB%"
echo Starting Windows Sandbox. Results will appear in %~dp0results
start "" "%WSB%"
