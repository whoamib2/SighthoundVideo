"""Hidden self-test hook for the running GUI.

Create the file ``selftest.request`` in the VigilCam data folder and the
running app logs, to VigilCam.log, what the Search view really shows: the
timeline's coverage and painted green pixels, and whether the newest result
loads and paints a frame. Nothing is changed except the selected result.
"""

import os
import sys
import threading
import time
import traceback

import wx

from appCommon.CommonStrings import kFrontEndLogName
from vitaToolbox.loggingUtils.LoggingUtils import getLogger

_kPollMs = 2000
_kRequestName = "selftest.request"


def _t(ms):
    if ms is None:
        return "None"
    return time.strftime("%H:%M:%S", time.localtime(ms / 1000.0))


_kBeatMs = 100
_stall = {"last": time.monotonic(), "max": 0.0, "reported": False,
          "mainId": None}


def installStallWatchdog(frame, thresholdSec=1.0):
    """Log the UI thread's stack whenever it is blocked > thresholdSec.

    A wx timer stamps a heartbeat every 100 ms; a daemon thread checks it.
    """
    log = getLogger(kFrontEndLogName)
    _stall["mainId"] = threading.get_ident()
    _stall["last"] = time.monotonic()
    beat = wx.Timer(frame)

    def onBeat(_event):
        now = time.monotonic()
        gap = now - _stall["last"]
        if gap > _stall["max"]:
            _stall["max"] = gap
        if _stall["reported"]:
            log.warning("UI thread was blocked for %.1f s", gap)
            _stall["reported"] = False
        _stall["last"] = now

    frame.Bind(wx.EVT_TIMER, onBeat, beat)
    beat.Start(_kBeatMs)
    frame._stallBeatTimer = beat

    def watch():
        while True:
            time.sleep(0.25)
            gap = time.monotonic() - _stall["last"]
            if gap > thresholdSec and not _stall["reported"]:
                _stall["reported"] = True
                stackFrame = sys._current_frames().get(_stall["mainId"])
                stack = "".join(traceback.format_stack(stackFrame)[-14:]) \
                    if stackFrame else "?"
                log.warning("UI thread blocked > %.1f s, stack:\n%s",
                            gap, stack)
    threading.Thread(target=watch, name="UiStallWatchdog", daemon=True).start()


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
    # Click through several results quickly (like a user would), then
    # report each one and the longest UI stall seen.
    order = sorted(pool, key=lambda i: clips[i].startTime)
    picks = order[-1:] + order[-6:-1][::-1] + order[:2]
    _stall["max"] = 0.0
    for n, num in enumerate(picks):
        wx.CallLater(1 + n * 700, _select, frame, num)
    wx.CallLater(len(picks) * 700 + 4000, _after, frame, picks[-1])


def _select(frame, num):
    model = frame._searchView._resultsModel
    clip = model.getMatchingClips()[num]
    getLogger(kFrontEndLogName).info("SELFTEST selecting result %d %s-%s",
                                     num, _t(clip.startTime), _t(clip.stopTime))
    model.setCurrentClipNum(num)


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
    log.info("SELFTEST maxStallMs=%d", int(_stall["max"] * 1000))
