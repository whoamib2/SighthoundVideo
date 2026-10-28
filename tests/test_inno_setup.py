"""The Windows installer script must be valid Inno Setup, and the build must find ISCC."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ISS = ROOT / "installer" / "SighthoundVideo.iss"
BUILD = ROOT / "scripts" / "build_windows.ps1"

# Inno Setup 6 [Tasks] flags. "checked" is not one of them; tasks start checked.
_TASK_FLAGS = {
    "checkablealone",
    "checkedonce",
    "dontinheritcheck",
    "exclusive",
    "restart",
    "unchecked",
}


def _sections(text):
    current = None
    sections = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(";") or not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]") and ":" not in stripped:
            current = stripped[1:-1]
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(stripped)
    return sections


def test_desktop_task_does_not_use_the_checked_flag():
    text = ISS.read_text(encoding="utf-8")
    tasks = _sections(text)["Tasks"]
    desktop = [line for line in tasks if 'Name: "desktopicon"' in line]
    startup = [line for line in tasks if 'Name: "startup"' in line]
    assert len(desktop) == 1
    assert len(startup) == 1
    assert "Flags:" not in desktop[0]
    assert "unchecked" in startup[0]
    assert "Flags: checked" not in text
    for line in tasks:
        match = re.search(r"Flags:\s*(.+)", line)
        if match is None:
            continue
        flags = match.group(1).split()
        unknown = [flag for flag in flags if flag not in _TASK_FLAGS]
        assert unknown == []


def test_build_searches_per_user_inno_and_searches_again_after_winget():
    build = BUILD.read_text(encoding="utf-8")
    assert "Programs\\Inno Setup 6\\ISCC.exe" in build
    assert "$env:LocalAppData" in build
    assert "InstallLocation" in build
    assert "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall" in build
    assert "HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall" in build
    assert "WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall" in build
    assert "ProgramFiles(x86)" in build
    assert "Get-Command ISCC" in build
    assert "function Update-ProcessPath" in build
    match = re.search(
        r"if \(-not \$iscc\) \{\s+"
        r"Install-WithWinget 'JRSoftware\.InnoSetup' 'Inno Setup 6' \{ return \[bool\]\(Find-Iscc\) \} \| Out-Null\s+"
        r"(?:#.*\n\s+)*"
        r"\$iscc = Find-Iscc\s+"
        r"\}",
        build,
    )
    assert match, "winget install must be followed by another Find-Iscc"


def _find_iscc():
    found = shutil.which("ISCC") or shutil.which("iscc")
    if found:
        return found
    if sys.platform != "win32":
        return None
    roots = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    for env_name in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(env_name)
        if root:
            roots.append(Path(root) / "Inno Setup 6" / "ISCC.exe")
    for path in roots:
        if path.is_file():
            return str(path)
    return None


def test_iscc_compiles_the_tasks_section_when_present(tmp_path):
    iscc = _find_iscc()
    if not iscc:
        pytest.skip("ISCC.exe is not installed in this environment")
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "SighthoundVideo.exe").write_bytes(b"MZ")
    tasks = "\n".join(_sections(ISS.read_text(encoding="utf-8"))["Tasks"])
    out = tmp_path / "out"
    out.mkdir()
    script = tmp_path / "probe.iss"
    # ISCC accepts forward slashes. Keep the real [Tasks] lines so an unknown flag fails here.
    script.write_text(
        "\n".join(
            [
                "[Setup]",
                "AppName=Sighthound Video",
                "AppVersion=7.0.16",
                "DefaultDirName={autopf}\\Sighthound Video",
                "OutputDir=" + out.as_posix(),
                "OutputBaseFilename=SighthoundVideo-Setup",
                "[Tasks]",
                tasks,
                "[Files]",
                'Source: "' + (payload / "*").as_posix() + '"; DestDir: "{app}"',
                "[Icons]",
                'Name: "{autodesktop}\\Sighthound Video"; Filename: "{app}\\SighthoundVideo.exe"; Tasks: desktopicon',
                "",
            ]
        ),
        encoding="utf-8",
    )
    ran = subprocess.run([iscc, str(script)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert (out / "SighthoundVideo-Setup.exe").is_file()
