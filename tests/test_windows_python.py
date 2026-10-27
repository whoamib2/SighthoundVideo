"""The Windows build prefers Python 3.14 and records why."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
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
    assert '"' not in policy.VERSION_PROBE and "'" not in policy.VERSION_PROBE
    assert '"' not in policy.EXECUTABLE_PROBE and "'" not in policy.EXECUTABLE_PROBE
    ran = subprocess.run(
        [sys.executable, "-c", policy.VERSION_PROBE],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ran.stdout.strip() == "%d.%d" % sys.version_info[:2]
    # Windows PowerShell 5.1 deletes " inside a native argument. The old probe
    # then is not valid Python.
    broken = 'import sys; print("%d.%d" % (sys.version_info[0], sys.version_info[1]))'.replace('"', "")
    try:
        compile(broken, "<string>", "exec")
    except SyntaxError:
        pass
    else:
        raise AssertionError("stripped probe still compiled")

    root = Path(__file__).resolve().parents[1]
    build = root.joinpath("scripts", "build_windows.ps1").read_text(encoding="utf-8")
    bootstrap = root.joinpath("scripts", "bootstrap.ps1").read_text(encoding="utf-8")
    assert policy.VERSION_PROBE in build
    assert policy.EXECUTABLE_PROBE in build
    for script in (build, bootstrap):
        assert "function Invoke-Native" in script
        assert "$global:ErrorActionPreference = 'Continue'" in script
        assert "PSNativeCommandUseErrorActionPreference" in script
        assert "NativeCommandError" in script
    for body in re.findall(r"-c\s+'([^']*)'", build):
        assert '"' not in body
    assert '-c "' not in build
    assert "-c '" not in build
    assert "py -3.14" in build or "'-3.14'" in build or '"-3.14"' in build or "-3.14" in build
    assert "3.14" in build
    assert "onnxruntime-directml" in build
    assert "cp315" in build
    assert "3.11" in build
    assert 'print("%d.%d"' not in build
    assert "--tb=short" in build
    assert "build\\pytest.log" in build or "pytest.log" in build


def test_pyproject_pins_discovery_and_spdx_license():
    text = Path(__file__).resolve().parents[1].joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "GPL-3.0-only"' in text
    assert "license = {text" not in text
    assert "requires-python = \">=3.11\"" in text
    assert "appCommon*" in text
    assert "backEnd*" in text
    assert "[tool.setuptools.packages.find]" in text
