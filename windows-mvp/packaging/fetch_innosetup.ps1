<#
.SYNOPSIS
  Get the pinned Inno Setup that compiles Alfred's installer: download the official Inno Setup installer,
  verify it, and unpack Inno Setup in portable mode into build\innosetup (ISCC.exe).

.DESCRIPTION
  CI runs this, so every build compiles packaging\alfred.iss with the same Inno Setup, whatever the runner
  image has. The file comes from the release on Inno Setup's GitHub (github.com/jrsoftware/issrc), and its
  SHA-256 must match the hash pinned below. Portable mode needs no administrator rights and adds nothing to
  the PC besides the folder: no Start menu entries, no .iss file association, no uninstall entry.
  installer.ps1 uses build\innosetup\ISCC.exe when it's there. To move to a newer release, change -Version
  and -Sha256 together (Get-FileHash of the new innosetup-<version>.exe) and check the installer CI builds.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\fetch_innosetup.ps1
#>
param(
  [string]$Version = '6.7.3',
  [string]$Sha256 = '9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732',
  [string]$OutDir = (Join-Path $PSScriptRoot '..\build\innosetup')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its progress bar

$OutDir = [IO.Path]::GetFullPath($OutDir)
$iscc = Join-Path $OutDir 'ISCC.exe'
$stamp = Join-Path $OutDir 'innosetup.sha256'

# Already there from an earlier build (the stamp holds the verified installer's hash)?
if ((Test-Path $iscc) -and (Test-Path $stamp) -and ((Get-Content -Raw $stamp).Trim() -eq $Sha256)) {
  Write-Host "Inno Setup $Version is already in $OutDir (verified earlier)."
  exit 0
}
if (Test-Path $OutDir) { Remove-Item -Recurse -Force -Path $OutDir }  # another version, or unfinished

$name = "innosetup-$Version.exe"
$url = "https://github.com/jrsoftware/issrc/releases/download/is-$($Version -replace '\.', '_')/$name"
$work = Join-Path ([IO.Path]::GetTempPath()) "alfred-innosetup-$Version-$PID"
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
  $setup = Join-Path $work $name
  Write-Host "Downloading $url"
  Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing
  $actual = (Get-FileHash -Algorithm SHA256 -Path $setup).Hash.ToLowerInvariant()
  Write-Host "SHA-256 of ${name}: $actual"
  Write-Host "Pinned in this script: $($Sha256.ToLowerInvariant())"
  if ($actual -ne $Sha256.ToLowerInvariant()) {
    throw "$name doesn't match the hash pinned in fetch_innosetup.ps1. If you changed -Version, update -Sha256 too."
  }

  # Inno Setup's own installer: /PORTABLE=1 unpacks it without registering anything; /CURRENTUSER needs no admin.
  $log = Join-Path $work 'innosetup-setup.log'
  $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER', '/PORTABLE=1', '/NOICONS',
    "/DIR=`"$OutDir`"", "/LOG=`"$log`"")
  Write-Host "Unpacking Inno Setup $Version (portable) into $OutDir"
  $process = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru
  if ($process.ExitCode -ne 0 -or -not (Test-Path $iscc)) {
    if (Test-Path $log) { Get-Content $log | Write-Host }
    throw "Inno Setup's installer failed (exit code $($process.ExitCode)) or left no $iscc."
  }
  Set-Content -Path $stamp -Value $actual -Encoding ascii
} finally {
  Remove-Item -Recurse -Force -Path $work -ErrorAction SilentlyContinue
}

Write-Host "Inno Setup $Version is in $OutDir"
