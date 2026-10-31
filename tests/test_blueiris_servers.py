"""Saved Blue Iris servers: DPAPI password, most-recent ordering, already-added, updates."""
import json
import sys

import pytest

from appCommon import BlueIrisServers as bs


@pytest.fixture
def fake_dpapi(monkeypatch):
    monkeypatch.setattr(bs, "_dpapi", lambda data, protect: bytes(b ^ 0x5A for b in data))


def test_password_never_plain_and_round_trips(tmp_path, fake_dpapi):
    path = str(tmp_path / "s.json")
    sid, changed = bs.save_server("192.168.0.10", 81, "ben", "s3cret!", "rtsp", path)
    assert (sid, changed) == ("192.168.0.10:81", False)
    raw = open(path, encoding="utf-8").read()
    assert "s3cret!" not in raw
    assert bs.get_password(sid, path) == "s3cret!"
    assert bs.save_server("192.168.0.10", 81, "ben", "new", "rtsp", path)[1] is True


def test_most_recent_first_and_forget(tmp_path, fake_dpapi, monkeypatch):
    path = str(tmp_path / "s.json")
    clock = iter([1.0, 2.0])
    monkeypatch.setattr(bs.time, "time", lambda: next(clock))
    bs.save_server("a", 81, "u", "p", "rtsp", path)
    bs.save_server("b", 82, "u", "p", "mjpeg", path)
    assert [s["id"] for s in bs.load_servers(path)] == ["b:82", "a:81"]
    bs.forget_server("b:82", path)
    assert [s["id"] for s in json.load(open(path))["servers"]] == ["a:81"]


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows-only")
def test_real_dpapi_round_trip():
    token = bs.protect("pässword")
    assert "pässword" not in token and bs.unprotect(token) == "pässword"


def test_added_short_names():
    cams = [("rtsp://u:p@bi:81/front", {}), ("http://u:p@bi:81/mjpg/back/video.mjpg", None),
            ("rtsp://x/other", {"blueIrisServer": "bi:81", "blueIrisShortName": "side"}),
            ("rtsp://u:p@elsewhere:81/drive", {})]
    assert bs.added_short_names(cams, "BI", 81) == {"front", "back", "side"}


class _Client:
    def __init__(self):
        self.cams = {"Front": ("Other", "rtsp://ben:old@bi:81/front", True, bs.camera_extras("bi:81", "front", "rtsp")),
                     "Mine": ("Other", "rtsp://x/y", True, {})}
        self.edits = []

    def getCameraLocations(self):
        return list(self.cams)

    def getCameraSettings(self, loc):
        return self.cams[loc]

    def editCamera(self, orig, loc, camType, uri, when, extra):
        self.edits.append((loc, uri, extra))


def test_password_change_updates_referencing_cameras(tmp_path, fake_dpapi):
    path = str(tmp_path / "s.json")
    bs.save_server("bi", 81, "ben", "new", "rtsp", path)
    client = _Client()
    assert bs.update_cameras_for_server(client, "bi:81", path) == 1
    loc, uri, extra = client.edits[0]
    assert loc == "Front" and uri == "rtsp://ben:new@bi:81/front" and extra["blueIrisServer"] == "bi:81"
