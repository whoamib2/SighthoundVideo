"""Labeled training samples collected with "Mark as Missed / Wrong Label".

Layout under ``%LOCALAPPDATA%\\VigilCam\\training`` (override with
``VIGILCAM_TRAINING_DIR``)::

    dataset/            folders read by the carrier trainer
        none/ USPS/ FedEx/ UPS/ Amazon/ Garbage/ Bus/ School bus/
    general/            samples the app cannot retrain on yet
        Person/ Vehicle/ Animal/ Delivery/ Other/ not_Person/ ...
    samples.jsonl       one JSON line per sample (camera, time, box, label)
    models/carrier.onnx written by "Retrain now", hot-loaded by the backend

Only the carrier/garbage-truck color classifier is retrained in the app.
Person/vehicle/animal samples are stored for a future detector fine-tune;
YOLOX itself is not retrained here.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid

from backEnd.native.carrier_features import CARRIER_LABELS
from backEnd.native.detector import DELIVERY_CARRIERS, carrier_display_name

GENERAL_LABELS = ("Person", "Vehicle", "Animal", "Delivery vehicle")
OTHER_LABEL = "Other"
MISSED = "missed"
WRONG = "wrong"


def training_root() -> str:
    override = os.environ.get("VIGILCAM_TRAINING_DIR")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "VigilCam", "training")


def dataset_dir(root=None) -> str:
    return os.path.join(root or training_root(), "dataset")


def user_carrier_model_path(root=None) -> str:
    return os.path.join(root or training_root(), "models", "carrier.onnx")


def label_choices() -> list:
    """UI labels: general types, every carrier type, then Other."""
    return list(GENERAL_LABELS) + [carrier_display_name(c) for c in DELIVERY_CARRIERS] + [OTHER_LABEL]


def _carrier_for_display(label: str):
    for carrier in DELIVERY_CARRIERS:
        if label in (carrier, carrier_display_name(carrier)):
            return carrier
    return None


def _safe(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9 _-]+", "", str(text or "")).strip().replace(" ", "_")
    return cleaned[:40] or "unnamed"


def folder_for(label: str, kind: str = MISSED, other_text: str = "") -> str:
    """Relative folder for a sample.

    Missed carrier types go to ``dataset/<Carrier>``. "Not a <Carrier>"
    corrections go to ``dataset/none`` so the retrained classifier learns
    to reject them. Everything else lands under ``general/``.
    """
    carrier = _carrier_for_display(label)
    if carrier is not None:
        return "dataset/" + (carrier if kind == MISSED else "none")
    if label == OTHER_LABEL:
        name = "Other/" + _safe(other_text)
    elif label == "Delivery vehicle":
        name = "Delivery"
    else:
        name = _safe(label)
    return "general/" + (name if kind == MISSED else "not_" + name)


def save_sample(image, label, box=None, camera="", timestamp_ms=0, kind=MISSED,
                other_text="", root=None) -> str:
    """Crop ``image`` (PIL image or path) to ``box`` and store it.

    ``box`` is ``(x1, y1, x2, y2)`` in image pixels, or ``None`` for the whole
    frame. Returns the saved crop path.
    """
    from PIL import Image

    root = root or training_root()
    if not isinstance(image, Image.Image):
        image = Image.open(image)
    image = image.convert("RGB")
    width, height = image.size
    if box:
        x1, y1, x2, y2 = [int(round(v)) for v in box]
        x1, x2 = sorted((max(0, min(width, x1)), max(0, min(width, x2))))
        y1, y2 = sorted((max(0, min(height, y1)), max(0, min(height, y2))))
        if x2 - x1 < 4 or y2 - y1 < 4:
            box = None
        else:
            box = (x1, y1, x2, y2)
    if not box:
        box = (0, 0, width, height)
    crop = image.crop(box)
    relative = folder_for(label, kind, other_text)
    folder = os.path.join(root, *relative.split("/"))
    os.makedirs(folder, exist_ok=True)
    stamp = int(timestamp_ms or time.time() * 1000)
    name = "%s_%d_%s.jpg" % (_safe(camera or "camera"), stamp, uuid.uuid4().hex[:8])
    path = os.path.join(folder, name)
    crop.save(path, "JPEG", quality=92)
    record = {
        "file": os.path.relpath(path, root).replace(os.sep, "/"),
        "label": label if label != OTHER_LABEL else "Other:%s" % (other_text or ""),
        "kind": kind,
        "camera": camera,
        "timestamp_ms": stamp,
        "box": list(box),
        "frame_size": [width, height],
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    with open(os.path.join(root, "samples.jsonl"), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return path


def sample_counts(root=None) -> dict:
    """``{"dataset/USPS": 3, "general/Person": 1, ...}`` for every non-empty folder."""
    root = root or training_root()
    counts = {}
    for top in ("dataset", "general"):
        base = os.path.join(root, top)
        if not os.path.isdir(base):
            continue
        for current, _dirs, files in os.walk(base):
            images = [f for f in files if f.lower().endswith((".jpg", ".jpeg", ".png"))]
            if images:
                rel = os.path.relpath(current, root).replace(os.sep, "/")
                counts[rel] = len(images)
    return counts


def retrain_carrier_model(root=None) -> dict:
    """Fit the carrier classifier on ``dataset/`` and write the user model.

    Returns per-label counts. Raises ``ValueError`` when fewer than two labels
    have samples. The backend reloads the new file within ~15 seconds.
    """
    from backEnd.native.carrier_training import export_onnx, fit_carrier_model

    root = root or training_root()
    data = dataset_dir(root)
    present = [lab for lab in CARRIER_LABELS if os.path.isdir(os.path.join(data, lab))
               and os.listdir(os.path.join(data, lab))]
    if len(present) < 2:
        raise ValueError(
            "Need samples for at least two of: %s (have %s). Mark a few "
            "\"Not a ...\" corrections to fill 'none'." % (", ".join(CARRIER_LABELS), ", ".join(present) or "none")
        )
    weight, bias, counts = fit_carrier_model(data)
    target = user_carrier_model_path(root)
    temp = target + ".tmp.onnx"
    export_onnx(temp, weight, bias)
    os.replace(temp, target)
    os.replace(temp + ".json", target + ".json")
    return counts
