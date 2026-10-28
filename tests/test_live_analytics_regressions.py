"""Regressions found on a live 4096x1536 RTSP camera."""

import logging
from collections import defaultdict

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("PIL")

from backEnd.QueuedDataManagerCloud import QueuedDataManagerCloud, _QueuedObjId  # noqa: E402
from backEnd.native import onnx_detector  # noqa: E402
from backEnd.native.open_capture import VideoFrame, analytics_frame  # noqa: E402


def test_object_ids_survive_a_slow_tracker():
    """Purge timing is in ms. An id must outlive a 1 s gap between frames."""
    start = 1_793_200_000_000
    obj = _QueuedObjId(0, 0, start, "unknown")
    _QueuedObjId._idLastPurgeTime = 0
    _QueuedObjId.purgeOldIds(start + 1_000)
    assert _QueuedObjId.getQueuedObjId(obj.sentryId) is obj
    _QueuedObjId.purgeOldIds(start + 6 * 60 * 1000)
    with pytest.raises(KeyError):
        _QueuedObjId.getQueuedObjId(obj.sentryId)


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, msg):
        self.lines.append(msg)

    def warning(self, msg):
        self.lines.append(msg)

    def debug(self, msg):
        pass


def test_unknown_object_id_does_not_raise():
    qdm = QueuedDataManagerCloud.__new__(QueuedDataManagerCloud)
    qdm._logger = _Log()
    qdm._trackedObjects = {}
    qdm._addFrameToDb(-12345, 1, 1000, (0, 0, 1, 1), "unknown")
    assert qdm._trackedObjects == {}
    assert "unknown object id" in qdm._logger.lines[0]


def test_minute_stats_line():
    qdm = QueuedDataManagerCloud.__new__(QueuedDataManagerCloud)
    qdm._logger = _Log()
    qdm._minuteStats = defaultdict(int)
    qdm._minuteStatsStart = 100.0
    qdm._minuteStats["trackerFrames"] = 300
    qdm._minuteStats["detectorFrames"] = 90
    qdm._minuteStats["det:person"] = 4
    qdm._minuteStats["det:vehicle"] = 2
    assert qdm._logMinuteStats(now=130.0) is None
    line = qdm._logMinuteStats(now=161.0)
    assert "trackerFrames=300 detectorFrames=90 detections=6 person=4 vehicle=2" in line
    assert qdm._minuteStats["trackerFrames"] == 0


def test_wide_camera_frame_is_downscaled_for_tracking():
    rgb = bytes(4096 * 1536 * 3)
    full = VideoFrame(rgb, 4096, 1536, 5)
    small = analytics_frame(full)
    assert (small.width, small.height) == (1280, 480)
    assert small.wasResized
    assert small.getLargeFrame() is full
    assert small.ms == 5
    narrow = VideoFrame(bytes(640 * 360 * 3), 640, 360, 6)
    assert analytics_frame(narrow) is narrow
    assert not narrow.wasResized


def test_yolox_gets_bgr_input():
    assert onnx_detector._wants_bgr({"layout": "yolox"})
    assert not onnx_detector._wants_bgr({"layout": "yolov8"})
    assert not onnx_detector._wants_bgr({"layout": "yolox", "channel_order": "rgb"})
    rgb = bytes([255, 0, 0]) * 4
    blob, _, _, _ = onnx_detector._letterbox(rgb, 2, 2, 2, 114, False, "nearest", False, bgr=True)
    assert list(blob[0, 0, 0]) == [0.0, 0.0, 255.0]


def test_disk_cleaner_log_paths_are_text():
    from vitaToolbox.strUtils.EnsureUnicode import ensureUnicode

    assert "Found an orphaned dir " + ensureUnicode("C:/x") == "Found an orphaned dir C:/x"
    logging.getLogger(__name__).debug("ok")

class _Reader:
    _extras: dict = {}
    _tmp_path = ""
    _camera_location = "Cam"
    _clip_mgr = None

    def __init__(self, root):
        self._archive_path = str(root)


def test_clip_writer_survives_jittery_timestamps_and_reader_plays_it(tmp_path):
    pytest.importorskip("av")
    import random

    from backEnd.native.open_capture import ClipReader, _ClipWriter, getMsList

    rng = random.Random(4)
    writer = _ClipWriter(_Reader(tmp_path))
    ms = 1_000
    for _ in range(120):
        # Several frames inside one 1/15 s tick used to make the muxer fail.
        ms += rng.choice([3, 20, 66, 66, 140, 400])
        frame = np.full((96, 160, 3), rng.randrange(256), dtype=np.uint8)
        writer.write(frame.tobytes(), 160, 96, ms)
    path = writer._full_path
    writer.close_segment()
    assert len(list((tmp_path / "Cam").glob("*.mp4"))) == 1
    stamps = getMsList(path)
    assert len(stamps) == 120
    assert stamps == sorted(stamps)

    reader = ClipReader(None)
    assert reader.open(path, 80, 48)
    first = reader.seek(0)
    assert (first.width, first.height) == (80, 48)
    middle = reader.seek(stamps[60])
    assert middle.ms == stamps[60]
    assert reader.getPrevFrame().ms == stamps[59]
    count = 0
    while reader.getNextFrame() is not None:
        count += 1
    assert count == 120 - 60
    reader.close()

def test_flush_registers_recording_and_timeline_stays_continuous(tmp_path):
    pytest.importorskip("av")
    import logging

    from backEnd.ClipManager import ClipManager
    from backEnd.native.open_capture import _ClipWriter

    clip_mgr = ClipManager(logging.getLogger("test"))
    clip_mgr.open(str(tmp_path / "clipdb"))
    reader = _Reader(tmp_path)
    reader._clip_mgr = clip_mgr
    reader._clip_mgr_lock = None
    reader._move_failed = None
    writer = _ClipWriter(reader)
    frame = np.zeros((96, 160, 3), dtype=np.uint8).tobytes()
    ms = 10_000
    for flush_round in range(3):
        for _ in range(45):  # 3 s at 15 fps
            ms += 66
            writer.write(frame, 160, 96, ms)
        assert writer.flush_if_long_enough() == 1
    # Too short to be worth its own file: kept open.
    writer.write(frame, 160, 96, ms + 66)
    assert writer.flush_if_long_enough() == 0
    ranges = clip_mgr.getTimesFromLocation("Cam", 0, ms + 10_000)
    assert len(ranges) == 1, ranges
    assert ranges[0][1] - ranges[0][0] >= 8_000
