"""Hidden self-test hook for the running GUI.

Create the file ``selftest.request`` in the VigilCam data folder and the
running app logs, to VigilCam.log, what the Search view really shows: the
timeline's coverage and painted green pixels, and whether the newest result
loads and paints a frame. Nothing is changed except the selected result.
"""

import os
import time

import wx

from appCommon.CommonStrings import kFrontEndLogName
from vitaToolbox.loggingUtils.LoggingUtils import getLogger

_kPollMs = 2000
_kRequestName = "selftest.request"


def _t(ms):
    if ms is None:
        return "None"
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000.0))


def installSelfTestHook(frame, dataDir):
    path = os.path.join(dataDir, _kRequestName)
    timer = wx.Timer(frame)

    def onTimer(_event):
        if not os.path.exists(path):
            return
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            _run(frame)
        except Exception:
            getLogger(kFrontEndLogName).exception("SELFTEST crashed")

    frame.Bind(wx.EVT_TIMER, onTimer, timer)
    timer.Start(_kPollMs)
    frame._selfTestTimer = timer


def _greenPixels(bitmap):
    if bitmap is None or not bitmap.IsOk():
        return -1
    img = bitmap.ConvertToImage()
    data = img.GetData()
    green = 0
    for i in range(0, len(data), 3):
        r, g, b = data[i], data[i + 1], data[i + 2]
        if g > 150 and r < 200 and b < 150 and g > r:
            green += 1
    return green


def _run(frame):
    log = getLogger(kFrontEndLogName)
    view = frame._searchView
    model = view._resultsModel
    panel = view._videoPanel
    timeline = panel._timelineControl
    log.info("SELFTEST view=%s didSearch=%s midnight=%s clips=%d",
             type(frame._currentView).__name__, model.didSearch(),
             _t(model.getMidnightMs()), len(model.getMatchingClips()))
    avail = model.getAvailableVideoRanges()
    log.info("SELFTEST availRanges=%d %s", len(avail),
             [(_t(a), _t(b)) for a, b in avail[-6:]])
    dm = getattr(timeline, "_divisionMap", None) or []
    log.info("SELFTEST timeline shown=%s size=%s midnight=%s divisions=%d "
             "availDivs=%d clipDivs=%d greenPixels=%d",
             timeline.IsShownOnScreen(), tuple(timeline.GetSize()),
             _t(timeline._curMidnightMs), len(dm),
             sum(1 for d in dm if isinstance(d, tuple)),
             sum(1 for d in dm if isinstance(d, int)),
             _greenPixels(getattr(timeline, "_timeBarBitmap", None)))
    timeline.recreateTimeline()
    dm = timeline._divisionMap
    log.info("SELFTEST after rebuild availDivs=%d clipDivs=%d greenPixels=%d",
             sum(1 for d in dm if isinstance(d, tuple)),
             sum(1 for d in dm if isinstance(d, int)),
             _greenPixels(getattr(timeline, "_timeBarBitmap", None)))
    clips = model.getMatchingClips()
    if not clips:
        return
    def recorded(c):
        return any(a <= c.startTime and c.stopTime <= b for a, b in avail)
    pool = [i for i in range(len(clips)) if recorded(clips[i])] or \
        list(range(len(clips)))
    newest = max(pool, key=lambda i: clips[i].startTime)
    log.info("SELFTEST selecting result %d %s-%s", newest,
             _t(clips[newest].startTime), _t(clips[newest].stopTime))
    model.setCurrentClipNum(newest)
    wx.CallLater(4000, _after, frame, newest)


def _after(frame, num):
    log = getLogger(kFrontEndLogName)
    view = frame._searchView
    model = view._resultsModel
    panel = view._videoPanel
    img = model.getCurrentImg()
    win = panel._videoWindow
    data = win.GetImageData() if hasattr(win, "GetImageData") else None
    lit = -1
    if img is not None and getattr(img, "rgb", None):
        sample = img.rgb[::997]
        lit = sum(1 for v in sample if v > 16) * 100 // max(1, len(sample))
    log.info("SELFTEST result %d loaded=%s img=%s litPct=%s window=%s "
             "windowShown=%s painted=%s slider=%s time=%s",
             num, model.isVideoLoaded(),
             img and (img.width, img.height), lit, type(win).__name__,
             win.IsShownOnScreen(), data and (data[1], data[2]),
             (panel._slider._minValue, panel._slider._maxValue),
             _t(model.getCurrentAbsoluteMs()))
