"""Package Sighthound Video with PyInstaller.

The historical Windows build used py2exe and the Mac build used cx_Freeze.
Both entry points (``frontEnd/setup-Win.py`` and ``frontEnd/setup-Mac.py``)
delegate here.

Closed binaries (``videoLib2``, ``svsentry``, ``SIOWrapper``) are not part of
this source tree. When a replacement or a legacy build of those libraries is
available, pass each one with ``--add-binary``:

    python scripts/package_app.py --add-binary path/to/liboptsearches.so:.

PyInstaller itself is an optional dependency (``pip install pyinstaller``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_DATA_DIRS = (
    "frontEnd/bmps",
    "fonts",
    "icons",
    "config",
)


def _existing_datas() -> list[str]:
    args: list[str] = []
    for relative in _DATA_DIRS:
        source = ROOT / relative
        if source.is_dir():
            args.extend(["--add-data", f"{source}{';' if sys.platform == 'win32' else ':'}{relative}"])
    sounds = ROOT / "frontEnd" / "sounds"
    if sounds.is_dir():
        sep = ";" if sys.platform == "win32" else ":"
        args.extend(["--add-data", f"{sounds}{sep}frontEnd/sounds"])
    return args


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a Sighthound Video bundle")
    parser.add_argument("--name", default="SighthoundVideo")
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

    command = [
        "--noconfirm",
        "--clean",
        "--name",
        args.name,
        "--distpath",
        args.distpath,
        "--workpath",
        args.workpath,
        "--specpath",
        str(ROOT / "build"),
        "--paths",
        str(ROOT),
        "--collect-submodules",
        "backEnd",
        "--collect-submodules",
        "frontEnd",
        "--collect-submodules",
        "vitaToolbox",
        "--collect-submodules",
        "appCommon",
        "--collect-submodules",
        "launch",
        "--collect-submodules",
        "xnat",
    ]
    command.extend(_existing_datas())
    for binary in args.add_binary:
        command.extend(["--add-binary", binary])
    if args.windowed:
        command.append("--windowed")
    command.append(str(ROOT / "FrontEndLaunchpad.py"))
    pyinstaller_run(command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
