"""Bytes from C APIs must be text before they are joined to strings."""

from __future__ import annotations

import sys

from vitaToolbox.sysUtils.GetProcessorInfo import getProcessorInfo
from vitaToolbox.sysUtils.MachineId import _hash_machine_id
from vitaToolbox.wx.GLCanvasSV import _gl_text
from vitaToolbox.wx.GLCanvasSV import getGLAttribute


def test_gl_get_string_bytes_are_text(monkeypatch):
    import vitaToolbox.wx.GLCanvasSV as canvas

    monkeypatch.setattr(canvas.gl, "glGetString", lambda _attr: b"NVIDIA Corporation\x00")
    vendor = getGLAttribute(0)
    assert vendor == "NVIDIA Corporation"
    assert isinstance("glVendor=\"" + vendor, str)

    monkeypatch.setattr(canvas.gl, "glGetString", lambda _attr: None)
    assert getGLAttribute(0) == "(none)"
    assert _gl_text("2.1.0") == "2.1.0"
    assert _gl_text(b"") == "(none)"


def test_machine_id_hash_accepts_text_and_bytes():
    assert _hash_machine_id("ABC-def") == _hash_machine_id(b"ABC-def")
    assert len(_hash_machine_id("ABC-def")) == 40


def test_processor_info_does_not_use_the_python2_registry_module():
    if sys.platform == "win32":
        text = getProcessorInfo()
        assert isinstance(text, str)
        assert "Num procs:" in text
    else:
        assert getProcessorInfo() == sys.platform


def test_web_auth_and_process_output_are_text():
    from backEnd.WebServer import _process_output_text
    from backEnd.WebServer import make_auth

    assert _process_output_text(b"openssl ok\n") == "openssl ok"
    basic = make_auth("ada", "secret")
    digest = make_auth("ada", "secret", "VigilCam")
    assert isinstance(basic, str) and isinstance(digest, str)
    assert not basic.startswith("b'") and "b'" not in digest
    assert basic.startswith("ada:{SHA}")
