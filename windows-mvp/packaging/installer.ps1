<#
.SYNOPSIS
  Compile Alfred's installer, dist\Alfred-Setup-<version>.exe, from dist\Alfred (run build.ps1 first).

.DESCRIPTION
  Needs Inno Setup 6.3 or newer (ISCC.exe). It uses, in this order: -Iscc, build\innosetup\ISCC.exe (the
  version CI builds with, which packaging\fetch_innosetup.ps1 puts there), ISCC.exe on PATH, an installed Inno Setup.
  Before compiling it prepares, in build\:
    - PRIVACY.txt: the data notice (PRIVACY.md) as the text the installer's notice page shows;
    - webview2\MicrosoftEdgeWebview2Setup.exe: Microsoft's Evergreen bootstrapper for the Edge WebView2
      Runtime, which the installer offers when a PC doesn't have it. Its Authenticode signature must be
      valid and Microsoft's. With -SkipWebView2 the installer only explains how to get the runtime.
  The installer isn't code-signed: Windows SmartScreen shows "unknown publisher" for it (see INSTALL.md).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\installer.ps1
#>
param(
  [string]$Iscc = '',
  [switch]$SkipWebView2
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'

$Mvp = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Build = Join-Path $Mvp 'build'
$Dist = Join-Path $Mvp 'dist'
$App = Join-Path $Dist 'Alfred'
$WebView2Url = 'https://go.microsoft.com/fwlink/p/?LinkId=2124703'  # Microsoft's Evergreen bootstrapper

function Find-Iscc {
  if ($Iscc) { return $Iscc }
  $pinned = Join-Path $Build 'innosetup\ISCC.exe'
  if (Test-Path $pinned) { return $pinned }
  $onPath = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  $roots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles, (Join-Path $env:LOCALAPPDATA 'Programs')) | Where-Object { $_ }
  foreach ($root in $roots) {
    foreach ($name in @('Inno Setup 6', 'Inno Setup 7')) {
      $candidate = Join-Path $root "$name\ISCC.exe"
      if (Test-Path $candidate) { return $candidate }
    }
  }
  throw 'Inno Setup (ISCC.exe) not found. Run packaging\fetch_innosetup.ps1 (the version CI uses), install it from https://jrsoftware.org/isinfo.php, or pass -Iscc <path to ISCC.exe>.'
}

if (-not (Test-Path (Join-Path $App 'Alfred.exe'))) { throw "No $App\Alfred.exe: run packaging\build.ps1 first." }
foreach ($art in @('alfred.ico', 'wizard-large-100.bmp', 'wizard-small-100.bmp')) {
  if (-not (Test-Path (Join-Path $Build $art))) { throw "No build\${art}: run packaging\build.ps1 first (make_icon.py draws it)." }
}

# The version comes from jarvis\__init__.py, like the exes' version resources (alfred.spec).
$init = Get-Content -Raw -Path (Join-Path $Mvp 'jarvis\__init__.py')
if ($init -notmatch '__version__ = "([^"]+)"') { throw 'No __version__ in jarvis\__init__.py.' }
$Version = $Matches[1]
Write-Host "Alfred $Version"

# The notice page shows plain text: drop Markdown's ** and backticks, use Windows line ends and a UTF-8 BOM
# (without a BOM, Inno Setup would read the file in the PC's ANSI code page).
$notice = Get-Content -Raw -Encoding UTF8 -Path (Join-Path $Mvp 'PRIVACY.md')
$notice = ($notice -replace '\*\*', '' -replace '`', '') -replace "`r?`n", "`r`n"
$privacyTxt = Join-Path $Build 'PRIVACY.txt'
[IO.File]::WriteAllText($privacyTxt, $notice, (New-Object System.Text.UTF8Encoding $true))

$defines = @("/DAppVersion=$Version", "/DDistDir=$App", "/DBuildDir=$Build", "/DOutputDir=$Dist")
if (-not $SkipWebView2) {
  $bootstrapper = Join-Path $Build 'webview2\MicrosoftEdgeWebview2Setup.exe'
  New-Item -ItemType Directory -Force -Path (Split-Path $bootstrapper) | Out-Null
  Write-Host "Downloading the WebView2 Runtime bootstrapper: $WebView2Url"
  Invoke-WebRequest -Uri $WebView2Url -OutFile $bootstrapper -UseBasicParsing
  $signature = Get-AuthenticodeSignature -FilePath $bootstrapper
  $signer = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '(none)' }
  Write-Host "Signature: $($signature.Status), signed by $signer"
  if ($signature.Status -ne 'Valid' -or $signer -notmatch 'O=Microsoft Corporation') {
    throw 'The WebView2 bootstrapper is not validly signed by Microsoft. Not bundling it.'
  }
  $defines += "/DWebView2Setup=$bootstrapper"
} else {
  Write-Host 'Without the WebView2 bootstrapper (-SkipWebView2).'
}

$compiler = Find-Iscc
Write-Host "Inno Setup: $compiler"
Write-Host "ISCC $($defines -join ' ') packaging\alfred.iss"
$global:LASTEXITCODE = 0
& $compiler @defines (Join-Path $PSScriptRoot 'alfred.iss')
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed (exit code $LASTEXITCODE)." }

$setup = Join-Path $Dist "Alfred-Setup-$Version.exe"
if (-not (Test-Path $setup)) { throw "Inno Setup didn't make $setup." }
$hash = (Get-FileHash -Algorithm SHA256 -Path $setup).Hash.ToLowerInvariant()
Write-Host ('{0}  {1:N1} MB  SHA-256 {2}' -f $setup, ((Get-Item $setup).Length / 1MB), $hash)
