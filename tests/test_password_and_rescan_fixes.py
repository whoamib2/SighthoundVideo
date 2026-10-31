"""Passwords are asked once with a Show toggle; re-scan runs back to back."""

import pathlib
import threading

import pytest

wx = pytest.importorskip("wx")

from backEnd import rescan  # noqa: E402

_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def app():
    application = wx.App(False)
    yield application


def test_password_ctrl_toggle_keeps_value(app):
    from frontEnd.PasswordCtrl import PasswordCtrl, makeShowPasswordToggle

    frame = wx.Frame(None)
    try:
        ctrl = PasswordCtrl(frame, -1, size=(200, -1))
        toggle = makeShowPasswordToggle(frame, ctrl)
        ctrl.SetValue("s3cret")
        assert ctrl._masked.IsShown() and not ctrl._plain.IsShown()
        ctrl.SetShown(True)
        assert ctrl.GetValue() == "s3cret" and ctrl._plain.IsShown()
        ctrl._plain.SetValue("changed")
        ctrl.SetShown(False)
        assert ctrl.GetValue() == "changed"
        assert toggle.GetLabel() == "Show password"
    finally:
        frame.Destroy()


@pytest.mark.parametrize("name", ["CameraSetupWizard.py", "EmailSetupDialog.py", "FtpSetupDialog.py"])
def test_password_not_asked_twice(name):
    source = (_ROOT / "frontEnd" / name).read_text(encoding="utf-8")
    assert '-1, "Verify password:")' not in source and "_kVerifyPasswordLabelStr," not in source
    assert "_kPasswordsMustMatchStr, " not in source.replace("_kPasswordsMustMatchStr = ", "")
    assert "makeShowPasswordToggle(" in source


def test_rescan_runs_are_serialized(tmp_path, monkeypatch):
    active = []
    overlap = []

    def fake(*args, **kwargs):
        active.append(1)
        if len(active) > 1:
            overlap.append(1)
        threading.Event().wait(0.05)
        active.pop()
        return rescan.RescanResult()

    monkeypatch.setattr(rescan, "_run_rescan", fake)
    threads = [threading.Thread(target=rescan.run_rescan, args=("a", "b", "c", 0, 1)) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not overlap


def test_default_pipeline_is_cached_carrier_only():
    first = rescan._default_pipeline()
    assert rescan._default_pipeline() is first
    assert first.plate_detector is None and first.face_detector is None
