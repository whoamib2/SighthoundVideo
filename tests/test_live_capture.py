"""Live capture must open a file, publish a frame, and record a clip.

The Windows camera log died in TimingInfo.associateKeys (bytes joined onto
a str) and the CameraCapture handler then passed that text to
traceback.format_exc, which only accepts an integer limit.
"""

from __future__ import annotations

import sys

import pytest


def test_associate_keys_stays_text():
    from appCommon.TimingInfo import TimingInfo

    info = TimingInfo({})
    info.associateKeys({
        "videoPath": "Driveway Center",
        "source": b"CameraCapture",
    })
    assert isinstance(info.sourceName, str)
    assert info.sourceName == "CameraCapture"


def test_capture_run_logs_the_original_exception():
    from backEnd.CameraCapture import CameraCapture

    class _Logger:
        def __init__(self):
            self.errors = []

        def error(self, msg, *args, **kwargs):
            self.errors.append(msg % args if args else str(msg))

    class _Boom:
        def _run(self):
            raise TypeError('can only concatenate str (not "bytes") to str')

    camera = _Boom()
    camera._logger = _Logger()
    CameraCapture.run(camera)
    logged = "\n".join(camera._logger.errors)
    assert 'can only concatenate str (not "bytes") to str' in logged


def _write_video(path, frames=8, fps=10):
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width = 320
    stream.height = 240
    stream.pix_fmt = "yuv420p"
    for index in range(frames):
        array = np.zeros((240, 320, 3), dtype=np.uint8)
        array[:, :, 1] = 30
        left = 8 + index * 24
        array[60:140, left:left + 36, 0] = 220
        video = av.VideoFrame.from_ndarray(array, format="rgb24")
        for packet in stream.encode(video):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


class _Queue:
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


class _Pipe:
    def poll(self, timeout=None):
        return False

    def recv(self):
        raise RuntimeError("no message")


def test_camera_capture_publishes_a_live_frame_and_records_a_clip(tmp_path):
    pytest.importorskip("av")
    pytest.importorskip("numpy")
    from backEnd.native.capture import native_capture_available

    if not native_capture_available():
        pytest.skip("open capture backend is not available")

    from appCommon.LiveViewBuffer import parse_live_footer, parse_live_header
    from appCommon.CommonStrings import kLiveHeaderSize
    from backEnd.CameraCapture import CameraCapture

    video = tmp_path / "driveway.mp4"
    _write_video(video)
    user_dir = tmp_path / "user"
    archive = tmp_path / "archive"
    user_dir.mkdir()
    archive.mkdir()
    location = "Driveway Center"
    stdout, stderr = sys.stdout, sys.stderr
    camera = None
    try:
        camera = CameraCapture(
            _Queue(),
            _Pipe(),
            _Pipe(),
            1,
            location,
            str(video),
            str(tmp_path / "clips.db"),
            str(tmp_path / "tmp"),
            str(archive),
            str(user_dir),
            {"recordSize": (320, 240), "maxClipMs": 200, "sioInProcess": True},
        )
        camera._liveViewEnabled = True
        camera._openStream()
        assert camera._streamReader.isRunning
        camera._openStream = lambda: None

        live = None
        jpeg = None
        for _ in range(16):
            if not camera._streamReader.isRunning:
                break
            if not camera._processFrame():
                continue
            live_path = user_dir / "live" / (location + ".live")
            raw = live_path.read_bytes()
            header = parse_live_header(raw[:kLiveHeaderSize])
            assert header is not None
            frame_id, width, height, _request_fps, _capture_fps = header
            assert frame_id >= 0
            assert width > 0 and height > 0
            footer_at = kLiveHeaderSize + width * height * 3
            footer_w, footer_h = parse_live_footer(raw[footer_at:footer_at + 8])
            assert (footer_w, footer_h) == (width, height)
            live = header

            statuses = {}

            def start_response(status, headers, exc_info=None):
                statuses["status"] = status
                statuses["headers"] = headers

            jpeg = camera._wsgiAppImage({"QUERY_STRING": "width=160&height=120"}, start_response)
            assert statuses["status"].startswith("200")
            assert isinstance(jpeg, bytes) and jpeg.startswith(b"\xff\xd8")
            break

        while camera._streamReader.isRunning:
            camera._processFrame()
    finally:
        sys.stdout, sys.stderr = stdout, stderr
        if camera is not None:
            try:
                camera._cleanup()
            except Exception:
                pass

    assert live is not None, "no live frame was published"
    assert jpeg is not None
    clips = list(archive.rglob("*.mp4"))
    assert clips, "no clip file was recorded"
    rows = camera._clipMgr._cur.execute("SELECT filename FROM clips").fetchall()
    assert rows, "clip was not registered for the frontend"
