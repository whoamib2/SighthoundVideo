"""Package VigilCam with PyInstaller.

The historical Windows build used py2exe and the Mac build used cx_Freeze.
Both entry points (``frontEnd/setup-Win.py`` and ``frontEnd/setup-Mac.py``)
delegate here.

``scripts/build_windows.ps1 -Package`` calls this, then wraps the one-folder
bundle with Inno Setup. The bundle contains the Python runtime, wxPython,
PyAV (and its FFmpeg libraries), and ONNX Runtime, so the installed app does
not need a Python install. On Windows the files sit next to the executable,
which is where the frozen app already looks for ``models``.

Closed binaries (``videoLib2``, ``svsentry``, ``SIOWrapper``) are not part of
this source tree. When a replacement or a legacy build of those libraries is
available, pass each one with ``--add-binary``:

    python scripts/package_app.py --add-binary path/to/liboptsearches.so:.

PyInstaller itself is an optional dependency (``pip install pyinstaller``).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Directories that are never application data, even if a file inside them
# happens to use a resource suffix.
_SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "build",
    "dist",
    "tests",
    "__pycache__",
    ".pytest_cache",
    ".cursor",
    ".mypy_cache",
}

# Non-Python files the UI and the packager open by relative path. Model
# weights are added file-by-file further down so a models/README is not packed.
_RESOURCE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".icns",
    ".wav",
    ".mp3",
    ".ogg",
    ".ttf",
    ".otf",
    ".html",
    ".css",
    ".xml",
    ".pem",
    ".svg",
}

_COLLECT_ALL = ("wx", "av", "onnxruntime", "PIL", "numpy", "cv2", "OpenGL", "certifi", "psutil")
# PyOpenGL loads platform plugins after the first import. PyInstaller does not
# trace those, so they are named here.
_HIDDEN_IMPORTS = (
    "OpenGL",
    "OpenGL.GL",
    "OpenGL.GLU",
    "OpenGL.platform.win32",
    "OpenGL.arrays",
    "OpenGL.arrays.numpymodule",
    "OpenGL.raw.GL",
    "certifi",
    "psutil",
)


def _sep(platform: str) -> str:
    return ";" if platform == "win32" else ":"


def _data_arg(source: Path, dest: str, sep: str) -> str:
    """One PyInstaller --add-data value. Spaces are kept; the separator is not."""
    text = str(source)
    if sep in text:
        raise ValueError(
            "Cannot pack %s because that path contains %r" % (text, sep)
        )
    return "%s%s%s" % (text, sep, dest)


def _importable(name: str) -> bool:
    try:
        __import__(name)
    except Exception:
        return False
    return True


def resource_directories(root: Path) -> list[str]:
    """Relative directories of bundled images, sounds, fonts, and similar files.

    A directory is included when it directly contains a resource file. Nested
    directories are collapsed into an ancestor that also contains a resource,
    so ``frontEnd/bmps`` covers ``frontEnd/bmps/mac`` and ``frontEnd/bmps/win``.
    The top-level ``models`` directory is omitted here; ONNX files are added
    individually.
    """
    root = Path(root)
    found: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            name for name in dirnames
            if name not in _SKIP_DIR_NAMES and not name.endswith(".egg-info")
        ]
        relative = Path(dirpath).relative_to(root)
        if relative.parts and (
            relative.parts[0] in _SKIP_DIR_NAMES or relative.parts[0] == "models"
        ):
            dirnames[:] = []
            continue
        if relative == Path("."):
            continue
        if any(Path(name).suffix.lower() in _RESOURCE_SUFFIXES for name in filenames):
            found.add(relative.as_posix())
    kept: list[str] = []
    for relative in sorted(found, key=lambda item: (item.count("/"), item)):
        if any(relative.startswith(parent + "/") for parent in kept):
            continue
        kept.append(relative)
    return kept


def pyinstaller_command(
    root: Path,
    *,
    name: str = "VigilCam",
    distpath: Path | None = None,
    workpath: Path | None = None,
    extra_binaries: list[str] | None = None,
    windowed: bool = False,
    platform: str | None = None,
) -> list[str]:
    """Arguments for a one-folder PyInstaller build, excluding the tool name."""
    system = platform or sys.platform
    sep = _sep(system)
    dist = distpath or (root / "dist")
    work = workpath or (root / "build" / "pyinstaller")
    command = [
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        name,
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        "--specpath",
        str(root / "build"),
        "--paths",
        str(root),
    ]
    # Windows one-folder bundles put datas next to the executable, which is
    # sys._MEIPASS. appCommon.resources.resource_path joins bundled paths there.
    icon = root / "icons" / "VigilCam.ico"
    if icon.is_file():
        command.extend(["--icon", str(icon)])
    if system == "win32":
        command.extend(["--contents-directory", "."])
    for module_name in ("backEnd", "frontEnd", "vitaToolbox", "appCommon", "launch", "xnat", "OpenGL"):
        command.extend(["--collect-submodules", module_name])
    for module_name in _HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module_name])
    for module_name in _COLLECT_ALL:
        if _importable(module_name):
            command.extend(["--collect-all", module_name])
    for relative in resource_directories(root):
        source = root / relative
        if source.is_dir():
            command.extend(["--add-data", _data_arg(source, relative.replace("\\", "/"), sep)])
    models = root / "models"
    if models.is_dir():
        for path in sorted(models.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() == ".onnx" or path.name.endswith(".onnx.json"):
                command.extend(["--add-data", _data_arg(path, "models", sep)])
    for binary in extra_binaries or []:
        command.extend(["--add-binary", binary])
    if windowed:
        command.append("--windowed")
    command.append(str(root / "FrontEndLaunchpad.py"))
    return command


_DEBUG_LAUNCHER = """@echo off
\"%~dp0{exe}\" --console %*
echo.
echo Exit code %ERRORLEVEL%
echo Startup log: %LOCALAPPDATA%\\VigilCam\\logs\\startup.log
pause
"""


def write_debug_launcher(folder: Path, exe_name: str = "VigilCam.exe") -> Path:
    """Console double-click launcher next to the windowed executable.

    The windowed exe has no console. This command file starts it with
    --console, which allocates one, and leaves the window open if startup
    returns.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "VigilCam-Debug.cmd"
    path.write_text(_DEBUG_LAUNCHER.format(exe=exe_name), encoding="utf-8", newline="\r\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a VigilCam bundle")
    parser.add_argument("--name", default="VigilCam")
    parser.add_argument("--distpath", default=str(ROOT / "dist"))
    parser.add_argument("--workpath", default=str(ROOT / "build" / "pyinstaller"))
    parser.add_argument(
        "--add-binary",
        action="append",
        default=[],
        help="PyInstaller --add-binary entry (SRC:DEST, or SRC;DEST on Windows)",
    )
    parser.add_argument("--windowed", action="store_true", help="Hide the console")
    args = parser.parse_args(argv)

    try:
        from PyInstaller.__main__ import run as pyinstaller_run
    except ImportError:
        print(
            "PyInstaller is not installed. Install it with: python -m pip install pyinstaller",
            file=sys.stderr,
        )
        return 1

    command = pyinstaller_command(
        ROOT,
        name=args.name,
        distpath=Path(args.distpath),
        workpath=Path(args.workpath),
        extra_binaries=args.add_binary,
        windowed=args.windowed,
    )
    folder = Path(args.distpath) / args.name
    print("PyInstaller one-folder bundle: %s" % folder)
    pyinstaller_run(command)
    if sys.platform == "win32" or args.windowed:
        launcher = write_debug_launcher(folder, args.name + ".exe")
        print("Debug launcher: %s" % launcher)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
