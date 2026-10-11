<#
.SYNOPSIS
  Build Alfred's app folder, dist\Alfred: Alfred.exe, alfred-cli.exe, their shared _internal folder,
  node\node.exe and the Telegram bridge (telegram\). With -Installer, also the installer (fetch_innosetup.ps1,
  then installer.ps1).

.DESCRIPTION
  Needs Windows x64, Python 3.12 with requirements.lock.txt and packaging\requirements-build.txt installed,
  and Node.js 22 with npm (for the bridge's packages). Steps:
    1. check the Python packages match the test spec (python -m jarvis --doctor)
    2. draw the icon and installer art (make_icon.py)
    3. generate comtypes' wrappers for Windows' UI Automation type library, so the bundle carries them
    4. PyInstaller (alfred.spec) -> dist\Alfred
    5. the official node.exe, verified (fetch_node.ps1) -> dist\Alfred\node
    6. the bridge's *.mjs, package.json and its production node_modules -> dist\Alfred\telegram
  Then run dist\Alfred\alfred-cli.exe --selftest.

.EXAMPLE
  cd windows-mvp
  .venv\Scripts\python -m pip install -r requirements.lock.txt -r packaging\requirements-build.txt
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Python .venv\Scripts\python.exe -Installer
#>
param(
  [string]$Python = 'python',
  [string]$Npm = 'npm',
  [switch]$SkipDoctor,
  [switch]$Installer
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Mvp = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Build = Join-Path $Mvp 'build'
$Dist = Join-Path $Mvp 'dist'
$App = Join-Path $Dist 'Alfred'

function Invoke-Native([string]$What, [scriptblock]$Command) {
  # Run a program and stop the build if it fails: PowerShell doesn't stop on a failing program by itself.
  Write-Host ''
  Write-Host "==> $What" -ForegroundColor Cyan
  $global:LASTEXITCODE = 0
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

Push-Location $Mvp
try {
  $started = Get-Date
  New-Item -ItemType Directory -Force -Path $Build | Out-Null

  Invoke-Native 'Python and PyInstaller' {
    & $Python -c "import sys, PyInstaller; print(sys.executable); print('Python', sys.version); print('PyInstaller', PyInstaller.__version__)"
  }
  if (-not $SkipDoctor) {
    Invoke-Native 'Packages match the test spec (python -m jarvis --doctor)' { & $Python -m jarvis --doctor }
  }
  Invoke-Native 'Icon and installer art (make_icon.py)' { & $Python (Join-Path $PSScriptRoot 'make_icon.py') $Build }

  # uiautomation asks comtypes for a wrapper of UIAutomationCore.dll's type library. Generated here into
  # site-packages\comtypes\gen, alfred.spec bundles it, so the app never generates code on the user's PC.
  Invoke-Native 'COM wrappers for UI Automation (comtypes.gen)' {
    & $Python -c "import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); import comtypes.gen.UIAutomationClient as m; print(m.__file__)"
  }

  Invoke-Native 'PyInstaller: dist\Alfred' {
    & $Python -m PyInstaller --noconfirm --clean --distpath $Dist --workpath (Join-Path $Build 'pyinstaller') (Join-Path $PSScriptRoot 'alfred.spec')
  }
  $warnings = Get-ChildItem -Path (Join-Path $Build 'pyinstaller') -Recurse -Filter 'warn-*.txt' -ErrorAction SilentlyContinue
  foreach ($file in $warnings) { Write-Host "PyInstaller's list of modules it couldn't find: $($file.FullName)" }

  Invoke-Native 'Node.js (fetch_node.ps1)' { & (Join-Path $PSScriptRoot 'fetch_node.ps1') -OutDir (Join-Path $Build 'node') }

  # The bridge's runtime packages, installed from its lock file in a staging folder (the source tree's own
  # node_modules may hold dev tools), then trimmed of what Node never reads: source maps, type stubs, docs.
  $stage = Join-Path $Build 'telegram'
  if (Test-Path $stage) { Remove-Item -Recurse -Force -Path $stage }
  New-Item -ItemType Directory -Force -Path $stage | Out-Null
  Copy-Item -Path (Join-Path $Mvp 'telegram\*.mjs'), (Join-Path $Mvp 'telegram\package.json'), (Join-Path $Mvp 'telegram\package-lock.json') -Destination $stage
  Push-Location $stage
  try {
    Invoke-Native 'Telegram bridge packages (npm ci --omit=dev)' { & $Npm ci --omit=dev --no-audit --no-fund }
  } finally {
    Pop-Location
  }
  $junk = @(Get-ChildItem -Path (Join-Path $stage 'node_modules') -Recurse -File | Where-Object {
      $_.Name -match '\.(map|d\.ts|d\.mts|d\.cts)$' -or ($_.Extension -eq '.md' -and $_.Name -notmatch '^(licen[cs]e|notice|copying)')
    })
  $junk | Remove-Item -Force
  Write-Host "Removed $($junk.Count) source maps, type stubs and docs from the bridge's node_modules."

  Write-Host ''
  Write-Host '==> Node.js and the Telegram bridge -> dist\Alfred' -ForegroundColor Cyan
  if (-not (Test-Path (Join-Path $App 'alfred-cli.exe'))) { throw "PyInstaller didn't make $App\alfred-cli.exe." }
  $nodeDir = Join-Path $App 'node'
  $bridgeDir = Join-Path $App 'telegram'
  New-Item -ItemType Directory -Force -Path $nodeDir, $bridgeDir | Out-Null
  Copy-Item -Path (Join-Path $Build 'node\node.exe'), (Join-Path $Build 'node\LICENSE') -Destination $nodeDir
  Copy-Item -Path (Join-Path $stage '*.mjs'), (Join-Path $stage 'package.json') -Destination $bridgeDir
  Copy-Item -Path (Join-Path $stage 'node_modules') -Destination $bridgeDir -Recurse

  # A summary that helps when something is missing or the bundle suddenly grows.
  Write-Host ''
  Write-Host "==> $App" -ForegroundColor Cyan
  Get-ChildItem -Path $App | Format-Table -AutoSize Mode, LastWriteTime, Length, Name | Out-String | Write-Host
  foreach ($part in @('_internal', 'node', 'telegram')) {
    $files = @(Get-ChildItem -Path (Join-Path $App $part) -Recurse -File)
    $mb = ($files | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Host ('{0,-10} {1,6} files {2,8:N1} MB' -f $part, $files.Count, $mb)
  }
  $longest = Get-ChildItem -Path $App -Recurse -File | ForEach-Object { $_.FullName.Substring($App.Length) } |
    Sort-Object Length -Descending | Select-Object -First 1
  Write-Host "Longest path inside the app folder ($($longest.Length) characters): $longest"
  Write-Host ('Built in {0:N0} s. Next: {1}\alfred-cli.exe --selftest' -f ((Get-Date) - $started).TotalSeconds, $App)

  if ($Installer) {
    & (Join-Path $PSScriptRoot 'fetch_innosetup.ps1')  # the Inno Setup version CI uses, in build\innosetup
    & (Join-Path $PSScriptRoot 'installer.ps1')
  }
} finally {
  Pop-Location
}
