"""Search playback boxes, recording-day calendar, tile label row (Oct 31)."""

import datetime
import json
import os

import pytest

from backEnd.native.box_overlay import BoxOverlay, parse_drawbox
from backEnd.native.open_capture import decoder_info_path


def _spec(x, y, w, h, uid, color="yellow", pw=320, ph=240):
    return "drawbox=%d:%d:%d:%d:%d:%d:%d:%s:t=0" % (x, y, w, h, pw, ph, uid, color)


def test_parse_drawbox():
    assert parse_drawbox(_spec(1, 2, 3, 4, 9)) == (1, 2, 3, 4, 320, 240, 9, "yellow")
    assert parse_drawbox("nope") is None
    assert parse_drawbox("drawbox=1:2") is None


def test_boxes_follow_playback_time():
    overlay = BoxOverlay([[1000, _spec(10, 10, 20, 20, 1)], [1500, _spec(50, 10, 20, 20, 1)],
                          [1200, _spec(100, 100, 10, 10, 2)]])
    assert [b[0] for b in overlay.boxes_at(1100)] == [10, 100]
    assert [b[0] for b in overlay.boxes_at(1400)] == [50, 100]
    # Past the gap window nothing is drawn.
    assert overlay.boxes_at(5000) == []
    assert not BoxOverlay([])


def test_draw_scales_to_output_and_labels():
    np = pytest.importorskip("numpy")
    overlay = BoxOverlay([[0, _spec(40, 40, 80, 80, 7, "green")]], {7: "person"})
    w, h = 160, 120
    out = overlay.draw(bytes(w * h * 3), w, h, 0)
    arr = np.frombuffer(out, dtype=np.uint8).reshape(h, w, 3)
    # Box edge scaled by 0.5 -> left edge at x=20, rows 20..60.
    assert arr[40, 20, 1] > 150 and arr[40, 20, 0] < 50
    assert arr[40, 40].sum() == 0  # inside the box untouched
    assert arr[8:20, 20:40].any()  # label tag above the box
    assert overlay.draw(b"\x00" * 12, 2, 2, 99999) == b"\x00" * 12


def test_clip_reader_draws_boxes(tmp_path):
    pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    from backEnd.native.capture import ClipReader
    from tests.open_backend_support import write_moving_rect

    path = tmp_path / "clip.mp4"
    write_moving_rect(path, frames=6)
    start = 1_000_000
    boxes = [[start + i * 100, _spec(150, 20, 100, 200, 3, "orange")] for i in range(6)]
    plain, marked = ClipReader(None), ClipReader(None)
    assert plain.open(str(path), 320, 240, start, None)
    assert marked.open(str(path), 320, 240, start, {"boxList": boxes, "boxLabels": {3: "vehicle"}})
    a = np.frombuffer(plain.seek(0).asPil().tobytes(), dtype=np.uint8).reshape(240, 320, 3)
    b = np.frombuffer(marked.seek(0).asPil().tobytes(), dtype=np.uint8).reshape(240, 320, 3)
    assert a[120, 150].sum() < 60
    assert b[120, 150, 0] > 200 and b[120, 150, 2] < 60  # orange edge


def test_decoder_info_path_without_live_view(tmp_path):
    assert decoder_info_path("x/live/Cam.live", "", "") == "x/live/Cam.decoder.json"
    assert decoder_info_path("", str(tmp_path), "Cam A") == os.path.join(str(tmp_path), "live", "Cam A.decoder.json")
    assert decoder_info_path("", "", "Cam") == ""


def test_decoder_info_written_without_mmap(tmp_path):
    pytest.importorskip("av")
    from backEnd.native.open_capture import StreamReader

    reader = StreamReader("Cam B", userDir=str(tmp_path))
    reader._decoder_info = {"decoder": "hardware (d3d11va)"}
    reader._write_decoder_info()
    with open(tmp_path / "live" / "Cam B.decoder.json", encoding="utf-8") as handle:
        assert json.load(handle)["decoder"].startswith("hardware")


def test_fps_warning_only_on_real_problems():
    pytest.importorskip("wx")
    from frontEnd.MonitorView import _FpsStats, tile_name_width, warning_tooltip

    healthy = _FpsStats("Cam")
    for _ in range(80):
        healthy.trackFrameRate(2.0, 5.0)  # normal: 2 fps analytics, 5 fps sub stream
    assert not healthy.checkForErrors()
    assert warning_tooltip(healthy, 2.0, 5.0) == ""

    stalled = _FpsStats("Cam")
    for _ in range(80):
        stalled.trackFrameRate(0.5, 0.5)
    assert stalled.checkForErrors()
    tip = warning_tooltip(stalled, 0.5, 0.5)
    assert "Analytics is falling behind" in tip or "Low frame rate" in tip

    assert tile_name_width(160, 16, 8, 20, 16) == 98
    assert tile_name_width(40, 16, 8, 20, 16) == 20


def test_calendar_marks_recording_days():
    wx = pytest.importorskip("wx")
    from vitaToolbox.wx import VitaDatePickerCtrl as mod

    app = wx.GetApp() or wx.App(False)  # one wx.App per process
    try:
        frame = wx.Frame(None)
        today = datetime.date.today()
        days = {today, today.replace(day=1)}
        popup = mod._CalendarPopup(frame, today, None, None, lambda d: d in days)
        popup._updateMonth()
        marked = {popup._firstShownDate + datetime.timedelta(i)
                  for i, btn in enumerate(popup._dayButtons)
                  if btn.GetToolTipText() == "Recordings stored"}
        assert marked == days
        popup.Destroy()
        frame.Destroy()
    finally:
        assert app
