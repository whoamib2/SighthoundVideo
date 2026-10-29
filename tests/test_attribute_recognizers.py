"""Plate, face, and carrier recognizers on tiny ONNX graphs."""

import os
import sys

import numpy as np
import pytest

from backEnd.attribute_text import merge_track_attributes, notification_sentence, choose_notification
from backEnd.native.attribute_recognizers import AttributePipeline, OnnxGraph
from backEnd.native.carrier_features import heuristic_carrier
from backEnd.native.detector import BoundingBox, Detection
from backEnd.native.test_attribute_models import (
    build_carrier_logits_model,
    build_constant_box_model,
    build_strip_embed_model,
    build_strip_ocr_model,
)

pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")


def _paint_plate(text: str = "ABC12345") -> np.ndarray:
    image = np.zeros((16, 64, 3), dtype=np.uint8)
    for index, char in enumerate(text):
        image[:, index * 8 : (index + 1) * 8] = ord(char)
    return image


def _person(image: np.ndarray) -> Detection:
    height, width = image.shape[:2]
    return Detection(
        type="person",
        bounding_box=BoundingBox(0, 0, width, height),
        score=0.9,
    )


def _vehicle(image: np.ndarray) -> Detection:
    height, width = image.shape[:2]
    return Detection(
        type="vehicle",
        bounding_box=BoundingBox(0, 0, width, height),
        score=0.9,
    )


def test_plate_ocr_reads_a_synthetic_strip(tmp_path):
    detector = OnnxGraph(build_constant_box_model(str(tmp_path / "plate.onnx"), "plate"))
    ocr = OnnxGraph(build_strip_ocr_model(str(tmp_path / "ocr.onnx")))
    pipeline = AttributePipeline(plate_detector=detector, plate_ocr=ocr, use_heuristic=False)
    image = _paint_plate("ABC12345")
    found = _vehicle(image)
    pipeline.annotate(image.tobytes(), image.shape[1], image.shape[0], [found])
    assert found.attributes["plate_text"] == "ABC12345"
    assert found.attributes["plate_confidence"] > 0.5


def test_face_gallery_matches_the_same_pattern_and_rejects_another(tmp_path):
    detector = OnnxGraph(build_constant_box_model(str(tmp_path / "face.onnx"), "face"))
    embed = OnnxGraph(build_strip_embed_model(str(tmp_path / "embed.onnx")))
    enrolled = np.full((32, 32, 3), 40, dtype=np.uint8)
    enrolled[:, :16] = 210
    other = np.full((32, 32, 3), 210, dtype=np.uint8)
    other[:, :16] = 40
    builder = AttributePipeline(face_detector=detector, face_embed=embed, use_heuristic=False)
    vector = builder.embed_image(enrolled)
    assert vector is not None and vector.shape == (8,)
    pipeline = AttributePipeline(
        face_detector=detector,
        face_embed=embed,
        gallery=[("Ada", vector)],
        threshold=0.85,
        use_heuristic=False,
    )
    known = _person(enrolled)
    pipeline.annotate(enrolled.tobytes(), 32, 32, [known])
    assert known.attributes["face_identity"] == "Ada"
    assert known.attributes["face_confidence"] >= 0.85

    stranger = _person(other)
    pipeline.annotate(other.tobytes(), 32, 32, [stranger])
    assert "face_identity" not in stranger.attributes


def test_carrier_logits_and_color_heuristic(tmp_path):
    graph = OnnxGraph(build_carrier_logits_model(str(tmp_path / "carrier.onnx")))
    pipeline = AttributePipeline(carrier=graph, use_heuristic=False)
    bright = np.full((32, 32, 3), 255, dtype=np.uint8)
    dark = np.zeros((32, 32, 3), dtype=np.uint8)
    ups = _vehicle(bright)
    none = _vehicle(dark)
    pipeline.annotate(bright.tobytes(), 32, 32, [ups])
    pipeline.annotate(dark.tobytes(), 32, 32, [none])
    assert ups.attributes["delivery_carrier"] == "UPS"
    assert "delivery_carrier" not in none.attributes

    blue = np.zeros((24, 24, 3), dtype=np.uint8)
    blue[:, :] = (20, 40, 200)
    assert heuristic_carrier(blue)[0] == "USPS"
    gray = np.full((24, 24, 3), 128, dtype=np.uint8)
    assert heuristic_carrier(gray) is None


def test_best_plate_and_notification_sentence():
    first = merge_track_attributes({}, {"plate_text": "AAA111", "plate_confidence": 0.4})
    second = merge_track_attributes(
        first, {"plate_text": "BBB222", "plate_confidence": 0.2, "delivery_carrier": "UPS", "delivery_confidence": 0.8}
    )
    assert second["plate_text"] == "AAA111"
    better = merge_track_attributes(second, {"plate_text": "BBB222", "plate_confidence": 0.95})
    assert better["plate_text"] == "BBB222"
    assert better["delivery_carrier"] == "UPS"
    assert notification_sentence({"delivery_carrier": "UPS"}, "the curb") == "UPS truck at the curb"
    assert choose_notification("UPS truck", "UPS truck at the curb") == "UPS truck at the curb"
    assert notification_sentence({"plate_text": "ABC123"}) == "plate ABC123"
    assert notification_sentence({"face_identity": "Ada"}) == "Ada"
    assert notification_sentence({}) == ""


def test_frozen_app_reads_models_beside_the_executable(tmp_path, monkeypatch):
    models = tmp_path / "models"
    models.mkdir()
    weight = models / "yolox_nano.onnx"
    weight.write_bytes(b"x")
    exe = tmp_path / "VigilCam.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.delenv("SIGHTHOUND_DETECTOR_MODEL", raising=False)
    from backEnd.native.attribute_recognizers import _models_dir
    from backEnd.native.onnx_detector import resolve_model_path

    assert os.path.normcase(_models_dir()) == os.path.normcase(str(models))
    assert os.path.normcase(resolve_model_path()) == os.path.normcase(str(weight))


def test_garbage_truck_heuristic_and_text():
    import numpy as np

    from backEnd.attribute_text import notification_sentence
    from backEnd.native.detector import DELIVERY_CARRIERS, carrier_display_name

    green_box = np.zeros((40, 48, 3), dtype=np.uint8)
    green_box[:, :] = (30, 120, 50)
    label, confidence = heuristic_carrier(green_box)
    assert label == "Garbage" and 0.2 <= confidence <= 0.99
    # A long, low green crop (car-shaped) is not called a garbage truck.
    low_car = np.zeros((10, 60, 3), dtype=np.uint8)
    low_car[:, :] = (30, 120, 50)
    assert heuristic_carrier(low_car) is None
    # Existing carriers still win on their colors.
    blue = np.zeros((40, 48, 3), dtype=np.uint8)
    blue[:, :] = (20, 40, 200)
    assert heuristic_carrier(blue)[0] == "USPS"
    assert "Garbage" in DELIVERY_CARRIERS
    assert carrier_display_name("Garbage") == "Garbage truck"
    assert "Garbage truck" in notification_sentence({"delivery_carrier": "Garbage"}, "the curb")
