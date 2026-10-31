"""Search 'When:' row: calendar opens on click, stays on the monitor, arrows
repeat without crashing; DiskCleaner thumb stats with <cam>/thumbs."""

import datetime

import pytest

from vitaToolbox.wx.VitaDatePickerCtrl import popup_position


def test_popup_position_stays_on_monitor():
    area = (1920, 0, 1920, 1040)  # second monitor
    assert popup_position((2000, 300), (200, 220), area, 20) == (1920, 300)
    # Near the bottom: flips above the control.
    assert popup_position((3000, 1000), (200, 220), area, 20) == (2900, 760)
    assert popup_position((3830, 100), (200, 220), area) == (3640, 100)


def _wxapp():
    import wx
    return wx.App(False)


def _click(wx, button):
    w, h = button.GetSize()
    for kind, handler in ((wx.wxEVT_LEFT_DOWN, button.OnMouseDown), (wx.wxEVT_LEFT_UP, button.OnMouseUp)):
        evt = wx.MouseEvent(kind)
        evt.SetPosition(wx.Point(w // 2, h // 2))
        handler(evt)


def _scenario_click(wx):
    import wx.adv  # noqa: F401
    from vitaToolbox.wx import VitaDatePickerCtrl as mod

    today = datetime.date.today()
    target = today.replace(day=1) if today.day != 1 else today
    recorded = {target}
    frame = wx.Frame(None)
    panel = wx.Panel(frame)
    ctrl = mod.VitaDatePickerCtrl(panel, today, None, "today", lambda d: d in recorded)
    frame.Show()
    frame.Layout()
    seen = {}
    changes = []
    ctrl.Bind(wx.adv.EVT_DATE_CHANGED, lambda e: changes.append(ctrl.GetValue()))

    def inspect():
        popups = [w for w in wx.GetTopLevelWindows() if isinstance(w, mod._CalendarPopup)]
        seen["open"] = bool(popups) and popups[0].IsModal()
        popup = popups[0]
        popup._updateMonth()
        index = (target - popup._firstShownDate).days
        button = popup._dayButtons[index]
        seen["tip"] = button.GetToolTipText()
        evt = wx.CommandEvent(wx.wxEVT_BUTTON, button.GetId())
        evt.SetEventObject(button)
        popup.OnDateButton(evt)

    wx.CallLater(300, inspect)
    _click(wx, ctrl._dateButton)
    assert seen.get("open") is True, "calendar did not stay open"
    assert seen["tip"] == "Recordings stored"
    assert ctrl.GetValue() == target
    if target != today:
        assert changes == [target]


def _scenario_arrows(wx):
    from vitaToolbox.wx.VitaDatePickerCtrl import VitaDatePickerCtrl

    frame = wx.Frame(None)
    ctrl = VitaDatePickerCtrl(wx.Panel(frame), datetime.date(2026, 10, 20))
    frame.Show()
    frame.Layout()
    _click(wx, ctrl._prevDayButton)  # used to raise TypeError in Timer.Start
    assert ctrl.GetValue() == datetime.date(2026, 10, 19)
    assert ctrl._prevDayButton.GetMinSize() == ctrl._nextDayButton.GetMinSize() or \
        ctrl._nextDayButton.GetMinSize() == ctrl._prevDayButton.GetBestSize()



# GUI scenarios run in a fresh process: a modal loop in a process where other
# tests already created and destroyed a wx.App crashes the interpreter.
def _run_scenario(name):
    import os
    import subprocess
    import sys

    pytest.importorskip("wx")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.run([sys.executable, os.path.abspath(__file__), name], cwd=root,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "SCENARIO OK" in proc.stdout, proc.stdout + proc.stderr


def test_click_on_date_label_opens_calendar_and_picks_day():
    _run_scenario("click")


def test_arrow_repeat_timer_accepts_float_delays():
    _run_scenario("arrows")


def test_disk_cleaner_ignores_non_numeric_time_folder():
    from backEnd.DiskCleaner import DiskCleaner

    cleaner = DiskCleaner.__new__(DiskCleaner)
    cleaner._thumbStats = {}

    class _Log:
        def error(self, *a, **k):
            raise AssertionError("should not log an error: %r" % (a,))

        warning = info = debug = lambda self, *a, **k: None

    cleaner._logger = _Log()
    cleaner._updateThumbsStats("Driveway Center", "Driveway Center", 10, 1)
    assert cleaner._thumbStats == {}
    cleaner._updateThumbsStats("Cam", "10000", 10, 1)
    assert cleaner._thumbStats == {"Cam": {"10000": (10, 1, False)}}


if __name__ == "__main__":
    import os
    import sys

    sys.path.insert(0, os.getcwd())
    import wx

    app = _wxapp()
    {"click": _scenario_click, "arrows": _scenario_arrows}[sys.argv[1]](wx)
    print("SCENARIO OK")
    for win in wx.GetTopLevelWindows():
        win.Destroy()
