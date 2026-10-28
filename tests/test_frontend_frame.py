"""Construct the main window and simple dialogs under wxPython.

These cases used to show up only after the frozen executable reached PostInit.
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Dialog classes that need a live camera, a saved query, or a running backend.
_SKIP_DIALOGS = {
    "QueryEditorDialog",
    "RemoveCameraDialog",
    "RenameCameraDialog",
    "CameraSetupWizard",
    "FullScreenView",
    "_PacketCaptureBox",
    "ExportMultipleClipsDialog",
    "ExportTimeRangeDialog",
    "ExportSingleClipDialog",
    "WebhookEditor",
}


def _ensure_display():
    """Give wx a display on Linux. Windows and macOS already have a desktop.

    Xvfb is a Linux program. Starting it on Windows raises FileNotFoundError
    before wx.App is created, so the frame test never runs there.
    """
    if sys.platform != "linux":
        return None
    if os.environ.get("DISPLAY"):
        return None
    xvfb = "Xvfb"
    display = ":97"
    proc = subprocess.Popen(
        [xvfb, display, "-screen", "0", "1400x900x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)
    if proc.poll() is not None:
        pytest.skip("Xvfb did not start and DISPLAY is unset")
    os.environ["DISPLAY"] = display
    return proc


@pytest.fixture(scope="module")
def wx_app(tmp_path_factory):
    proc = _ensure_display()
    wx = pytest.importorskip("wx")
    data = tmp_path_factory.mktemp("vigilcam-data")
    os.environ["XDG_DATA_HOME"] = str(data)
    os.environ["LOCALAPPDATA"] = str(data)
    os.environ.setdefault("HOME", str(data / "home"))
    (data / "home").mkdir(exist_ok=True)

    from frontEnd.FrontEndApp import FrontEndApp
    from vitaToolbox.loggingUtils.LoggingUtils import getLogger

    app = FrontEndApp(getLogger("frame-test"))
    # Parent/sizer mismatches are wx assertions. Make them fail the test.
    app.SetAssertMode(wx.APP_ASSERT_EXCEPTION)
    yield app
    app.Destroy()
    if proc is not None:
        proc.terminate()


class _FakeBackend:
    """Enough of BackEndClient for the window to build with no cameras."""

    def __init__(self, storage, video):
        self._storage = storage
        self._video = video

    def getTimePreferences(self):
        return (False, True)

    def getStorageLocation(self):
        return self._storage

    def getVideoLocation(self):
        return self._video

    def getLicense(self):
        return self.getLicenseData()

    def getLicenseData(self):
        return {
            "Edition": "Starter Edition",
            "Expires": 0,
            "Trial": False,
            "Serial Number": "",
            "Support Expires": 0,
            "Cameras": -1,
        }

    def getUpnpDictRevNum(self):
        return None

    def getOnvifDictRevNum(self):
        return None

    def activeCameraSearch(self, *_args, **_kwargs):
        return None

    def getLocalCameraNames(self):
        return []

    def launchedByService(self):
        return False

    def getCameraLocations(self):
        return []

    def getSavedQueries(self):
        return []

    def getRules(self):
        return []

    def getRuleNames(self):
        return []

    def getRule(self, _name=""):
        class _Rule:
            def getSchedule(self):
                return {
                    "dayType": "Every day",
                    "customDays": [],
                    "is24Hours": True,
                    "startHour": 0,
                    "startMin": 0,
                    "stopHour": 0,
                    "stopMin": 0,
                }

            def getName(self):
                return _name or "People"

        return _Rule()

    def memstoreGet(self, *_args, **_kwargs):
        return [None]

    def __getattr__(self, name):
        """Unread backend calls during UI setup return an empty result."""

        def method(*_args, **_kwargs):
            if name.startswith(("is", "has", "want")):
                return False
            if "Count" in name or name.endswith("Num"):
                return 0
            return []

        return method


def test_ensure_display_does_not_launch_xvfb_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    def boom(*_args, **_kwargs):
        raise AssertionError("Xvfb must not be started on Windows")

    monkeypatch.setattr("tests.test_frontend_frame.subprocess.Popen", boom)
    assert _ensure_display() is None


def test_ensure_display_starts_xvfb_on_linux_without_a_display(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    started = []

    class _Proc:
        def poll(self):
            return None

    def fake_popen(command, **_kwargs):
        started.append(command)
        return _Proc()

    monkeypatch.setattr("tests.test_frontend_frame.subprocess.Popen", fake_popen)
    monkeypatch.setattr("tests.test_frontend_frame.time.sleep", lambda _seconds: None)
    previous = os.environ.get("DISPLAY")
    try:
        proc = _ensure_display()
        assert proc is not None
        assert started[0][0] == "Xvfb"
        assert os.environ["DISPLAY"] == ":97"
    finally:
        if previous is None:
            os.environ.pop("DISPLAY", None)
        else:
            os.environ["DISPLAY"] = previous


def test_kill_process_tree_stops_parent_and_child():
    from appCommon.process_cleanup import kill_process_tree

    child_code = "import time; time.sleep(60)"
    parent_code = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', %r]); "
        "time.sleep(60)" % child_code
    )
    parent = subprocess.Popen([sys.executable, "-c", parent_code])
    deadline = time.time() + 5
    children = []
    while time.time() < deadline:
        try:
            import psutil
            children = psutil.Process(parent.pid).children()
        except Exception:
            children = []
        if children:
            break
        time.sleep(0.05)
    assert children, "child process was not started"
    child_pid = children[0].pid
    kill_process_tree(parent.pid)
    parent.wait(timeout=5)
    assert parent.poll() is not None
    import psutil
    assert not psutil.pid_exists(child_pid) or not psutil.Process(child_pid).is_running()


def test_postinit_failure_kills_the_backend_it_launched(monkeypatch):
    from frontEnd import FrontEndApp as frontend_app

    killed = []

    def fake_shutdown(self):
        killed.append(getattr(self._backEndProc, "pid", None))

    monkeypatch.setattr(frontend_app.FrontEndApp, "shutdown_launched_backend", fake_shutdown)
    monkeypatch.setattr(frontend_app, "show_fatal", lambda *_args, **_kwargs: None)

    def boom(self):
        self._backEndProc = type("Proc", (), {"pid": 4242})()
        raise RuntimeError("PostInit boom")

    monkeypatch.setattr(frontend_app.FrontEndApp, "PostInit", boom)
    monkeypatch.setattr(frontend_app, "validateOSVersion", lambda _logger: True)
    monkeypatch.setattr(frontend_app, "console_requested", lambda: True)
    frontend_app.main()
    assert killed == [4242]


def _is_bundled_bitmap(path):
    text = str(path).replace("\\", "/")
    return (
        "frontEnd/bmps/" in text
        or "vitaToolbox/wx/bmps/" in text
        or "/icons/" in text
        or text.startswith("icons/")
    )


def _use_uniform_bitmaps():
    """Load every bundled bitmap as the same 8x8 image.

    Git LFS checkouts mix real files and pointer files. Hover buttons require
    every state of a button to share one size, and a pointer is not a PNG.
    The widget tree still runs the real sizer and drawing code.
    """
    import wx
    from PIL import Image

    real_open = Image.open

    def open_image(fp, *args, **kwargs):
        if isinstance(fp, str) and _is_bundled_bitmap(fp):
            return Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        return real_open(fp, *args, **kwargs)

    Image.open = open_image
    real_bitmap = wx.Bitmap
    real_image = wx.Image

    def bitmap(*args, **kwargs):
        if args and isinstance(args[0], str) and _is_bundled_bitmap(args[0]):
            return real_bitmap(8, 8)
        return real_bitmap(*args, **kwargs)

    def image(*args, **kwargs):
        if args and isinstance(args[0], str) and _is_bundled_bitmap(args[0]):
            return real_image(8, 8)
        return real_image(*args, **kwargs)

    bitmap.FromBuffer = real_bitmap.FromBuffer
    bitmap.FromBufferRGBA = real_bitmap.FromBufferRGBA
    bitmap.FromRGBA = getattr(real_bitmap, "FromRGBA", real_bitmap.FromBufferRGBA)
    wx.Bitmap = bitmap
    wx.Image = image


def test_main_frame_and_simple_dialogs_construct(wx_app, tmp_path):
    import wx
    _use_uniform_bitmaps()
    from frontEnd.FrontEndFrame import FrontEndFrame

    storage = tmp_path / "db"
    video = tmp_path / "video"
    storage.mkdir()
    video.mkdir()
    client = _FakeBackend(str(storage), str(video))
    frame = FrontEndFrame("VigilCam", client)
    assert frame.GetTitle() == "VigilCam"
    frame.Destroy()

    failures = []
    built = []
    for path in sorted((ROOT / "frontEnd").rglob("*.py")):
        module_name = "frontEnd." + ".".join(path.relative_to(ROOT / "frontEnd").with_suffix("").parts)
        if module_name.endswith(".__init__"):
            module_name = module_name[: -len(".__init__")]
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:
            failures.append("%s import: %s" % (module_name, exc))
            continue
        for name, cls in vars(module).items():
            if name in _SKIP_DIALOGS or not inspect.isclass(cls):
                continue
            if not issubclass(cls, wx.Dialog) or cls is wx.Dialog:
                continue
            if cls.__module__ != module.__name__:
                continue
            try:
                signature = inspect.signature(cls.__init__)
            except (TypeError, ValueError):
                continue
            required = [
                param
                for param in list(signature.parameters.values())[1:]
                if param.default is inspect.Parameter.empty
                and param.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if len(required) > 1:
                continue
            parent = wx.Frame(None)
            try:
                if len(required) == 1:
                    dialog = cls(parent)
                else:
                    dialog = cls()
                dialog.Destroy()
                built.append("%s.%s" % (module_name, name))
            except Exception as exc:
                text = str(exc)
                assertion = (
                    exc.__class__.__name__ in ("wxAssertionError", "PyAssertionError")
                    or "CheckExpectedParentIs" in text
                    or "SetContainingWindow" in text
                )
                if assertion or "unexpected type 'float'" in text or "arguments did not match" in text:
                    failures.append("%s.%s: %s" % (module_name, name, text.splitlines()[0]))
                else:
                    # A dialog that needs live data is not a Phoenix type error.
                    continue
            finally:
                parent.Destroy()
    assert not failures, "wx construction failed:\n" + "\n".join(failures)
    assert built, "no dialogs were constructed"


class _FakeDataManager:
    def getCameraLocations(self):
        return ["Driveway Center"]

    def getProcSize(self, _location=""):
        return (0, 0)

    def listFaces(self):
        return []

    def getFaceThreshold(self):
        return 0.4

    def listPlateWatchlist(self):
        return []


def test_camera_setup_wizard_constructs(wx_app):
    """Add Camera builds every wizard page. A sizer/parent mismatch asserts."""
    import wx
    _use_uniform_bitmaps()
    from frontEnd.CameraSetupWizard import CameraSetupWizard

    parent = wx.Frame(None, title="wizard-parent")
    client = _FakeBackend("", "")
    wizard = None
    try:
        wizard = CameraSetupWizard(parent, client, _FakeDataManager(), "")
        assert wizard.GetTitle() == "Camera Setup"
        assert wizard._welcomeScreen.GetParent() is wizard
        assert wizard._manualSettingsScreen.GetParent() is wizard
        assert wizard._finishScreen.GetParent() is wizard
    finally:
        if wizard is not None:
            wizard.CleanupTimers()
            wizard.Destroy()
        parent.Destroy()


def test_rule_editor_and_subdialogs_construct(wx_app):
    """New Rule builds the editor, each default rule, and the rule dialogs."""
    import wx
    _use_uniform_bitmaps()
    from appCommon.CommonStrings import kSearchViewDefaultRules
    from backEnd.SavedQueryDataModel import SavedQueryDataModel
    from frontEnd.QueryEditorDialog import QueryEditorDialog
    from frontEnd.RuleScheduleDialog import RuleScheduleDialog
    from frontEnd.constructionComponents.ResponseConfigPanel import (
        ResponseConfigPanel,
        WebhookEditor,
    )
    from frontEnd.constructionComponents.TargetConfigPanel import TargetConfigPanel
    from frontEnd.constructionComponents.WhereConfigPanel import WhereConfigPanel

    parent = wx.Frame(None, title="rule-parent")
    client = _FakeBackend("", "")
    data = _FakeDataManager()
    created = []
    try:
        queries = [SavedQueryDataModel("")]
        for name in kSearchViewDefaultRules:
            query = SavedQueryDataModel(name)
            query.getVideoSource().setLocationName("Driveway Center")
            queries.append(query)
        for query in queries:
            dialog = QueryEditorDialog(
                parent, data, client, query, list(kSearchViewDefaultRules),
                ["Driveway Center"],
            )
            created.append(dialog)
            assert dialog.GetTitle()
        schedule = RuleScheduleDialog(parent, "People", client, False)
        created.append(schedule)
        webhook = WebhookEditor(parent, "https://example.test/hook", "application/json", "{}")
        created.append(webhook)
        sample = queries[0]
        video = created[0]._constructionView._videoWindow
        target_params = inspect.signature(TargetConfigPanel.__init__).parameters
        if len(target_params) > 4:
            created.append(TargetConfigPanel(parent, video, sample.getTargets()[0], sample))
        else:
            created.append(TargetConfigPanel(parent, video, sample.getTargets()[0]))
        where_params = inspect.signature(WhereConfigPanel.__init__).parameters
        if len(where_params) > 4:
            created.append(WhereConfigPanel(
                parent, video, sample.getTriggers()[0], sample.getTriggers()[1],
            ))
        else:
            created.append(WhereConfigPanel(parent, video, sample.getTriggers()[0]))
        created.append(ResponseConfigPanel(parent, sample, client, data))
        try:
            from frontEnd.AttributeLibraryDialog import AttributeLibraryDialog
        except ImportError:
            AttributeLibraryDialog = None
        if AttributeLibraryDialog is not None:
            created.append(AttributeLibraryDialog(parent, data))
    finally:
        for window in reversed(created):
            if window is not None:
                window.Destroy()
        parent.Destroy()
