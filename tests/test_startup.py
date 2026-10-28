"""Startup must reach wx.MainLoop and must not quit just because the app is frozen."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_log_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    startup = _load(ROOT / "appCommon" / "startuplog.py", "sighthound_startuplog")
    startup.log_startup("hello")
    path = tmp_path / "Sighthound Video" / "logs" / "startup.log"
    assert path.is_file()
    assert "hello" in path.read_text(encoding="utf-8")
    assert startup.startup_log_path() == str(path)


def test_instance_lock_name_strips_domain_slash():
    startup = _load(ROOT / "appCommon" / "startuplog.py", "sighthound_startuplog_lock")
    assert "\\" not in startup.instance_lock_name("Sighthound Video", r"OFFICE\Pat")
    assert startup.instance_lock_name("Sighthound Video", r"OFFICE\Pat") == "Sighthound_Video-OFFICE_Pat"


def test_frozen_app_does_not_require_a_missing_launch_service(monkeypatch):
    import launch.Launch as launchmod

    previous = launchmod._kServiceAvailable
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("SV_NO_SERVICE", "0")
    launchmod._kServiceAvailable = None
    try:
        assert launchmod.serviceAvailable() is False
    finally:
        launchmod._kServiceAvailable = previous


def test_frozen_windows_backend_relaunches_this_executable(monkeypatch):
    from frontEnd.GetLaunchParameters import getLaunchParameters

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        sys,
        "executable",
        r"C:\Program Files\Sighthound Video\SighthoundVideo.exe",
    )
    params = getLaunchParameters()
    assert params == [sys.executable]


def test_debug_launcher_passes_console(tmp_path):
    package = _load(ROOT / "scripts" / "package_app.py", "sighthound_package_app_debug")
    path = package.write_debug_launcher(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert '"%~dp0SighthoundVideo.exe" --console %*' in text
    assert "startup.log" in text


def test_frontend_main_enters_mainloop_when_a_window_exists(monkeypatch):
    wx = pytest.importorskip("wx")
    import frontEnd.FrontEndApp as frontend

    entered = {}

    class _Logger:
        def grabStdStreams(self):
            pass

        def error(self, *_args, **_kwargs):
            pass

        def info(self, *_args, **_kwargs):
            pass

    class _App:
        _quietExit = False
        _fatalShown = False

        def PostInit(self):
            return True

        def MainLoop(self):
            entered["loop"] = True

    monkeypatch.setattr(frontend, "getLogger", lambda _name: _Logger())
    monkeypatch.setattr(frontend, "FrontEndApp", lambda _logger: _App())
    monkeypatch.setattr(frontend, "validateOSVersion", lambda _logger: True)
    monkeypatch.setattr(frontend.wx, "GetTopLevelWindows", lambda: [object()])
    monkeypatch.setattr(frontend, "show_fatal", lambda _message: entered.setdefault("fatal", True))
    frontend.main()
    assert entered.get("loop") is True
    assert "fatal" not in entered
    assert wx.OK


def _ensure_wx_or_stub(tmp_path):
    """Use real wxPython when it imports. Otherwise a MainLoop stub.

    This image has no wxPython wheel. The stub still runs the launchpad code
    through App.MainLoop. A machine with wxPython uses that instead, under
    xvfb when there is no DISPLAY.
    """
    if importlib.util.find_spec("wx") is not None:
        return False
    stub = tmp_path / "wxstub"
    (stub / "wx").mkdir(parents=True)
    (stub / "wx" / "__init__.py").write_text(
        "\n".join(
            [
                "class App:",
                "    running = True",
                "    def __init__(self, *_args, **_kwargs):",
                "        App.running = True",
                "    def MainLoop(self):",
                "        while App.running and CallLater._queue:",
                "            CallLater._queue.pop(0)()",
                "class Frame:",
                "    def __init__(self, *_args, **_kwargs):",
                "        pass",
                "    def Show(self):",
                "        return True",
                "    def Close(self):",
                "        App.running = False",
                "class CallLater:",
                "    _queue = []",
                "    def __init__(self, _delay, callback):",
                "        CallLater._queue.append(callback)",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return stub


def test_launchpad_startup_reaches_mainloop(tmp_path, monkeypatch):
    monkeypatch.setenv("SIGHTHOUND_TEST_UI", "1")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    script = str(ROOT / "FrontEndLaunchpad.py")
    command = [sys.executable, script]
    env = os.environ.copy()
    stub = _ensure_wx_or_stub(tmp_path)
    if stub:
        env["PYTHONPATH"] = str(stub) + os.pathsep + env.get("PYTHONPATH", "")
    elif not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        command = ["xvfb-run", "-a", *command]
    ran = subprocess.run(
        command,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    candidates = (
        tmp_path / "appdata" / "Sighthound Video" / "logs" / "startup.log",
        tmp_path / "share" / "Sighthound Video" / "logs" / "startup.log",
    )
    log = next((path for path in candidates if path.is_file()), candidates[-1])
    detail = (ran.stdout or "") + (ran.stderr or "")
    if log.is_file():
        detail += "\n" + log.read_text(encoding="utf-8")
    assert ran.returncode == 0, detail
    assert log.is_file(), detail
    assert "entered MainLoop" in log.read_text(encoding="utf-8")
