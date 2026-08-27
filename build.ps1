<#
.SYNOPSIS
    Build WlfRyt BadUpdate Prep into a portable single-file .exe

.EXAMPLE
    .\build.ps1
    .\build.ps1 -Clean
    .\build.ps1 -SkipTests
#>
param(
    [switch]$Clean,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$VenvDir   = Join-Path $ScriptDir ".venv"
$DistDir   = Join-Path $ScriptDir "dist"
$BuildDir  = Join-Path $ScriptDir "build"
$SpecFile  = Join-Path $ScriptDir "badupdateprep.spec"
$OutExe    = Join-Path $DistDir "WlfRyt-BadUpdate-Prep.exe"

function Write-Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg) { Write-Host "    OK $msg" -ForegroundColor Green }

function Get-CommandSource([string]$Name) {
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Find-Python {
    $candidates = @(
        (Get-CommandSource "python3"),
        (Get-CommandSource "python")
    ) | Where-Object { $_ -and $_ -notmatch "WindowsApps" }
    foreach ($p in $candidates) {
        $ver = & $p --version 2>&1
        if ($ver -match "Python 3\.(\d+)") { return $p }
    }
    foreach ($name in @("python313", "python312", "python311", "python310", "python")) {
        $scoop = "$env:USERPROFILE\scoop\apps\$name\current\python.exe"
        if (Test-Path $scoop) { return $scoop }
    }
    return $null
}

if ($Clean) {
    Write-Step "Cleaning previous build artifacts"
    foreach ($dir in @($BuildDir, $DistDir)) {
        if (Test-Path $dir) {
            Remove-Item $dir -Recurse -Force
            Write-Ok "Removed $dir"
        }
    }
}

Write-Step "Checking for Python 3"
$PythonExe = Find-Python
if (-not $PythonExe) { throw "Python 3 not found." }
Write-Ok "Using: $PythonExe  ($(& $PythonExe --version 2>&1))"

Write-Step "Setting up virtual environment"
if (-not (Test-Path $VenvDir)) {
    & $PythonExe -m venv $VenvDir
    Write-Ok "Created venv at $VenvDir"
} else {
    Write-Ok "Reusing existing venv at $VenvDir"
}

$VenvPython      = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip         = Join-Path $VenvDir "Scripts\pip.exe"
$VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"

Write-Step "Installing PyInstaller"
& $VenvPip install --quiet --upgrade pip
& $VenvPip install --quiet pyinstaller
Write-Ok "PyInstaller ready"

if (-not $SkipTests) {
    Write-Step "Running unit tests"
    & $VenvPython -m unittest discover -s (Join-Path $ScriptDir "tests") -v
    if ($LASTEXITCODE -ne 0) { throw "Unit tests failed. Build aborted." }
    Write-Ok "All unit tests passed"
}

Write-Step "Stamping build version"
$BuildVersion = Get-Date -Format "yyyy.MMdd.HHmm"
$VersionFile = Join-Path $ScriptDir "badupdateprep\__init__.py"
$OriginalInit = Get-Content $VersionFile -Raw
$NewInit = $OriginalInit -replace '(?m)^APP_VERSION\s*=\s*"[^"]*"', "APP_VERSION = `"$BuildVersion`""
Set-Content -Path $VersionFile -Value $NewInit -NoNewline
Write-Ok "APP_VERSION = `"$BuildVersion`""

Write-Step "Building portable executable"
Push-Location $ScriptDir
try {
    & $VenvPyInstaller $SpecFile --noconfirm
} finally {
    Set-Content -Path $VersionFile -Value $OriginalInit -NoNewline
    Pop-Location
}

Write-Step "Verifying output"
if (Test-Path $OutExe) {
    $size = [math]::Round((Get-Item $OutExe).Length / 1MB, 1)
    Write-Ok "Built successfully: $OutExe  ($size MB)"
    Write-Host ""
    Write-Host "  Copy WlfRyt-BadUpdate-Prep.exe anywhere and run it. No Python install required." -ForegroundColor White
} else {
    throw "Build finished but $OutExe was not found. Check PyInstaller output above."
}
