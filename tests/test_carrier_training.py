"""The carrier training script exports a model that separates solid colors."""

import numpy as np
import pytest
from PIL import Image

from backEnd.native.attribute_recognizers import AttributePipeline, OnnxGraph
from backEnd.native.detector import BoundingBox, Detection
from scripts.train_carrier_classifier import export_onnx, fit_carrier_model

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")


def _solid(folder, name, color, count=4):
    folder.mkdir(parents=True)
    for index in range(count):
        image = Image.new("RGB", (32, 32), color)
        image.save(folder / ("%s-%d.png" % (name, index)))


def test_trained_linear_model_labels_its_own_colors(tmp_path):
    root = tmp_path / "photos"
    _solid(root / "USPS", "usps", (20, 40, 180))
    _solid(root / "UPS", "ups", (150, 90, 40))
    _solid(root / "none", "none", (140, 140, 140))
    weight, bias, counts = fit_carrier_model(str(root))
    assert counts["USPS"] == 4
    path = export_onnx(str(tmp_path / "carrier.onnx"), weight, bias)
    pipeline = AttributePipeline(carrier=OnnxGraph(path), use_heuristic=False)

    def label(color):
        image = np.zeros((40, 40, 3), dtype=np.uint8)
        image[:, :] = color
        found = Detection("vehicle", BoundingBox(0, 0, 40, 40), 0.9)
        pipeline.annotate(image.tobytes(), 40, 40, [found])
        return found.attributes.get("delivery_carrier")

    assert label((20, 40, 180)) == "USPS"
    assert label((150, 90, 40)) == "UPS"
    assert label((140, 140, 140)) in (None, "none")
