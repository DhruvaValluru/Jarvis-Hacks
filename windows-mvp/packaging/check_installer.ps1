<#
.SYNOPSIS
  Check Alfred's installer end to end: install it silently into a temporary folder, run the installed
  alfred-cli.exe --selftest, then uninstall it silently and check it's gone. CI runs this; you can too.

.DESCRIPTION
  The installer is per-user, so this installs for the account running it, into -InstallDir (a fresh
  folder under %TEMP% by default). It refuses to run where Alfred is already installed. It does not
  remove your Alfred settings or keys: the uninstall runs without /REMOVEUSERDATA, and this checks
  they're kept. The installer's and uninstaller's logs go to -LogDir and are printed when a step fails.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\check_installer.ps1
#>
param(
  [string]$Setup = '',
  [string]$InstallDir = (Join-Path ([IO.Path]::GetTempPath()) 'AlfredInstallCheck'),
  [string]$LogDir = $(if ($env:RUNNER_TEMP) { $env:RUNNER_TEMP } else { [IO.Path]::GetTempPath() })
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Mvp = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $Setup) {
  $found = Get-ChildItem -Path (Join-Path $Mvp 'dist') -Filter 'Alfred-Setup-*.exe' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if (-not $found) { throw 'No dist\Alfred-Setup-*.exe: run packaging\installer.ps1 first, or pass -Setup.' }
  $Setup = $found.FullName
}
$installLog = Join-Path $LogDir 'alfred-install.log'
$uninstallLog = Join-Path $LogDir 'alfred-uninstall.log'
$shortcut = Join-Path ([Environment]::GetFolderPath('Programs')) 'Alfred.lnk'
$userData = Join-Path $env:LOCALAPPDATA 'Jarvis'
$marker = Join-Path $userData 'installer-check.txt'

function Step([string]$Text) { Write-Host ''; Write-Host "==> $Text" -ForegroundColor Cyan }

function Show-Log([string]$Path) {
  if (Test-Path $Path) { Write-Host "----- $Path"; Get-Content $Path | Write-Host; Write-Host '-----' }
}

function Get-UninstallEntry {
  Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' -ErrorAction SilentlyContinue |
    Get-ItemProperty | Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -eq 'Alfred' }
}

function Assert([bool]$Ok, [string]$Text) {
  if ($Ok) { Write-Host "[ok] $Text" } else { throw "FAILED: $Text" }
}

Step "Install $Setup silently into $InstallDir"
# Every Alfred install shares one uninstall entry (the installer's AppId), so this check would take over
# and then remove an Alfred you use. Run it on a PC without Alfred (CI, Windows Sandbox).
if (Get-UninstallEntry) { throw 'Alfred is installed on this PC. Uninstall it first, or run this check in Windows Sandbox.' }
if (Test-Path $InstallDir) { Remove-Item -Recurse -Force -Path $InstallDir }
$userDataWasNew = -not (Test-Path $userData)
New-Item -ItemType Directory -Force -Path $userData | Out-Null
Set-Content -Path $marker -Value 'Left by packaging\check_installer.ps1: the uninstaller must keep this.'
# One argument string: Start-Process doesn't quote list items that contain spaces.
$arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /DIR="{0}" /LOG="{1}"' -f $InstallDir, $installLog
$process = Start-Process -FilePath $Setup -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -ne 0) { Show-Log $installLog; throw "The installer failed (exit code $($process.ExitCode))." }
Write-Host "Installer exit code 0, log: $installLog"

foreach ($file in @('Alfred.exe', 'alfred-cli.exe', 'unins000.exe', 'node\node.exe', 'telegram\index.mjs', '_internal\PRIVACY.md')) {
  Assert (Test-Path (Join-Path $InstallDir $file)) "installed $file"
}
Assert (Test-Path $shortcut) "Start menu shortcut $shortcut"
$entry = Get-UninstallEntry
Assert ($null -ne $entry) 'registered in Apps & features (HKCU uninstall entry "Alfred")'
Write-Host "    $($entry.DisplayName) $($entry.DisplayVersion): $($entry.UninstallString)"

Step 'Self-test of the installed copy'
$global:LASTEXITCODE = 0
& (Join-Path $InstallDir 'alfred-cli.exe') --selftest
if ($LASTEXITCODE -ne 0) { throw "The installed alfred-cli.exe --selftest failed (exit code $LASTEXITCODE)." }

Step 'Speech model download, as the setup runs it (tiny.en, into a temporary folder)'
# On a real PC the setup's download finished but its process never exited, so the setup span forever.
# The setup starts exactly this command (jarvis/onboarding/speech.py: download_command).
$models = Join-Path ([IO.Path]::GetTempPath()) ("AlfredModelCheck-" + [guid]::NewGuid().ToString('N'))
$cli = Join-Path $InstallDir 'alfred-cli.exe'
$out = Join-Path $LogDir 'alfred-model-download.log'
$env:HF_HUB_DISABLE_XET = '1'; $env:HF_HUB_DISABLE_PROGRESS_BARS = '1'
$download = Start-Process -FilePath $cli -ArgumentList ('--fetch-speech-model tiny.en "{0}"' -f $models) `
  -RedirectStandardOutput $out -NoNewWindow -PassThru
$null = $download.Handle  # keep the handle, so ExitCode is readable once it ends
if (-not $download.WaitForExit(300000)) {
  $download.Kill(); Show-Log $out
  throw 'The speech model download did not exit within 5 minutes (the setup would spin forever).'
}
Show-Log $out
$said = Get-Content $out -Raw
if ($download.ExitCode -ne 0 -or $said -notmatch '"type": "done"') {
  throw "The speech model download failed (exit code $($download.ExitCode))."
}
$bin = Get-ChildItem -Path $models -Recurse -Filter 'model.bin' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $bin) { throw "The download said done, but there's no model.bin under $models." }
Write-Host "[ok] speech model downloaded ($($bin.FullName))"
Remove-Item -Recurse -Force -Path $models -ErrorAction SilentlyContinue
Remove-Item Env:HF_HUB_DISABLE_XET, Env:HF_HUB_DISABLE_PROGRESS_BARS -ErrorAction SilentlyContinue

Step 'Uninstall silently'
$uninstaller = Join-Path $InstallDir 'unins000.exe'
$arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG="{0}"' -f $uninstallLog
$process = Start-Process -FilePath $uninstaller -ArgumentList $arguments -Wait -PassThru
$exitCode = $process.ExitCode
Write-Host "Uninstaller exit code: $(if ($null -eq $exitCode) { 'none reported' } else { $exitCode })"
# unins000.exe hands over to a copy of itself in %TEMP%, which does the work, writes the log and then
# removes unins000.exe. Its log is what says how it went: wait for the copy to close it.
function Test-UninstallDone {
  (-not (Test-Path $uninstaller)) -and (Test-Path $uninstallLog) -and
    [bool](Select-String -Path $uninstallLog -SimpleMatch 'Log closed.' -Quiet)
}
$deadline = (Get-Date).AddMinutes(3)
while (-not (Test-UninstallDone) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }
$succeeded = (Test-Path $uninstallLog) -and
  [bool](Select-String -Path $uninstallLog -SimpleMatch 'Uninstallation process succeeded.' -Quiet)
if (-not (Test-UninstallDone) -or -not $succeeded) {
  Show-Log $uninstallLog
  throw "The uninstaller did not finish (unins000.exe still there: $(Test-Path $uninstaller); log says it succeeded: $succeeded)."
}
Write-Host '[ok] the uninstaller says: Uninstallation process succeeded.'
# Only the first half of the uninstaller exits on its own; the exit code it leaves isn't the uninstall's result.
if ($exitCode -ne 0) { Write-Host "    (unins000.exe's own exit code was $exitCode; the log above is the result.)" }

Assert (-not (Test-Path (Join-Path $InstallDir 'Alfred.exe'))) 'Alfred.exe removed'
Assert (-not (Test-Path (Join-Path $InstallDir '_internal'))) '_internal removed'
Assert (-not (Test-Path $shortcut)) 'Start menu shortcut removed'
Assert ($null -eq (Get-UninstallEntry)) 'Apps & features entry removed'
Assert (Test-Path $marker) "your Alfred data folder kept ($userData)"
if (Test-Path $InstallDir) {
  $left = @(Get-ChildItem -Path $InstallDir -Recurse -Force)
  Write-Host "Left in $InstallDir ($($left.Count) items):"
  $left | ForEach-Object { Write-Host "    $($_.FullName)" }
}

Remove-Item -Force -Path $marker
if ($userDataWasNew -and -not (Get-ChildItem -Path $userData -Force)) { Remove-Item -Force -Path $userData }
Write-Host ''
Write-Host 'Installer check passed.' -ForegroundColor Green
