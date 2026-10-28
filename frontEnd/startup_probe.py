"""Tiny wx startup used to prove FrontEndLaunchpad reaches MainLoop.

Set SIGHTHOUND_TEST_UI=1. The probe shows a frame, enters MainLoop, records
that in startup.log, and closes. It does not start the backend.
"""

from __future__ import annotations

import wx

from appCommon.startuplog import log_startup


def run_startup_probe() -> None:
    log_startup("startup probe creating wx.App")
    app = wx.App(False)
    frame = wx.Frame(None, title="Sighthound Video startup probe")
    frame.Show()
    entered = {"ok": False}

    def mark():
        entered["ok"] = True
        log_startup("entered MainLoop")
        frame.Close()

    wx.CallLater(100, mark)
    log_startup("calling MainLoop")
    app.MainLoop()
    log_startup("MainLoop returned")
    if not entered["ok"]:
        raise SystemExit("MainLoop returned before the startup probe ran")
