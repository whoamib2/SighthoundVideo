# Clone or update VigilCam, then run scripts\build_windows.ps1.
# The GitHub repository name is still SighthoundVideo.
#
# From a checkout:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\bootstrap.ps1 -Package
#
# From an empty directory, after you have this file:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Branch cursor/python3-port-phase1-0735 -Package

[CmdletBinding()]
param(
    [string]$RepoUrl = "https://github.com/whoamib2/SighthoundVideo.git",
    [string]$Branch = "",
    [string]$Destination = "",
    [switch]$Package
)

# Stop applies to cmdlets. Native stderr must not stop the script: Windows
# PowerShell 5.1 turns it into a terminating NativeCommandError, and PowerShell
# 7 does the same for a non-zero exit when PSNativeCommandUseErrorActionPreference
# is true. Invoke-Native turns that off for each native command.
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
    $global:PSNativeCommandUseErrorActionPreference = $false
}
$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Command,
        [switch]$Quiet
    )
    # Local and global: 5.1 promotes native stderr using the preference it
    # finds, and a scriptblock inherits the preference of the function that
    # invokes it. PowerShell 7's native-exit preference is turned off too.
    $previousLocal = $ErrorActionPreference
    $previousGlobal = $global:ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $global:ErrorActionPreference = 'Continue'
    $restoreNative = $false
    $previousNative = $false
    if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
        $restoreNative = $true
        $previousNative = $PSNativeCommandUseErrorActionPreference
        $PSNativeCommandUseErrorActionPreference = $false
        $global:PSNativeCommandUseErrorActionPreference = $false
    }
    $global:LASTEXITCODE = 0
    $code = 0
    try {
        & $Command 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                if (-not $Quiet) { Write-Host $_.ToString() }
            } else {
                $_
            }
        }
        $code = $global:LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousLocal
        $global:ErrorActionPreference = $previousGlobal
        if ($restoreNative) {
            $PSNativeCommandUseErrorActionPreference = $previousNative
            $global:PSNativeCommandUseErrorActionPreference = $previousNative
        }
    }
    $global:LASTEXITCODE = $code
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
    Invoke-Native { & winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements --disable-interactivity }
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
            Invoke-Native { & git clone --branch $Branch $RepoUrl $Destination }
        } else {
            Invoke-Native { & git clone $RepoUrl $Destination }
        }
        if ($LASTEXITCODE -ne 0) { throw "git clone failed (exit $LASTEXITCODE)." }
        $root = (Resolve-Path $Destination).Path
    }
} else {
    Write-Host "Using checkout $root"
}

Set-Location -LiteralPath $root
$status = Invoke-Native { & git status --porcelain }
if ($LASTEXITCODE -ne 0) { throw "git status failed." }
if ($status) {
    Write-Host "Working tree has local changes. Building this checkout without pulling."
} else {
    $rev = Invoke-Native { & git rev-parse --abbrev-ref HEAD }
    if ($LASTEXITCODE -ne 0) { throw "git rev-parse failed." }
    $branchName = ($rev | Select-Object -Last 1).ToString().Trim()
    Write-Step "Updating $branchName"
    Invoke-Native { & git pull --ff-only }
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
