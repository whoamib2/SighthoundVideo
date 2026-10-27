"""Every repository module at least imports under Python 3.11+.

Missing optional or closed-binary dependencies are reported and skipped.
Any other exception fails the test: that is the regression this port is
guarding against (syntax, PyCObject, removed stdlib modules, bytes/str
mistakes that fire at import).
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SKIP_PARTS = {"build", "dist", "tests", ".git"}
_ALLOWED_MISSING = {
    "wx",
    "wx.adv",
    "wx.lib",
    "OpenGL",
    "OpenGL.GL",
    "cv2",
    "numpy",
    "pyaudio",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "pywintypes",
    "pythoncom",
    "objc",
    "AppKit",
    "Foundation",
    "distutils",
    "cx_Freeze",
    "py2exe",
    "videoLib2",
    "svsentry",
    "SIOWrapper",
    "fcntl",
    "winreg",
    "netifaces",
    "requests",
}


def _python_files():
    for path in sorted(ROOT.rglob("*.py")):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if path.name.startswith("."):
            continue
        yield path


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _is_allowed_missing(exc: BaseException) -> bool:
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModuleNotFoundError):
            name = (current.name or "").split(".")[0]
            if name in _ALLOWED_MISSING or (current.name or "") in _ALLOWED_MISSING:
                return True
        if isinstance(current, ImportError):
            text = str(current)
            for name in _ALLOWED_MISSING:
                if name in text:
                    return True
        current = current.__cause__ or current.__context__
    return False


@pytest.mark.parametrize("path", list(_python_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_module_imports(path: Path):
    name = _module_name(path)
    try:
        if "-" in path.name or not name.isidentifier() and "." not in name:
            spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        else:
            importlib.import_module(name)
    except Exception as exc:
        if _is_allowed_missing(exc):
            pytest.skip(str(exc))
        raise AssertionError(
            f"{name} failed to import:\n{traceback.format_exc()}"
        ) from exc
