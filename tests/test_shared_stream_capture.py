"""Shared-session capture: sub-stream analytics, passthrough recording,
idle analytics rate, network options and reconnect backoff."""
import os
import time

import av
import numpy as np
import pytest

from backEnd.native import open_capture as oc
from backEnd.native.open_capture import ClipReader, getDuration, getMsList


def _make(path, w, h, n=90, gop=15):
    c = av.open(path, "w")
    s = c.add_stream("libx264", rate=15)
    s.width, s.height, s.pix_fmt = w, h, "yuv420p"
    s.options = {"g": str(gop), "keyint_min": str(gop), "bf": "0", "preset": "ultrafast"}
    for i in range(n):
        f = av.VideoFrame.from_ndarray(np.full((h, w, 3), (i * 3) % 255, np.uint8), format="rgb24")
        f.pts = i
        for p in s.encode(f):
            c.mux(p)
    for p in s.encode():
        c.mux(p)
    c.close()


class _ClipMgr:
    def __init__(self):
        self.clips = []

    def addClip(self, *args):
        self.clips.append(args)


def _run(tmp_path, uri, extras):
    mgr = _ClipMgr()
    reader = oc.StreamReader("Cam", mgr, None, str(tmp_path), str(tmp_path / "arch"), record=True)
    assert reader.open(uri, extras)
    frames = []
    while True:
        frame = reader.getNewFrame(True)
        if frame is None:
            break
        frames.append(frame)
    time.sleep(0.3)
    info = reader.getDecoderInfo()
    proc = reader.getProcSize()
    reader.close()
    return reader, mgr, frames, info, proc


@pytest.fixture()
def videos(tmp_path):
    main, sub = str(tmp_path / "main.mp4"), str(tmp_path / "sub.mp4")
    _make(main, 1280, 720)
    _make(sub, 320, 180)
    return main, sub


def _source_packets(path):
    with av.open(path) as c:
        return [bytes(p) for p in c.demux(c.streams.video[0]) if p.size]


def test_passthrough_segments_are_keyframe_aligned_bit_exact_and_play(tmp_path, videos):
    main, _ = videos
    _, mgr, frames, info, proc = _run(tmp_path, main, {"maxClipMs": 2000, "useSubStream": False})
    assert info["recording"] == "passthrough" and info["analytics_stream"] == "main"
    assert len(mgr.clips) == 3
    recorded = []
    prev = ""
    for clip in mgr.clips:
        rel, cam, first, last, prev_file = clip[:5]
        assert cam == "Cam" and last > first and prev_file == prev
        prev = rel
        path = str(tmp_path / "arch" / rel)
        with av.open(path) as c:
            packets = [p for p in c.demux(c.streams.video[0]) if p.size]
            assert packets[0].is_keyframe
            assert c.streams.video[0].codec_context.name == "h264"
            recorded += [bytes(p) for p in packets]
        # The app's player backend opens, seeks and steps through it.
        reader = ClipReader()
        assert reader.open(path)
        assert reader.getInputSize() == (1280, 720)
        assert reader.seek(0) is not None and reader.getNextFrame() is not None
        reader.close()
        assert len(getMsList(path)) == 30 and getDuration(path) > 1500
    # No re-encode: identical compressed packets, none lost.
    assert recorded == _source_packets(main)
    assert all(f.filename for f in frames if not f.dummy)


def test_sub_stream_feeds_analytics_main_is_recorded(tmp_path, videos):
    main, sub = videos
    _, mgr, frames, info, proc = _run(tmp_path, main, {"subStreamUri": sub, "maxClipMs": 60000})
    assert info["analytics_stream"] == "sub" and info["main_resolution"] == "1280x720"
    real = [f for f in frames if not f.dummy]
    assert real and (real[0].width, real[0].height) == (320, 180)
    assert proc == (320, 180)
    assert mgr.clips and mgr.clips[0][7:9] == (320, 180)  # proc size for boxes
    with av.open(str(tmp_path / "arch" / mgr.clips[0][0])) as c:
        assert (c.streams.video[0].width, c.streams.video[0].height) == (1280, 720)


def test_unusable_sub_stream_falls_back_to_main(tmp_path, videos):
    main, _ = videos
    square = str(tmp_path / "square.mp4")
    _make(square, 240, 240, n=15)
    _, _, _, info, _ = _run(tmp_path, main, {"subStreamUri": square})
    assert info["analytics_stream"] == "main"
    _, _, _, info, _ = _run(tmp_path, main, {"subStreamUri": str(tmp_path / "missing.mp4")})
    assert info["analytics_stream"] == "main"


def test_idle_analytics_rate_drops_and_activity_restores(tmp_path, videos):
    main, _ = videos
    mgr = _ClipMgr()
    reader = oc.StreamReader("Cam", mgr, None, str(tmp_path), str(tmp_path / "arch"), record=False)
    assert reader.open(main, {"useSubStream": False})
    reader._last_activity_t = 0  # idle
    idle = [reader.getNewFrame() for _ in range(30)]
    assert sum(not f.dummy for f in idle) <= 2 and all(f.dummy and f.rgb == b"" for f in idle[1:])
    reader.noteActivity()
    busy = [reader.getNewFrame() for _ in range(10)]
    assert all(not f.dummy for f in busy)
    reader.close()


def test_main_live_only_for_enlarged_view(tmp_path, videos):
    main, sub = videos
    reader = oc.StreamReader("Cam", None, None, str(tmp_path), str(tmp_path / "a"), record=False)
    assert reader.open(main, {"subStreamUri": sub})
    reader.getNewFrame()
    assert reader.open_mmap(str(tmp_path / "live" / "Cam.live"))
    reader.setMmapParams(True, 320, 240, 15)
    assert not reader._main_live_wanted()
    reader.setMmapParams(True, 1280, 720, 15)
    assert reader._main_live_wanted()
    reader.close()


@pytest.mark.parametrize("uri,expected", [
    ("rtsp://h/h264Preview_01_main", "rtsp://h/h264Preview_01_sub"),
    ("rtsp://h:554/Preview_01_main", "rtsp://h:554/Preview_01_sub"),
    ("rtsp://h/cam/realmonitor?channel=1&subtype=0", "rtsp://h/cam/realmonitor?channel=1&subtype=1"),
    ("rtsp://h/Streaming/Channels/101", "rtsp://h/Streaming/Channels/102"),
    ("rtsp://h/cam/realmonitor?channel=1&subtype=1", None),
    ("rtsp://h/live", None),
    ("C:/clips/a.mp4", None),
])
def test_sub_stream_templates(uri, expected):
    assert oc.sub_stream_uri(uri, {}) == expected


def test_sub_stream_overrides():
    assert oc.sub_stream_uri("rtsp://h/a", {"subStreamUri": "rtsp://h/b"}) == "rtsp://h/b"
    assert oc.sub_stream_uri("rtsp://h/h264Preview_01_main", {"useSubStream": False}) is None
    # Blue Iris: only when a sub stream was configured.
    bi = {"blueIrisServer": "bi", "blueIrisShortName": "Cam8"}
    assert oc.sub_stream_uri("rtsp://bi:81/Cam8?subtype=0", bi) is None
    assert oc.sub_stream_uri("rtsp://bi:81/Cam8", dict(bi, blueIrisSubStream="rtsp://bi:81/Cam8sub")) == "rtsp://bi:81/Cam8sub"


def test_pick_onvif_sub_uri():
    choices = [("MainStream: H264, 2560x1440 (rtsp://x/m)", "rtsp://x/m"),
               ("SubStream: H264, 640x480 (rtsp://x/s)", "rtsp://x/s"),
               ("Third: H264, 1280x720 (rtsp://x/t)", "rtsp://x/t"),
               ("Custom", "Custom")]
    assert oc.pick_onvif_sub_uri(choices, "rtsp://x/m") == "rtsp://x/s"
    assert oc.pick_onvif_sub_uri(choices[:1], "rtsp://x/m") is None


def test_sub_stream_usable():
    assert oc.sub_stream_usable((2560, 1440), (640, 360))
    assert oc.sub_stream_usable((2560, 1440), (640, 480))      # Amcrest 4:3 sub
    assert not oc.sub_stream_usable((1280, 720), (1280, 720))
    assert not oc.sub_stream_usable((2560, 1440), (240, 400))


def test_network_options_use_tcp_and_supported_socket_timeout():
    opts = oc.network_options("rtsp://cam/stream")
    assert opts["rtsp_transport"] == "tcp" and opts["timeout"] == "10000000"
    assert "stimeout" not in opts
    assert oc.network_options("C:/a.mp4") == {}


def test_reconnect_backoff_quick_first_then_exponential():
    from backEnd import CameraCapture

    times = CameraCapture._kRetryTimeList
    assert times[0] == 0 and times[1] <= 2
    assert all(b >= a for a, b in zip(times, times[1:])) and times[-1] == 60


def test_pts_clock_monotonic_and_reanchors():
    clock = oc._PtsClock()
    a = clock.ms(100.0)
    b = clock.ms(100.5)
    assert 490 <= b - a <= 510
    c = clock.ms(5000.0)  # huge jump: re-anchored near wall clock
    assert c > b and abs(c - time.time() * 1000) < 1000
    assert clock.ms(None) > c
