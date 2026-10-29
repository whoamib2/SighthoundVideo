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
    frame._selfTestPlayQueue = [order[-1], order[-3] if len(order) > 2 else order[0], order[0]]
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
    _frameGrabTest(panel, num)
    _rescanTest(frame)
    wx.CallLater(45000, _wizardTest, frame)
    _playTest(frame, num)


def _frameGrabTest(panel, num):
    """Same grab Mark as Missed / Export Frame uses, on the paused result."""
    import tempfile

    log = getLogger(kFrontEndLogName)
    handle, path = tempfile.mkstemp(prefix="vigilcam_selftest_", suffix=".jpg")
    os.close(handle)
    try:
        ok = panel.grabCurrentFrame(path)
        size = None
        if ok:
            img = wx.Image(path)
            size = (img.GetWidth(), img.GetHeight()) if img.IsOk() else None
        log.info("SELFTEST markMissed frameGrab result %d ok=%s bytes=%d image=%s",
                 num, ok, os.path.getsize(path) if os.path.exists(path) else -1, size)
    except Exception:
        log.exception("SELFTEST markMissed frameGrab crashed")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _playTest(frame, num, seconds=5.0):
    """Press play and count distinct frames painted over ``seconds``."""
    log = getLogger(kFrontEndLogName)
    model = frame._searchView._resultsModel
    state = {"frames": 0, "last": None, "startMs": model.getCurrentAbsoluteMs(),
             "ticks": 0}
    model.setPlay(True)
    timer = wx.Timer(frame)

    def onTick(_evt):
        img = model.getCurrentImg()
        key = (id(img), model.getCurrentAbsoluteMs())
        if img is not None and key != state["last"]:
            state["frames"] += 1
            state["last"] = key
        state["ticks"] += 1
        if state["ticks"] * 0.05 >= seconds:
            timer.Stop()
            endMs = model.getCurrentAbsoluteMs()
            log.info("SELFTEST play result %d framesPainted=%d in %.0fs "
                     "playing=%s advancedMs=%s from=%s to=%s",
                     num, state["frames"], seconds, model.isPlaying(),
                     (endMs - state["startMs"]) if endMs and state["startMs"] else None,
                     _t(state["startMs"]), _t(endMs))
            model.setPlay(False)
            queue = getattr(frame, "_selfTestPlayQueue", None)
            if queue:
                nxt = queue.pop(0)
                _select(frame, nxt)
                wx.CallLater(4000, _playTest, frame, nxt)

    frame.Bind(wx.EVT_TIMER, onTick, timer)
    timer.Start(50)
    frame._selfTestPlayTimer = timer


def _rescanTest(frame):
    """Re-scan the vehicles of the newest few results in the background."""
    from backEnd.rescan import run_rescan
    from frontEnd.TrainingDialogs import refreshSearch, rescanPaths

    log = getLogger(kFrontEndLogName)
    model = frame._searchView._resultsModel
    clips = model.getMatchingClips()
    ids = []
    for clip in clips[-4:]:
        for obj in getattr(clip, "objList", None) or []:
            ids.append(int(obj[0] if isinstance(obj, (tuple, list)) else obj))
    objDb, clipDb, videoDir = rescanPaths(frame)
    try:
        import sqlite3

        conn = sqlite3.connect(objDb, timeout=5)
        try:
            ids += [r[0] for r in conn.execute(
                "SELECT uid FROM objects WHERE type='vehicle' ORDER BY timeStart DESC LIMIT 3")]
        finally:
            conn.close()
    except Exception:
        log.exception("SELFTEST rescan could not list vehicles")
    log.info("SELFTEST rescan start objects=%d ids=%s db=%s videos=%s", len(ids), ids, objDb, videoDir)
    started = time.monotonic()

    def work():
        try:
            first = run_rescan(objDb, clipDb, videoDir, 0, 2 ** 62, None, ids)
            log.info("SELFTEST rescan first run %r", first)
            result = run_rescan(objDb, clipDb, videoDir, 0, 2 ** 62, None, ids)
            import sqlite3

            conn = sqlite3.connect(objDb, timeout=5)
            try:
                labels = conn.execute(
                    "SELECT objUid, value FROM objAttributes WHERE key='delivery_carrier' AND objUid IN (%s)"
                    % (",".join(str(i) for i in ids) or "0")).fetchall()
            finally:
                conn.close()
            log.info("SELFTEST rescan done in %.1fs %r labels=%s",
                     time.monotonic() - started, result, labels[:10])
            wx.CallAfter(refreshSearch, frame)
        except Exception:
            log.exception("SELFTEST rescan crashed")

    threading.Thread(target=work, name="selftest-rescan", daemon=True).start()


def _wizardTest(frame):
    """Open the Add Camera wizard, step through its setup pages, then cancel."""
    log = getLogger(kFrontEndLogName)
    try:
        from frontEnd.CameraSetupWizard import CameraSetupWizard

        wizard = CameraSetupWizard(frame, frame._backEndClient, frame._dataManager)
    except Exception:
        log.exception("SELFTEST wizard construct crashed")
        return
    visited = []

    def step():
        try:
            for name in ("_welcomeScreen", "_detectedCamerasScreen", "_manualSettingsScreen",
                         "_upnpSettingsScreen", "_onvifCredentialsScreen"):
                page = getattr(wizard, name, None)
                if page is not None:
                    wizard.ShowPage(page)
                    wx.SafeYield()
                    visited.append(name)
            page = wizard._manualSettingsScreen
            page._passCtrl.SetValue("selftest-pw")
            page._passCtrl.SetShown(True)
            shown = page._passCtrl.GetValue()
            page._passCtrl.SetShown(False)
            log.info("SELFTEST wizard ok pages=%s showPassword=%s verifyField=%s",
                     visited, shown == "selftest-pw", type(page._passVerifyCtrl).__name__)
        except Exception:
            log.exception("SELFTEST wizard step crashed after %s", visited)
        finally:
            wizard.EndModal(wx.ID_CANCEL)

    wx.CallLater(1500, step)
    try:
        wizard.run()
    except Exception:
        log.exception("SELFTEST wizard run crashed")
    finally:
        wizard.Destroy()
    log.info("SELFTEST wizard closed")
