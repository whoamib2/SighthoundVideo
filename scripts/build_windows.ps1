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

# Stop applies to cmdlets. Native stderr must not stop the script: Windows
# PowerShell 5.1 turns it into a terminating NativeCommandError, and PowerShell
# 7 does the same for a non-zero exit when PSNativeCommandUseErrorActionPreference
# is true. Invoke-Native turns that off for each native command.
if (Test-Path variable:PSNativeCommandUseErrorActionPreference) {
    $PSNativeCommandUseErrorActionPreference = $false
    $global:PSNativeCommandUseErrorActionPreference = $false
}
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $Root

$script:NativeStatus = 'not started'
$script:InstallerPath = ''

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Test-Tool([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
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
        # 2>&1 makes stderr an ErrorRecord in the pipeline instead of a
        # terminating error. Stdout is returned. Stderr is printed.
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

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    Write-Step $Label
    Invoke-Native $Action
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
    Invoke-Native { & winget install --id $Id -e --accept-package-agreements --accept-source-agreements --disable-interactivity }
    if (& $Verify) {
        Write-Host "$Label is ready."
        return $true
    }
    Write-Host "winget could not make $Label available (exit $LASTEXITCODE)." -ForegroundColor Yellow
    return $false
}

# Newest first. 3.14 is preferred. 3.15+ is refused: onnxruntime-directml 1.24.4
# has no Windows amd64 cp315 wheel. See scripts\windows_python.py.
$script:AllowedVersions = @('3.14', '3.13', '3.12', '3.11')

function Get-PythonMM([string]$Exe) {
    if (-not $Exe -or -not (Test-Path -LiteralPath $Exe)) { return $null }
    # Same text as scripts\windows_python.py VERSION_PROBE. No quote characters:
    # Windows PowerShell 5.1 strips them from arguments to python.exe.
    $probe = 'import sys; sys.stdout.write(str(sys.version_info[0])+chr(46)+str(sys.version_info[1])+chr(10))'
    $out = Invoke-Native { & $Exe -c $probe }
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    return ($out | Select-Object -Last 1).ToString().Trim()
}

function Find-VersionedPython([string]$Version) {
    if (Test-Tool 'py') {
        # Same text as scripts\windows_python.py EXECUTABLE_PROBE. No quote characters.
        $probe = 'import sys; sys.stdout.write(sys.executable+chr(10))'
        $out = Invoke-Native -Quiet { & py "-$Version" -c $probe }
        if ($LASTEXITCODE -eq 0 -and $out) {
            $exe = ($out | Select-Object -Last 1).ToString().Trim()
            if ($exe -and (Test-Path -LiteralPath $exe)) {
                $mm = Get-PythonMM $exe
                if ($mm -eq $Version) { return $exe }
            }
        }
    }
    $tag = 'Python' + $Version.Replace('.', '')
    $candidates = @(
        (Join-Path $env:LocalAppData "Programs\Python\$tag\python.exe"),
        (Join-Path $env:ProgramFiles "$tag\python.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            $mm = Get-PythonMM $path
            if ($mm -eq $Version) { return $path }
        }
    }
    return $null
}

function Find-BasePython {
    foreach ($version in $script:AllowedVersions) {
        $exe = Find-VersionedPython $version
        if ($exe) {
            # A bare hashtable is unrolled into the pipeline. A PSCustomObject stays one object.
            return [pscustomobject]@{ Exe = $exe; Version = $version }
        }
    }
    return $null
}

function Write-PythonPolicy([string]$Chosen) {
    Write-Host "Windows build prefers Python 3.14 (py -3.14)."
    Write-Host "cp314 win_amd64 wheels: wxPython 4.3.2, numpy 2.5.4, Pillow 13.0.0, onnxruntime-directml 1.24.4."
    Write-Host "Installed on 3.14 from another tag: PyAV 19.0.2 (cp312-abi3), onnx 1.23.2 (cp312-abi3), opencv-python-headless 5.0.0.93 (cp37-abi3), PyInstaller 6.22.3 (py3-none-win_amd64)."
    Write-Host "Python 3.15 and newer are refused: onnxruntime-directml 1.24.4 has no Windows amd64 cp315 wheel."
    if ($Chosen -and $Chosen -ne '3.14') {
        Write-Host "Python 3.14 was not found. Using Python $Chosen." -ForegroundColor Yellow
    }
}

function Find-Msvc2022 {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vswhere)) { return $null }
    $install = Invoke-Native { & $vswhere -latest -version '[17.0,18.0)' -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath }
    if ($LASTEXITCODE -ne 0 -or -not $install) { return $null }
    $path = ($install | Select-Object -Last 1).ToString().Trim()
    if ($path -and (Test-Path -LiteralPath $path)) { return $path }
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
        if ($path -and (Test-Path -LiteralPath $path)) { return $path }
    }
    return $null
}

function Find-Iscc {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
    )
    foreach ($path in $candidates) {
        if (Test-Path -LiteralPath $path) { return $path }
    }
    if (Test-Tool 'ISCC') { return (Get-Command ISCC).Source }
    return $null
}

function Invoke-ModelFetch([string]$Python) {
    $detector = Join-Path $Root 'scripts\fetch_detector_model.py'
    $attributes = Join-Path $Root 'scripts\fetch_attribute_models.py'
    if (-not (Test-Path -LiteralPath $detector) -and -not (Test-Path -LiteralPath $attributes)) {
        Write-Host "No model fetch script is in this checkout."
        return
    }
    $models = Join-Path $Root 'models'
    New-Item -ItemType Directory -Force -Path $models | Out-Null
    if (Test-Path -LiteralPath $detector) {
        $dest = Join-Path $models 'yolox_nano.onnx'
        if (Test-Path -LiteralPath $dest) {
            Write-Host "Detector weights already present: $dest"
        } else {
            Invoke-Checked "Fetching YOLOX-Nano (Apache-2.0)" { & $Python $detector }
        }
    }
    if (Test-Path -LiteralPath $attributes) {
        $needed = @(
            'plate_ocr_cct_xs.onnx',
            'face_detector_yunet.onnx',
            'face_recognition_sface.onnx'
        )
        $missing = @()
        foreach ($name in $needed) {
            if (-not (Test-Path -LiteralPath (Join-Path $models $name))) { $missing += $name }
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

$chosen = Find-BasePython
if (-not $chosen) {
    $installed = Install-WithWinget 'Python.Python.3.14' 'Python 3.14' { return [bool](Find-BasePython) }
    if (-not $installed) {
        throw "Python 3.14, 3.13, 3.12, or 3.11 was not found. Python 3.15 and newer are refused because onnxruntime-directml 1.24.4 has no Windows amd64 cp315 wheel. Install Python 3.14, then re-run scripts\build_windows.ps1."
    }
    $chosen = Find-BasePython
}
if (-not $chosen) {
    throw "Python 3.14 was not found after the winget install."
}
$pythonBase = $chosen.Exe
Write-PythonPolicy $chosen.Version
Write-Host "Base Python: $pythonBase"

$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
$venvDir = Join-Path $Root '.venv'
$reuseVenv = $false
if (Test-Path -LiteralPath $venvPython) {
    $venvMM = Get-PythonMM $venvPython
    if ($venvMM -eq $chosen.Version) {
        $reuseVenv = $true
    } else {
        Write-Host "Existing .venv is Python $venvMM. Recreating it with Python $($chosen.Version)."
    }
}
if ($reuseVenv) {
    Write-Host "Using existing .venv (Python $($chosen.Version))"
} else {
    Write-Step "Creating .venv with Python $($chosen.Version)"
    if (Test-Path -LiteralPath $venvDir) {
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
    Invoke-Native { & $pythonBase -m venv $venvDir }
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv." }
}
$Python = $venvPython

Invoke-Checked "Installing Python dependencies" {
    & $Python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { return }
    # '{0}[extras]' keeps a path with spaces as one argument. "$Root[extras]" is not safe:
    # PowerShell treats $Root[ as an index.
    $pyproject = Get-Content -LiteralPath (Join-Path $Root 'pyproject.toml') -Raw
    $extras = @('dev')
    if ($Package) {
        $extras += 'package'
        if ($pyproject -match '(?m)^vision\s*=') { $extras += 'vision' }
    }
    $editable = '{0}[{1}]' -f $Root, ($extras -join ',')
    Write-Host "pip install -e $editable"
    & $Python -m pip install -e $editable
}
# The editable install pulls the CPU onnxruntime package. Replace it so the
# venv and the PyInstaller bundle ship one runtime that uses DirectML or CPU.
Invoke-Checked "Installing ONNX Runtime DirectML" {
    & $Python -m pip uninstall -y onnxruntime onnxruntime-gpu
    & $Python -m pip install 'onnxruntime-directml>=1.18'
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
            # The generator name is one argument. Do not put a " character in it:
            # Windows PowerShell 5.1 strips those from native arguments.
            Invoke-Checked "Configure $($project.Name)" {
                & $cmake -S $source -B $build -G 'Visual Studio 17 2022' -A x64
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
    if (-not (Test-Path -LiteralPath $bundle)) {
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
    if (-not (Test-Path -LiteralPath $script:InstallerPath)) {
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
