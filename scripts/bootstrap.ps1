# Clone or update Sighthound Video, then run scripts\build_windows.ps1.
#
# From a checkout:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Package
#
# From an empty directory, after you have this file:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Branch cursor/python3-port-phase3-0735 -Package

[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/whoamib2/SighthoundVideo.git",
    [string]$Branch = "",
    [string]$Destination = "",
    [switch]$Package
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Find-Checkout([string]$Start) {
    if (-not $Start) { return $null }
    $dir = $Start
    while ($dir) {
        if (Test-Path (Join-Path $dir '.git')) { return $dir }
        $parent = Split-Path -Parent $dir
        if (-not $parent -or $parent -eq $dir) { break }
        $dir = $parent
    }
    return $null
}

function Ensure-Git {
    if (Get-Command git -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "git is not on PATH, and winget is missing. Install Git for Windows, then re-run scripts\bootstrap.ps1."
    }
    Write-Step "Installing Git for Windows"
    & winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    $gitCmd = 'C:\Program Files\Git\cmd'
    if (Test-Path (Join-Path $gitCmd 'git.exe')) {
        $env:Path = $gitCmd + ';' + $env:Path
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "Git was not found after winget install. Open a new terminal, or install Git for Windows, then re-run scripts\bootstrap.ps1."
    }
}

Ensure-Git

$root = $null
if ($PSScriptRoot) { $root = Find-Checkout $PSScriptRoot }
if (-not $root) { $root = Find-Checkout (Get-Location).Path }

if (-not $root) {
    if (-not $Destination) {
        $Destination = Join-Path (Get-Location).Path 'SighthoundVideo'
    }
    if (Test-Path (Join-Path $Destination '.git')) {
        $root = (Resolve-Path $Destination).Path
        Write-Host "Using existing clone $root"
    } else {
        Write-Step "Cloning $RepoUrl"
        if ($Branch) {
            & git clone --branch $Branch $RepoUrl $Destination
        } else {
            & git clone $RepoUrl $Destination
        }
        if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)." }
        $root = (Resolve-Path $Destination).Path
    }
} else {
    Write-Host "Using checkout $root"
}

Set-Location -LiteralPath $root
$status = & git status --porcelain
if ($LASTEXITCODE -ne 0) { throw "git status failed." }
if ($status) {
    Write-Host "Working tree has local changes. Building this checkout without pulling."
} else {
    $branchName = (& git rev-parse --abbrev-ref HEAD).Trim()
    Write-Step "Updating $branchName"
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) {
        throw "git pull --ff-only failed. Fix the checkout, then re-run scripts\bootstrap.ps1."
    }
}

$build = Join-Path $root 'scripts\build_windows.ps1'
if (-not (Test-Path $build)) {
    throw "scripts\build_windows.ps1 is not in this checkout ($root)."
}
if ($Package) {
    & $build -Package
} else {
    & $build
}
exit $LASTEXITCODE
