<#
.SYNOPSIS
  Download the official Node.js for Windows x64 that Alfred's Telegram bridge runs on, verify it, and put
  node.exe (and Node's licence) in build\node.

.DESCRIPTION
  The version is pinned. Keep it equal to NODE_VERSION in .github/workflows/alfred-desktop.yml and the
  node-version in .github/workflows/windows-mvp.yml, so the bundle runs on the Node the tests ran on.
  The zip's SHA-256 must match both the official SHASUMS256.txt published next to it on nodejs.org and
  the hash pinned below (so a file swapped on the server can't slip in). To move to a new 22.x release:
  change -Version and -Sha256 (the "node-v<version>-win-x64.zip" line of that release's SHASUMS256.txt).
  Only node.exe is kept: the bridge needs no npm at run time.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\fetch_node.ps1
#>
param(
  [string]$Version = '22.23.3',
  [string]$Sha256 = '2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71',
  [string]$OutDir = (Join-Path $PSScriptRoot '..\build\node')
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is many times slower with its progress bar

$name = "node-v$Version-win-x64"
$base = "https://nodejs.org/dist/v$Version"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$OutDir = (Resolve-Path $OutDir).Path
$nodeExe = Join-Path $OutDir 'node.exe'

# Already there from an earlier build (the stamp holds the zip's verified hash)?
$stamp = Join-Path $OutDir 'node.sha256'
if ((Test-Path $nodeExe) -and (Test-Path $stamp) -and ((Get-Content -Raw $stamp).Trim() -eq $Sha256)) {
  Write-Host "Node.js v$Version is already in $OutDir (verified earlier)."
  & $nodeExe --version
  exit $LASTEXITCODE
}

$work = Join-Path ([IO.Path]::GetTempPath()) "alfred-node-$Version-$PID"
New-Item -ItemType Directory -Force -Path $work | Out-Null
try {
  $zip = Join-Path $work "$name.zip"
  $sums = Join-Path $work 'SHASUMS256.txt'
  Write-Host "Downloading $base/$name.zip"
  Invoke-WebRequest -Uri "$base/$name.zip" -OutFile $zip -UseBasicParsing
  Write-Host "Downloading $base/SHASUMS256.txt"
  Invoke-WebRequest -Uri "$base/SHASUMS256.txt" -OutFile $sums -UseBasicParsing

  # Lines look like "<sha256>  node-v22.23.3-win-x64.zip".
  $pattern = '^[0-9a-fA-F]{64}\s+' + [regex]::Escape("$name.zip") + '$'
  $entry = Get-Content $sums | Where-Object { $_ -match $pattern } | Select-Object -First 1
  if (-not $entry) { throw "SHASUMS256.txt for v$Version has no line for $name.zip." }
  $official = ($entry -split '\s+')[0].ToLowerInvariant()
  $actual = (Get-FileHash -Algorithm SHA256 -Path $zip).Hash.ToLowerInvariant()
  Write-Host "SHA-256 of $name.zip: $actual"
  Write-Host "SHASUMS256.txt says:  $official"
  Write-Host "Pinned in this script: $($Sha256.ToLowerInvariant())"
  if ($actual -ne $official) { throw "$name.zip doesn't match the official SHASUMS256.txt. Not using it." }
  if ($actual -ne $Sha256.ToLowerInvariant()) {
    throw "$name.zip doesn't match the hash pinned in fetch_node.ps1. If you changed -Version, update -Sha256 too."
  }

  # Take node.exe and the licence straight out of the zip (expanding all of npm takes much longer).
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $archive = [IO.Compression.ZipFile]::OpenRead($zip)
  try {
    foreach ($file in @('node.exe', 'LICENSE')) {
      $item = $archive.GetEntry("$name/$file")
      if (-not $item) { throw "$name.zip has no $name/$file." }
      [IO.Compression.ZipFileExtensions]::ExtractToFile($item, (Join-Path $OutDir $file), $true)
    }
  } finally {
    $archive.Dispose()
  }
  Set-Content -Path $stamp -Value $actual -Encoding ascii
} finally {
  Remove-Item -Recurse -Force -Path $work -ErrorAction SilentlyContinue
}

Write-Host "node.exe is in $OutDir"
& $nodeExe --version
exit $LASTEXITCODE
