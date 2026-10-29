"""optsearches pure-Python fallback and HW/SW decoder badge helpers."""

import json

from backEnd.triggers import TriggerUtils, optsearches_py
from backEnd.triggers.TriggerUtils import BBOX, kDirectionStrToInt, kTrackPointStrToInt
from frontEnd import DecoderBadge


def _square(x1, y1, x2, y2):
    pts = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    segs = (BBOX * 4)()
    for i, (a, b) in enumerate(zip(pts, pts[1:] + pts[:1])):
        segs[i] = BBOX(a[0], a[1], b[0], b[1])
    return segs


def test_point_in_polygon():
    segs = _square(100, 100, 200, 200)
    center = kTrackPointStrToInt["center"]
    assert optsearches_py.is_obj_inside(BBOX(140, 140, 160, 160), center, segs, 4) == 1
    assert optsearches_py.is_obj_inside(BBOX(10, 10, 30, 30), center, segs, 4) == 0
    # bottom track point of a box straddling the top edge is inside
    assert optsearches_py.is_obj_inside(BBOX(140, 60, 160, 150), kTrackPointStrToInt["bottom"], segs, 4) == 1
    assert optsearches_py.is_obj_inside(BBOX(140, 60, 160, 150), kTrackPointStrToInt["top"], segs, 4) == 0


def test_line_crossing_any_and_directions():
    line = BBOX(100, 0, 100, 300)  # vertical line
    c = kTrackPointStrToInt["center"]
    left_box, right_box = BBOX(40, 100, 60, 120), BBOX(140, 100, 160, 120)
    anyd = kDirectionStrToInt["any"]
    assert optsearches_py.did_obj_cross(left_box, right_box, line, c, anyd) == 1
    assert optsearches_py.did_obj_cross(right_box, left_box, line, c, anyd) == 1
    assert optsearches_py.did_obj_cross(left_box, BBOX(60, 100, 80, 120), line, c, anyd) == 0
    l2r = [optsearches_py.did_obj_cross(left_box, right_box, line, c, kDirectionStrToInt[d]) for d in ("left", "right")]
    r2l = [optsearches_py.did_obj_cross(right_box, left_box, line, c, kDirectionStrToInt[d]) for d in ("left", "right")]
    assert sorted(l2r) == [0, 1] and sorted(r2l) == [0, 1] and l2r != r2l


def test_triggerutils_falls_back_when_native_missing(monkeypatch):
    def boom(*a, **k):
        raise OSError("optsearches not found")
    monkeypatch.setattr(TriggerUtils, "LoadLibrary", boom)
    monkeypatch.setattr(TriggerUtils, "_searchlib", None)
    assert TriggerUtils._lib() is optsearches_py
    assert TriggerUtils.optimizedIsPointInside(BBOX(140, 140, 160, 160), 0, _square(100, 100, 200, 200), 4) is True


def test_decoder_badge(tmp_path):
    assert DecoderBadge.badge_for({})[0] == ""
    assert DecoderBadge.badge_for({"decoder": "Hardware (D3D11VA)"}) == ("HW", "Decoder: Hardware (D3D11VA)")
    assert DecoderBadge.badge_for({"decoder": "Software (h264)"})[0] == "SW"
    (tmp_path / "live").mkdir()
    info = {"decoder": "Hardware (D3D11VA)", "codec": "h264", "width": 640, "height": 360,
            "fps": 15.0, "analytics_stream": "sub", "recording": "passthrough"}
    (tmp_path / "live" / "Cam.decoder.json").write_text(json.dumps(info))
    got = DecoderBadge.read_decoder_info(str(tmp_path), "Cam")
    assert got == info
    line = DecoderBadge.status_line("Cam", got)
    assert "HW Hardware (D3D11VA)" in line and "640x360" in line and "analytics: sub" in line
    assert DecoderBadge.read_decoder_info(str(tmp_path), "Missing") == {}
