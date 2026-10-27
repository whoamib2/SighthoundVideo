"""The Windows package command is a one-folder bundle that carries model files."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load():
    path = Path(__file__).resolve().parents[1] / "scripts" / "package_app.py"
    spec = importlib.util.spec_from_file_location("sighthound_package_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_bundle_is_one_folder_and_packs_models(tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    weight = models / "yolox_nano.onnx"
    weight.write_bytes(b"not a real model")
    sidecar = models / "yolox_nano.onnx.json"
    sidecar.write_text("{}", encoding="utf-8")
    readme = models / "README.md"
    readme.write_text("skip", encoding="utf-8")

    command = _load().pyinstaller_command(tmp_path, platform="win32", windowed=True)
    assert "--onedir" in command
    assert "--onefile" not in command
    contents = command[command.index("--contents-directory") + 1]
    assert contents == "."
    assert "--windowed" in command
    packed = [item for item in command if item.startswith("--add-data") or "yolox" in item]
    blob = " ".join(command)
    assert str(weight) in blob
    assert str(sidecar) in blob
    assert "README.md" not in blob
    assert any(item.endswith(";models") or item.endswith(":models") for item in packed)
    assert command[-1] == str(tmp_path / "FrontEndLaunchpad.py")
