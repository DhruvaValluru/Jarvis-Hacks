# Runs inside Windows Sandbox. Installs the exact spec Python, copies the code, runs setup + tests.
$ErrorActionPreference = "Stop"
$PythonVersion = "3.12.10"   # exact interpreter for the test spec
$log = "C:\results\sandbox-$(Get-Date -Format yyyyMMdd-HHmmss).txt"
Start-Transcript -Path $log
Write-Host "Installing Python $PythonVersion..."
$installer = "$env:TEMP\python.exe"
Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe" -OutFile $installer
Start-Process $installer -Wait -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1 Include_test=0"
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine")
robocopy C:\mvp C:\jarvis /E /XD .venv __pycache__ results /XF .env | Out-Null
Set-Location C:\jarvis
cmd /c "setup.bat /ci"
cmd /c "test.bat"
Write-Host "Exit code: $LASTEXITCODE. Log saved to $log"
Stop-Transcript
