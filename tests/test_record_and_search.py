"""A short clip is detected, saved, and returned by a search.

Windows was recording tiny files and showing no search hits: frame times came
from the camera clock, and the analytics process exited when the ONNX file
could not be opened. This runs capture, the ONNX detector, and the data
manager together.
"""

from __future__ import annotations

import os
import threading

import pytest

from backEnd import MessageIds
from backEnd.ClipManager import ClipManager
from backEnd.DataManager import DataManager
from backEnd.QueuedDataManagerCloud import QueuedDataManagerCloud
from backEnd.VideoPipeline import VideoPipeline
from backEnd.native.capture import StreamReader
from backEnd.triggers.TargetTrigger import TargetTrigger
from tests.open_backend_support import Log, write_moving_rect

pytest.importorskip("av")
pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")


class _Queue:
    def __init__(self) -> None:
        self.messages = []

    def put(self, message) -> None:
        self.messages.append(message)


class _Pipe:
    def poll(self, timeout=0):
        return False

    def recv(self):
        raise RuntimeError("no database id on the pipe")


def _apply(data_mgr: DataManager, messages: list) -> None:
    temp_ids = {}
    for message in messages:
        kind = message[0]
        if kind == MessageIds.msgIdDataAddObject:
            tail = message[1:]
            _pipe_id, cam_obj_id, add_time, obj_type, location = tail[:5]
            attributes = tail[5] if len(tail) > 5 and isinstance(tail[5], dict) else None
            db_id = data_mgr.addObject(add_time, obj_type, location)
            if attributes:
                data_mgr.setObjectAttributes(db_id, attributes, add_time)
            temp_ids[cam_obj_id] = db_id
        elif kind == MessageIds.msgIdDataAddFrame:
            _pipe_id, db_id, frame, frame_time, bbox, obj_type, action = message[1:]
            if isinstance(db_id, tuple):
                db_id = temp_ids[db_id[1]]
            data_mgr.addFrame(db_id, frame, frame_time, bbox, obj_type, action)


def test_detected_clip_is_playable_and_searchable(tmp_path, monkeypatch):
    from backEnd.native.test_detector_model import build_bright_blob_model

    bundled = os.path.join(os.path.dirname(__file__), "..", "models", "yolox_nano.onnx")
    if os.path.isfile(bundled):
        model = bundled
    else:
        model = str(tmp_path / "blob.onnx")
        build_bright_blob_model(model)
    monkeypatch.setenv("SIGHTHOUND_DETECTOR_MODEL", model)

    video = tmp_path / "driveway.mp4"
    write_moving_rect(video, frames=16, fps=10)
    archive = tmp_path / "video"
    archive.mkdir()
    logger = Log()
    queue = _Queue()
    clips = ClipManager(logger, 0)
    clips.open(str(tmp_path / "clips.db"))
    reader = StreamReader(
        "Driveway Center", clips, threading.Lock(),
        str(tmp_path), str(archive), str(tmp_path), None, True,
    )
    assert reader.open(str(video), {})
    data = QueuedDataManagerCloud(
        queue, _Pipe(), 1, "Driveway Center", str(archive), 0, logger, True,
    )
    pipeline = VideoPipeline("Driveway Center", data)
    try:
        while True:
            frame = reader.getNewFrame(False)
            if frame is None:
                break
            if frame.dummy:
                data.reportFrame(frame.ms)
            else:
                data.reportFrame(frame.ms, frame)
                pipeline.processClipFrame(frame, frame.ms)
        pipeline.flush()
        data.flush(10)
    finally:
        reader.close()
        data.terminate()

    files = list(archive.rglob("*.mp4"))
    assert files, "no clip file was written"
    import av
    container = av.open(str(files[0]))
    decoded = next(container.decode(video=0))
    assert decoded.width > 0 and decoded.height > 0
    container.close()

    manager = DataManager(logger)
    manager.open(str(tmp_path / "objects.db"))
    try:
        _apply(manager, queue.messages)
        manager.save()
        rows = manager._cur.execute("SELECT uid, type FROM objects").fetchall()
        assert rows, "detector produced no objects: %s" % logger.messages[-8:]
        trigger = TargetTrigger(
            manager, [("person", "any"), ("vehicle", "any"), ("animal", "any")],
        )
        hits = trigger.search()
        assert hits, rows
    finally:
        manager.close()
