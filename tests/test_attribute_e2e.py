"""A clip's carrier attribute is stored and fires a delivery rule."""

import pytest

from backEnd.DataManager import DataManager
from backEnd.QueuedDataManagerCloud import QueuedDataManagerCloud
from backEnd.VideoPipeline import VideoPipeline
from backEnd.attribute_text import choose_notification
from backEnd.native.capture import StreamReader
from backEnd.native.test_attribute_models import build_carrier_logits_model
from backEnd.native.test_detector_model import build_bright_blob_model
from backEnd.triggers.AttributeTriggers import DeliveryTrigger
from tests.open_backend_support import Log, write_moving_rect
from tests.test_pipeline_e2e import _Pipe, _Queue, _apply

pytest.importorskip("av")
pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")

_ATTRIBUTE_ENV = (
    "SIGHTHOUND_PLATE_DETECTOR_MODEL",
    "SIGHTHOUND_PLATE_OCR_MODEL",
    "SIGHTHOUND_FACE_DETECTOR_MODEL",
    "SIGHTHOUND_FACE_EMBED_MODEL",
    "SIGHTHOUND_CARRIER_HEURISTIC",
    "SIGHTHOUND_OBJECT_DB",
)


def test_carrier_reaches_the_database_and_fires_a_rule(tmp_path, monkeypatch):
    for name in _ATTRIBUTE_ENV:
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("SIGHTHOUND_CARRIER_HEURISTIC", "0")
    detector = tmp_path / "vehicle.onnx"
    carrier = tmp_path / "carrier.onnx"
    build_bright_blob_model(str(detector), class_id=1.0)
    build_carrier_logits_model(str(carrier))
    monkeypatch.setenv("SIGHTHOUND_DETECTOR_MODEL", str(detector))
    monkeypatch.setenv("SIGHTHOUND_CARRIER_MODEL", str(carrier))

    video = tmp_path / "truck.mp4"
    write_moving_rect(video, frames=16, fps=10)
    logger = Log()
    queue = _Queue()
    reader = StreamReader("curb", None, None, str(tmp_path), str(tmp_path), str(tmp_path), None, False)
    assert reader.open(str(video), {})
    data = QueuedDataManagerCloud(queue, _Pipe(), 1, "curb", str(tmp_path), 0, logger, True)
    pipeline = VideoPipeline("curb", data)
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

    manager = DataManager(logger)
    manager.open(str(tmp_path / "objects.db"))
    try:
        _apply(manager, queue.messages)
        manager.save()
        rows = manager._cur.execute(
            "SELECT objUid, value FROM objAttributes WHERE key='delivery_carrier'"
        ).fetchall()
        assert rows, "no carrier reached the database: %s" % logger.messages[-12:]
        assert any(row[1] == "UPS" for row in rows), rows
        hits = DeliveryTrigger(manager, ["UPS"], cooldown_ms=60000).search()
        assert hits, rows
        sentence = manager.notificationSentenceNear("curb", hits[0][2])
        assert choose_notification(sentence, "UPS truck at the curb") == "UPS truck at the curb"
    finally:
        manager.close()
