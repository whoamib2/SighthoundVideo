# Build and test Sighthound Video on Windows, and optionally make an installer.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Package
#
# The script is safe to re-run. It reuses .venv, skips model downloads that
# are already on disk, and overwrites dist\SighthoundVideo-Setup.exe.
# onnxruntime-directml is installed for the venv and the packaged app. The
# process uses DirectML when that provider loads, and CPU otherwise.

[CmdletBinding()]
param(
    [switch]$Package
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$script:NativeStatus = 'not started'
$script:InstallerPath = ''

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Tool([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Step $Label
    $global:LASTEXITCODE = 0
    & $Action
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed (exit $LASTEXITCODE)."
    }
}

function Install-WithWinget {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Verify
    )
    if (& $Verify) {
        Write-Host "$Label is already installed."
        return $true
    }
    if (-not (Test-Tool 'winget')) {
        Write-Host "winget is not available, so $Label was not installed." -ForegroundColor Yellow
        Write-Host "Install App Installer (winget), or install $Label yourself, then re-run this script."
        return $false
    }
    Write-Step "Installing $Label ($Id)"
    & winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity
    if (& $Verify) {
        Write-Host "$Label is ready."
        return $true
    }
    Write-Host "winget could not make $Label available (exit $LASTEXITCODE)." -ForegroundColor Yellow
    return $false
}

function Find-PythonExecutable {
    if (Test-Tool 'py') {
        $listed = & py -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $listed) {
            $exe = ($listed | Select-Object -Last 1).ToString().Trim()
            if ($exe -and (Test-Path $exe)) {
                & $exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
                if ($LASTEXITCODE -eq 0) { return $exe }
            }
        }
    }
    if (Test-Tool 'python') {
        $cmd = Get-Command python
        & $cmd.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
        if ($LASTEXITCODE -eq 0) { return $cmd.Source }
    }
    $candidates = @(
        (Join-Path $env:LocalAppData 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) {
            & $path -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -eq 0) { return $path }
        }
    }
    return $null
}

function Find-Msvc2022 {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { return $null }
    $install = & $vswhere -latest -version "[17.0,18.0)" -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if ($LASTEXITCODE -ne 0 -or -not $install) { return $null }
    $path = ($install | Select-Object -Last 1).ToString().Trim()
    if ($path -and (Test-Path $path)) { return $path }
    return $null
}

function Find-Cmake([string]$VsRoot) {
    if (Test-Tool 'cmake') {
        return (Get-Command cmake).Source
    }
    $candidates = @()
    if ($VsRoot) {
        $candidates += (Join-Path $VsRoot 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe')
    }
    $candidates += (Join-Path $env:ProgramFiles 'CMake\bin\cmake.exe')
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    return $null
}

function Find-Iscc {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    if (Test-Tool 'ISCC') { return (Get-Command ISCC).Source }
    return $null
}

function Invoke-ModelFetch([string]$Python) {
    $detector = Join-Path $Root 'scripts\fetch_detector_model.py'
    $attributes = Join-Path $Root 'scripts\fetch_attribute_models.py'
    if (-not (Test-Path $detector) -and -not (Test-Path $attributes)) {
        Write-Host "No model fetch script is in this checkout."
        return
    }
    $models = Join-Path $Root 'models'
    New-Item -ItemType Directory -Force -Path $models | Out-Null
    if (Test-Path $detector) {
        $dest = Join-Path $models 'yolox_nano.onnx'
        if (Test-Path $dest) {
            Write-Host "Detector weights already present: $dest"
        } else {
            Invoke-Checked "Fetching YOLOX-Nano (Apache-2.0)" { & $Python $detector }
        }
    }
    if (Test-Path $attributes) {
        $needed = @(
            'plate_ocr_cct_xs.onnx',
            'face_detector_yunet.onnx',
            'face_recognition_sface.onnx'
        )
        $missing = @()
        foreach ($name in $needed) {
            if (-not (Test-Path (Join-Path $models $name))) { $missing += $name }
        }
        if ($missing.Count -eq 0) {
            Write-Host "Plate and face weights already present."
        } else {
            Invoke-Checked "Fetching plate OCR, YuNet, and SFace" { & $Python $attributes }
        }
        Write-Host "Plate, face, and detector weights are about 45 MB and are copied into the installer."
        Write-Host "No carrier weights are downloaded. The color heuristic runs until you train models\carrier.onnx."
    }
}

Write-Host "Sighthound Video Windows build"
Write-Host "Repository: $Root"
if ($Package) {
    Write-Host "Installer: yes (dist\SighthoundVideo-Setup.exe)"
} else {
    Write-Host "Installer: no (pass -Package to build dist\SighthoundVideo-Setup.exe)"
}

$pythonBase = Find-PythonExecutable
if (-not $pythonBase) {
    $installed = Install-WithWinget 'Python.Python.3.12' 'Python 3.12' { return [bool](Find-PythonExecutable) }
    if (-not $installed) {
        throw "Python 3.11 or newer was not found. Install it, then re-run scripts\build_windows.ps1."
    }
    $pythonBase = Find-PythonExecutable
}
if (-not $pythonBase) {
    throw "Python 3.11 or newer was not found after the winget install."
}
Write-Host "Base Python: $pythonBase"

$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
$reuseVenv = $false
if (Test-Path $venvPython) {
    & $venvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -eq 0) { $reuseVenv = $true }
}
if ($reuseVenv) {
    Write-Host "Using existing .venv"
} else {
    Write-Step "Creating .venv"
    $venvDir = Join-Path $Root '.venv'
    if (Test-Path $venvDir) {
        Remove-Item -Recurse -Force $venvDir
    }
    & $pythonBase -m venv $venvDir
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv." }
}
$Python = $venvPython

Invoke-Checked "Installing Python dependencies" {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { return }
    if ($Package) {
        & $Python -m pip install -e ".[dev,vision,package]"
    } else {
        & $Python -m pip install -e ".[dev]"
    }
}
# The editable install pulls the CPU onnxruntime package. Replace it so the
# venv and the PyInstaller bundle ship one runtime that uses DirectML or CPU.
Invoke-Checked "Installing ONNX Runtime DirectML" {
    & $Python -m pip uninstall -y onnxruntime onnxruntime-gpu
    & $Python -m pip install "onnxruntime-directml>=1.18"
}

Invoke-ModelFetch $Python

Invoke-Checked "Ruff" { & $Python -m ruff check . }
Invoke-Checked "Compile Python" {
    & $Python -m compileall -f -q appCommon backEnd frontEnd launch scripts vitaToolbox xnat FrontEndLaunchpad.py PackageSources.py
}
Invoke-Checked "Pytest" { & $Python -m pytest -q }

$vsRoot = Find-Msvc2022
if (-not $vsRoot) {
    $script:NativeStatus = 'skipped (MSVC 2022 was not found)'
    Write-Host ""
    Write-Host "MSVC 2022 was not found. Skipping the CMake build of optsearches, launch, and xnat." -ForegroundColor Yellow
} else {
    $cmake = Find-Cmake $vsRoot
    if (-not $cmake) {
        $gotCmake = Install-WithWinget 'Kitware.CMake' 'CMake' { return [bool](Find-Cmake $vsRoot) }
        if ($gotCmake) { $cmake = Find-Cmake $vsRoot }
    }
    if (-not $cmake) {
        $script:NativeStatus = 'skipped (cmake was not found)'
        Write-Host "MSVC 2022 is installed, and cmake was not found. Skipping the native build." -ForegroundColor Yellow
    } else {
        Write-Host "CMake: $cmake"
        $projects = @(
            @{ Source = 'backEnd\triggers'; Build = 'build\optsearches'; Name = 'optsearches' },
            @{ Source = 'launch'; Build = 'build\launch'; Name = 'launch' },
            @{ Source = 'xnat'; Build = 'build\xnat'; Name = 'xnat' }
        )
        foreach ($project in $projects) {
            $source = Join-Path $Root $project.Source
            $build = Join-Path $Root $project.Build
            Invoke-Checked "Configure $($project.Name)" {
                & $cmake -S $source -B $build -G "Visual Studio 17 2022" -A x64
            }
            Invoke-Checked "Build $($project.Name) (Release)" {
                & $cmake --build $build --config Release
            }
        }
        $script:NativeStatus = 'built with MSVC 2022 (Release)'
    }
}

if ($Package) {
    Invoke-Checked "PyInstaller one-folder bundle" {
        & $Python (Join-Path $Root 'scripts\package_app.py') --windowed
    }
    $bundle = Join-Path $Root 'dist\SighthoundVideo\SighthoundVideo.exe'
    if (-not (Test-Path $bundle)) {
        throw "PyInstaller did not write $bundle."
    }
    $iscc = Find-Iscc
    if (-not $iscc) {
        $gotInno = Install-WithWinget 'JRSoftware.InnoSetup' 'Inno Setup 6' { return [bool](Find-Iscc) }
        if ($gotInno) { $iscc = Find-Iscc }
    }
    if (-not $iscc) {
        throw "Inno Setup 6 (ISCC.exe) was not found. Install it from https://jrsoftware.org/isinfo.php or with winget, then re-run with -Package."
    }
    Write-Host "Inno Setup: $iscc"
    $iss = Join-Path $Root 'installer\SighthoundVideo.iss'
    Invoke-Checked "Inno Setup" { & $iscc $iss }
    $script:InstallerPath = Join-Path $Root 'dist\SighthoundVideo-Setup.exe'
    if (-not (Test-Path $script:InstallerPath)) {
        throw "Inno Setup finished without writing $($script:InstallerPath)."
    }
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "Sighthound Video Windows build finished" -ForegroundColor Green
Write-Host "  Python:    $Python"
Write-Host "  Checks:    ruff, compileall, pytest"
Write-Host "  Native:    $($script:NativeStatus)"
if ($Package) {
    Write-Host "  Installer: $($script:InstallerPath)"
} else {
    Write-Host "  Installer: not requested. Re-run with -Package."
}
Write-Host "========================================" -ForegroundColor Green
if ($Package) {
    Write-Host $script:InstallerPath
}
exit 0
