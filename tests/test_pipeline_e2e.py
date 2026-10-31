"""A video file runs through capture, tracking, detection, and rule search."""

import pytest

from backEnd import MessageIds
from backEnd.DataManager import DataManager
from backEnd.QueuedDataManagerCloud import QueuedDataManagerCloud
from backEnd.VideoPipeline import VideoPipeline
from backEnd.native.capture import StreamReader
from backEnd.native.test_detector_model import build_bright_blob_model
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


def test_sample_video_is_tracked_classified_and_searchable(tmp_path, monkeypatch):
    model = tmp_path / "blob.onnx"
    build_bright_blob_model(str(model))
    monkeypatch.setenv("SIGHTHOUND_DETECTOR_MODEL", str(model))
    video = tmp_path / "walk.mp4"
    write_moving_rect(video, frames=40, fps=10)

    logger = Log()
    queue = _Queue()
    reader = StreamReader("porch", None, None, str(tmp_path), str(tmp_path), str(tmp_path), None, False)
    assert reader.open(str(video), {})
    data = QueuedDataManagerCloud(queue, _Pipe(), 1, "porch", str(tmp_path), 0, logger, True)
    pipeline = VideoPipeline("porch", data)
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

    db_path = tmp_path / "objects.db"
    manager = DataManager(logger)
    manager.open(str(db_path))
    try:
        _apply(manager, queue.messages)
        manager.save()
        rows = manager._cur.execute("SELECT uid, type FROM objects").fetchall()
        assert rows, "pipeline produced no database objects: %s" % logger.messages[-12:]
        assert any(row[1] == "person" for row in rows), rows
        trigger = TargetTrigger(manager, [("person", "any"), ("vehicle", "any"), ("animal", "any")])
        hits = trigger.search()
        assert hits, rows
        assert all(isinstance(hit[0], int) for hit in hits)
    finally:
        manager.close()
