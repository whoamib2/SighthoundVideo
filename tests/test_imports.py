"""Every repository module at least imports under Python 3.11+.

Missing optional or closed-binary dependencies are reported and skipped.
Any other exception fails the test: that is the regression this port is
guarding against (syntax, PyCObject, removed stdlib modules, bytes/str
mistakes that fire at import).
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Directory names that are not project source. Hidden directories are skipped
# too, so .venv, .git, and .pytest_cache are never walked.
_SKIP_DIR_NAMES = frozenset({
    ".venv",
    "venv",
    "build",
    "dist",
    ".git",
    "tests",
    "__pycache__",
})
_SKIP_DIR_NAMES_FOLDED = frozenset(name.casefold() for name in _SKIP_DIR_NAMES)
_ALLOWED_MISSING = {
    "wx",
    "wx.adv",
    "wx.lib",
    "cv2",
    "numpy",
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
    "requests",
}


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name.casefold() in _SKIP_DIR_NAMES_FOLDED


def _python_files(root: Path | None = None) -> list[Path]:
    root = ROOT if root is None else root
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk does not descend into .venv or build trees.
        dirnames[:] = sorted(name for name in dirnames if not _skip_dir(name))
        for filename in sorted(filenames):
            if filename.endswith(".py") and not filename.startswith("."):
                found.append(Path(dirpath) / filename)
    return found


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


def test_import_walk_skips_virtualenv_build_and_hidden_dirs(tmp_path: Path):
    keep = [
        tmp_path / "appCommon" / "ok.py",
        tmp_path / "FrontEndLaunchpad.py",
    ]
    skipped = [
        tmp_path / ".venv" / "Lib" / "site-packages" / "altgraph" / "ObjectGraph.py",
        tmp_path / "venv" / "Lib" / "site-packages" / "bad.py",
        tmp_path / "build" / "gen.py",
        tmp_path / "dist" / "gen.py",
        tmp_path / ".git" / "hooks" / "x.py",
        tmp_path / ".hidden" / "x.py",
        tmp_path / "tests" / "test_x.py",
        tmp_path / "__pycache__" / "stale.py",
    ]
    for path in keep + skipped:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n", encoding="utf-8")
    found = {path.relative_to(tmp_path).as_posix() for path in _python_files(tmp_path)}
    assert found == {"appCommon/ok.py", "FrontEndLaunchpad.py"}


def test_pytest_collection_skips_a_venv_directory():
    planted = ROOT / "tests" / "venv" / "test_planted_venv_collect.py"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text(
        "def test_planted_venv_collect():\n    raise AssertionError('collected')\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        blob = proc.stdout + proc.stderr
        assert proc.returncode == 0, blob
        assert "test_planted_venv_collect" not in blob
    finally:
        shutil.rmtree(planted.parent, ignore_errors=True)


def test_pytest_config_does_not_collect_virtualenv():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'testpaths = ["tests"]' in text
    assert "norecursedirs" in text
    for name in (".git", ".venv", "venv", "build", "dist"):
        assert f'"{name}"' in text


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.relative_to(ROOT)))
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
