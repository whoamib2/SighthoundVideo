"""Mark-as-missed samples, folder layout, retraining, and hot reload."""

import json
import os

import numpy as np
import pytest
from PIL import Image

from backEnd import training_samples as ts
from backEnd.native.attribute_recognizers import AttributePipeline
from backEnd.native.detector import BoundingBox, Detection


def _frame(color=(30, 120, 50), size=(64, 48)):
    return Image.new("RGB", size, color)


def test_label_choices_follow_type_list():
    choices = ts.label_choices()
    for name in ("Person", "Vehicle", "Animal", "Delivery vehicle", "USPS", "UPS",
                 "FedEx", "Amazon", "Garbage truck", "Other"):
        assert name in choices


def test_folder_mapping():
    assert ts.folder_for("Garbage truck") == "dataset/Garbage"
    assert ts.folder_for("USPS") == "dataset/USPS"
    assert ts.folder_for("UPS", ts.WRONG) == "dataset/none"
    assert ts.folder_for("Person") == "general/Person"
    assert ts.folder_for("Animal", ts.WRONG) == "general/not_Animal"
    assert ts.folder_for("Delivery vehicle") == "general/Delivery"
    assert ts.folder_for("Other", other_text="Ice cream truck!") == "general/Other/Ice_cream_truck"


def test_save_sample_crops_and_logs(tmp_path):
    path = ts.save_sample(_frame(), "Garbage truck", (10, 5, 40, 35), "Driveway", 1234,
                          root=str(tmp_path))
    assert os.path.dirname(path) == str(tmp_path / "dataset" / "Garbage")
    assert Image.open(path).size == (30, 30)
    record = json.loads((tmp_path / "samples.jsonl").read_text().splitlines()[0])
    assert record["camera"] == "Driveway" and record["box"] == [10, 5, 40, 35]
    assert record["label"] == "Garbage truck" and record["kind"] == "missed"
    # No / tiny box falls back to the whole frame.
    whole = ts.save_sample(_frame(), "Person", None, "Driveway", 5, root=str(tmp_path))
    assert Image.open(whole).size == (64, 48)
    assert ts.sample_counts(str(tmp_path)) == {"dataset/Garbage": 1, "general/Person": 1}


def test_retrain_needs_two_labels_then_hot_loads(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    root = str(tmp_path)
    for _ in range(3):
        ts.save_sample(_frame((30, 120, 50)), "Garbage truck", None, "c", 1, root=root)
    with pytest.raises(ValueError):
        ts.retrain_carrier_model(root)
    for _ in range(3):
        ts.save_sample(_frame((20, 40, 200)), "USPS", None, "c", 2, root=root)
    counts = ts.retrain_carrier_model(root)
    assert counts["Garbage"] == 3 and counts["USPS"] == 3
    model = ts.user_carrier_model_path(root)
    assert os.path.isfile(model)

    pipeline = AttributePipeline(use_heuristic=False)
    pipeline.watch_user_carrier_model(model)
    assert pipeline.carrier is not None
    green = np.zeros((32, 32, 3), dtype=np.uint8)
    green[:, :] = (30, 120, 50)
    det = Detection(type="vehicle", bounding_box=BoundingBox(0, 0, 32, 32), score=0.9)
    pipeline.annotate(green.tobytes(), 32, 32, [det])
    assert det.attributes["delivery_carrier"] == "Garbage"


def test_dialog_module_and_menu_ids_import():
    pytest.importorskip("wx")
    from frontEnd import MenuIds
    from frontEnd.TrainingDialogs import MarkMissedDialog, TrainingSamplesDialog

    assert MenuIds.kMarkMissed.startswith("Mark as Missed")
    assert MarkMissedDialog and TrainingSamplesDialog
