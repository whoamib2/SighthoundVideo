"""Bundled resource paths exist on disk and are passed to PyInstaller."""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    "frontEnd",
    "backEnd",
    "appCommon",
    "launch",
    "vitaToolbox",
    "xnat",
)
_PREFIX = re.compile(
    r"^(frontEnd|vitaToolbox|appCommon|backEnd|icons|fonts|config|models|launch|xnat)/"
)
_SUFFIX = re.compile(
    r"\.(png|jpe?g|gif|bmp|ico|icns|wav|mp3|ogg|ttf|otf|html|css|xml|pem|onnx|svg)$",
    re.IGNORECASE,
)
_FORMAT = re.compile(r"%[%0-9]*[diouxXeEfFgGcrs]")


def _load_packager():
    path = ROOT / "scripts" / "package_app.py"
    spec = importlib.util.spec_from_file_location("vigilcam_package_app", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source_files():
    files = [ROOT / "FrontEndLaunchpad.py"]
    for name in SOURCE_ROOTS:
        files.extend((ROOT / name).rglob("*.py"))
    return files


def _is_main_guard(node):
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
        return False
    sides = (test.left, test.comparators[0])
    names = [side.id for side in sides if isinstance(side, ast.Name)]
    constants = [side.value for side in sides if isinstance(side, ast.Constant)]
    return "__name__" in names and "__main__" in constants


class _ResourceVisitor(ast.NodeVisitor):
    def __init__(self):
        self.paths = []

    def visit_FunctionDef(self, node):
        if node.name == "test_main":
            return
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node):
        if _is_main_guard(node):
            return
        self.generic_visit(node)

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            self.paths.append(node.value)

    def visit_Call(self, node):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            parts = []
            complete = True
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    parts.append(arg.value.strip("/\\"))
                else:
                    complete = False
                    break
            if complete and parts:
                self.paths.append("/".join(parts))
        self.generic_visit(node)


def _referenced_resources():
    found = []
    seen = set()
    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
        visitor = _ResourceVisitor()
        visitor.visit(tree)
        for text in visitor.paths:
            text = text.replace("\\", "/")
            if "://" in text or "\n" in text or "*" in text or len(text) > 240:
                continue
            if not _PREFIX.match(text) or not _SUFFIX.search(text):
                continue
            if text in seen:
                continue
            seen.add(text)
            found.append((text, path))
    return found


def _exists(relative):
    if "%" not in relative:
        return (ROOT / relative).is_file(), relative
    pattern = _FORMAT.sub("*", relative)
    if "%" in pattern:
        return False, pattern
    return any(path.is_file() for path in ROOT.glob(pattern)), pattern


def _add_data_dests(command):
    dests = []
    for index, item in enumerate(command):
        if item != "--add-data":
            continue
        value = command[index + 1]
        dests.append(value.split(";")[-1].replace("\\", "/").strip("/"))
    return dests


def _covered(relative, dests):
    relative = _FORMAT.sub("*", relative).replace("\\", "/")
    parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
    for dest in dests:
        if parent == dest or parent.startswith(dest + "/"):
            return True
    return False


def test_referenced_resources_exist_and_are_packed():
    packager = _load_packager()
    command = packager.pyinstaller_command(ROOT, platform="win32")
    dests = _add_data_dests(command)
    directories = packager.resource_directories(ROOT)
    missing_dirs = [relative for relative in directories if relative not in dests]
    assert not missing_dirs, "resource directories missing from PyInstaller datas:\n" + "\n".join(missing_dirs)

    missing = []
    unpacked = []
    for relative, path in _referenced_resources():
        exists, shown = _exists(relative)
        if not exists:
            missing.append("%s (referenced from %s)" % (shown, path.relative_to(ROOT)))
        elif not _covered(relative, dests):
            unpacked.append("%s (referenced from %s)" % (shown, path.relative_to(ROOT)))
    assert not missing, "resource files referenced in code are not on disk:\n" + "\n".join(missing)
    assert not unpacked, "resource files referenced in code are not in the PyInstaller datas:\n" + "\n".join(unpacked)
    assert _covered("vitaToolbox/wx/bmps/Date_Arrow_Left_Enabled.png", dests)
    assert (ROOT / "vitaToolbox/wx/bmps/Date_Arrow_Left_Enabled.png").is_file()


def test_resource_directories_collapse_nested_bitmap_folders(tmp_path):
    nested = tmp_path / "frontEnd" / "bmps" / "win"
    nested.mkdir(parents=True)
    (nested / "Play.png").write_bytes(b"png")
    (tmp_path / "frontEnd" / "bmps" / "Edit.png").write_bytes(b"png")
    (tmp_path / "vitaToolbox" / "wx" / "bmps").mkdir(parents=True)
    (tmp_path / "vitaToolbox" / "wx" / "bmps" / "Arrow.png").write_bytes(b"png")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "README.md").write_text("skip", encoding="utf-8")
    (tmp_path / "models" / "net.onnx").write_bytes(b"onnx")

    packager = _load_packager()
    assert packager.resource_directories(tmp_path) == [
        "frontEnd/bmps",
        "vitaToolbox/wx/bmps",
    ]
    command = packager.pyinstaller_command(tmp_path, platform="win32")
    blob = " ".join(command)
    assert "vitaToolbox/wx/bmps" in blob
    assert "README.md" not in blob
    assert str(tmp_path / "models" / "net.onnx") in blob


def test_resource_path_uses_meipass_not_the_working_directory(monkeypatch, tmp_path):
    from appCommon.resources import application_root, resource_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    resolved = resource_path("vitaToolbox/wx/bmps/Date_Arrow_Left_Enabled.png")
    assert resolved == os.path.normpath(
        str(bundle / "vitaToolbox" / "wx" / "bmps" / "Date_Arrow_Left_Enabled.png")
    )
    assert application_root() == str(bundle)
    again = resource_path(resolved)
    assert again == resolved

    absolute = str(tmp_path / "camera.jpg")
    assert resource_path(absolute) == absolute
    assert resource_path("notes.txt") == "notes.txt"
    assert resource_path(b"icons/VigilCam.ico") == os.path.normpath(str(bundle / "icons" / "VigilCam.ico"))


def test_resource_path_without_meipass_uses_the_executable_directory(monkeypatch, tmp_path):
    from appCommon.resources import resource_path

    exe = tmp_path / "VigilCam.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "platform", "win32")
    resolved = resource_path("config/cacert.pem")
    assert resolved == os.path.normpath(str(tmp_path / "config" / "cacert.pem"))


def test_source_resource_path_ignores_the_working_directory(monkeypatch, tmp_path):
    from appCommon.resources import resource_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    resolved = resource_path(os.path.join("icons", "VigilCam.ico"))
    assert resolved == os.path.normpath(str(ROOT / "icons" / "VigilCam.ico"))
    assert os.path.isfile(resolved)


def test_path_aware_wx_subclass_keeps_buffer_constructors(monkeypatch, tmp_path):
    from appCommon.resources import _path_aware_subclass

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    class Box:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        @staticmethod
        def FromBuffer(width, height, data):
            return ("buffer", width, height, data)

    wrapped = _path_aware_subclass(Box)
    made = wrapped("frontEnd/bmps/Edit.png", 3)
    assert made.args[0] == os.path.normpath(str(tmp_path / "frontEnd" / "bmps" / "Edit.png"))
    assert made.args[1] == 3
    sized = wrapped(8, 9)
    assert sized.args == (8, 9)
    assert wrapped.FromBuffer(2, 4, b"rgba") == ("buffer", 2, 4, b"rgba")


def test_pillow_hook_opens_bundled_paths(monkeypatch, tmp_path):
    import appCommon.resources as resources

    picture = tmp_path / "frontEnd" / "bmps"
    picture.mkdir(parents=True)
    Image.new("RGB", (2, 2), (255, 0, 0)).save(picture / "mark.png")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resources._PIL_PATCHED = False
    resources.install_resource_hooks()
    opened = Image.open("frontEnd/bmps/mark.png")
    assert opened.size == (2, 2)


def test_model_dir_follows_the_frozen_root(monkeypatch, tmp_path):
    from appCommon.resources import model_dir

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("SV_DEVEL_LIB_FOLDER_CONAN", raising=False)
    assert model_dir() == os.path.join(str(tmp_path), "models")
