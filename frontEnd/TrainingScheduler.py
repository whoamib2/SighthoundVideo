"""Automatic retrain of the truck-type classifier (nightly / after N samples).

A wx timer checks once a minute on the UI thread (cheap file reads only);
training and the optional re-scan run on a low-priority daemon thread.
"""

import datetime
import threading
import time

import wx

from appCommon.CommonStrings import kFrontEndLogName
from backEnd import training_samples as ts
from vitaToolbox.loggingUtils.LoggingUtils import getLogger

_kCheckMs = 60 * 1000


def _lowerThreadPriority():
    try:
        import ctypes

        kernel = ctypes.windll.kernel32
        kernel.SetThreadPriority(kernel.GetCurrentThread(), -2)  # BELOW_NORMAL
    except Exception:
        pass


def installAutoRetrain(frame):
    state = {"running": False}
    timer = wx.Timer(frame)
    log = getLogger(kFrontEndLogName)

    def onTimer(_event):
        if state["running"]:
            return
        try:
            now = datetime.datetime.now()
            reason = ts.auto_retrain_due(now)
        except Exception:
            log.exception("auto retrain check failed")
            return
        if not reason:
            return
        state["running"] = True
        settings = ts.get_settings()

        def work():
            _lowerThreadPriority()
            try:
                counts = ts.retrain_carrier_model()
                log.info("auto retrain (%s) succeeded: %s", reason, counts)
                if settings.get("rescan_after"):
                    from backEnd.rescan import run_rescan
                    from frontEnd.TrainingDialogs import rescanPaths

                    objDb, clipDb, videoDir = rescanPaths(frame)
                    endMs = int(time.time() * 1000)
                    result = run_rescan(objDb, clipDb, videoDir, endMs - 7 * 86400000, endMs,
                                        pause_s=0.01)
                    log.info("auto re-scan after retrain: %r", result)
                    from frontEnd.TrainingDialogs import refreshSearch

                    wx.CallAfter(refreshSearch, frame)
            except Exception as exc:
                log.warning("auto retrain (%s) failed: %s", reason, exc)
            finally:
                if reason == "nightly":
                    ts.mark_nightly_checked(datetime.datetime.now())
                state["running"] = False

        threading.Thread(target=work, name="auto-retrain", daemon=True).start()

    frame.Bind(wx.EVT_TIMER, onTimer, timer)
    timer.Start(_kCheckMs)
    frame._autoRetrainTimer = timer
