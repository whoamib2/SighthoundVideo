"""The Windows build prefers Python 3.14 and records why."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _policy():
    path = Path(__file__).resolve().parents[1] / "scripts" / "windows_python.py"
    spec = importlib.util.spec_from_file_location("sighthound_windows_python", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_build_prefers_python_314_and_names_the_ceiling():
    policy = _policy()
    assert policy.PREFERRED == (3, 14)
    assert policy.MINIMUM == (3, 11)
    assert policy.MAXIMUM == (3, 14)
    assert policy.CANDIDATES[0] == 14
    assert policy.CANDIDATES[-1] == 11
    assert policy.CEILING_PACKAGE == "onnxruntime-directml"
    assert "cp315" in policy.CEILING_REASON

    build = Path(__file__).resolve().parents[1].joinpath("scripts", "build_windows.ps1").read_text(encoding="utf-8")
    assert "py -3.14" in build or "'-3.14'" in build or '"-3.14"' in build or "-3.14" in build
    assert "3.14" in build
    assert "onnxruntime-directml" in build
    assert "cp315" in build
    assert "3.11" in build


def test_pyproject_pins_discovery_and_spdx_license():
    text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "GPL-3.0-only"' in text
    assert "license = {text" not in text
    assert "requires-python = \">=3.11\"" in text
    assert "appCommon*" in text
    assert "backEnd*" in text
    assert "[tool.setuptools.packages.find]" in text
