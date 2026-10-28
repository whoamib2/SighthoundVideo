"""Every third-party import in the application is a pyproject dependency."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "frontEnd",
    "backEnd",
    "appCommon",
    "launch",
    "vitaToolbox",
    "xnat",
)
LOCAL_TOPS = set(SOURCE_ROOTS) | {"scripts", "FrontEndLaunchpad"}

# Import name -> project name in pyproject.toml. Names are compared after PEP 503
# normalization. onnxruntime-directml provides the onnxruntime module.
IMPORT_TO_REQUIREMENTS = {
    "OpenGL": ("PyOpenGL",),
    "PIL": ("Pillow",),
    "wx": ("wxPython",),
    "numpy": ("numpy",),
    "av": ("av",),
    "psutil": ("psutil",),
    "requests": ("requests",),
    "certifi": ("certifi",),
    "cv2": ("opencv-python-headless",),
    "onnx": ("onnx",),
    "onnxruntime": ("onnxruntime", "onnxruntime-directml"),
}

# Closed binaries and optional local modules. They are not pip distributions.
NOT_DISTRIBUTIONS = {
    "SIOWrapper",
    "videoLib2",
    "svsentry",
    "AppUpdateDialog",
    "BugReportDialog",
    "LicenseManager",
}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _pyproject_requirements() -> set[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    names = set()
    for quoted in re.findall(r'"([^"]+)"', text):
        token = re.split(r"[<>=;!\s\[]", quoted, maxsplit=1)[0].strip()
        if token and re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", token):
            names.add(_norm(token))
    return names


def _source_files() -> list[Path]:
    files = [ROOT / "FrontEndLaunchpad.py"]
    for name in SOURCE_ROOTS:
        files.extend(path for path in (ROOT / name).rglob("*.py"))
    return files


def _imported_tops(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    tops = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            tops.add(node.module.split(".")[0])
    return tops


def test_third_party_imports_are_declared_in_pyproject():
    declared = _pyproject_requirements()
    stdlib = sys.stdlib_module_names
    missing = {}
    unknown = {}
    for path in _source_files():
        for name in _imported_tops(path):
            if name in stdlib or name in LOCAL_TOPS or name in NOT_DISTRIBUTIONS:
                continue
            requirements = IMPORT_TO_REQUIREMENTS.get(name)
            rel = str(path.relative_to(ROOT))
            if requirements is None:
                unknown.setdefault(name, set()).add(rel)
                continue
            if not any(_norm(req) in declared for req in requirements):
                missing.setdefault(name, set()).add(rel)
    assert not unknown, "undeclared third-party imports:\n" + "\n".join(
        "%s in %s" % (name, ", ".join(sorted(paths)))
        for name, paths in sorted(unknown.items())
    )
    assert not missing, "imports whose packages are absent from pyproject.toml:\n" + "\n".join(
        "%s needs %s (%s)" % (name, IMPORT_TO_REQUIREMENTS[name], ", ".join(sorted(paths)))
        for name, paths in sorted(missing.items())
    )


def test_pyinstaller_hides_opengl_plugins():
    import importlib.util

    path = ROOT / "scripts" / "package_app.py"
    spec = importlib.util.spec_from_file_location("vigilcam_package_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    command = module.pyinstaller_command(ROOT, platform="win32", windowed=True)
    blob = " ".join(command)
    for name in ("OpenGL", "OpenGL.GL", "OpenGL.platform.win32", "certifi", "psutil"):
        assert name in module._HIDDEN_IMPORTS
        assert name in blob
    assert "--collect-submodules" in command
    assert "OpenGL" in command
