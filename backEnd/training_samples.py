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
    try:  # Community sharing (opt-in, off by default): queue a sanitized copy.
        from backEnd import community

        community.queue_sample(image, box, record["label"], camera, stamp,
                               root=os.path.join(root, "community") if root != training_root() else None)
    except Exception:
        pass
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


_STATE = "state.json"
DEFAULT_SETTINGS = {
    "auto_enabled": True,     # nightly retrain when there is something new
    "auto_hour": 2,
    "auto_minute": 0,
    "threshold": 5,           # also retrain soon after this many new samples (0 = off)
    "rescan_after": False,    # re-scan last 7 days after an auto retrain
}


def load_state(root=None) -> dict:
    path = os.path.join(root or training_root(), _STATE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, root=None) -> None:
    root = root or training_root()
    os.makedirs(root, exist_ok=True)
    temp = os.path.join(root, _STATE + ".tmp")
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
    os.replace(temp, os.path.join(root, _STATE))


def get_settings(root=None) -> dict:
    merged = dict(DEFAULT_SETTINGS)
    merged.update(load_state(root).get("settings") or {})
    return merged


def save_settings(settings: dict, root=None) -> None:
    state = load_state(root)
    current = dict(DEFAULT_SETTINGS)
    current.update(state.get("settings") or {})
    current.update({k: settings[k] for k in settings if k in DEFAULT_SETTINGS})
    state["settings"] = current
    save_state(state, root)


def _dataset_files(root) -> dict:
    """``{label: [relative file, ...]}`` for every carrier label folder."""
    found = {}
    data = dataset_dir(root)
    for label in CARRIER_LABELS:
        folder = os.path.join(data, label)
        if os.path.isdir(folder):
            files = sorted(f for f in os.listdir(folder) if f.lower().endswith((".jpg", ".jpeg", ".png")))
            found[label] = ["dataset/%s/%s" % (label, f) for f in files]
        else:
            found[label] = []
    return found


def label_status(root=None) -> list:
    """Rows ``(folder, new, trained, trainable)`` for the Training dialog.

    Samples are never deleted by training: every retrain uses all samples,
    so "Trained" means "included in the last successful train" and "New"
    means "added since then".
    """
    root = root or training_root()
    trained = set(load_state(root).get("trained_files") or [])
    rows = []
    for label, files in _dataset_files(root).items():
        if files:
            done = sum(1 for f in files if f in trained)
            rows.append(("dataset/" + label, len(files) - done, done, True))
    for name, count in sorted(sample_counts(root).items()):
        if name.startswith("general/"):
            rows.append((name, count, 0, False))
    return rows


def new_sample_count(root=None) -> int:
    root = root or training_root()
    trained = set(load_state(root).get("trained_files") or [])
    return sum(1 for files in _dataset_files(root).values() for f in files if f not in trained)


def can_retrain(root=None):
    """``(ok, reason)``. Needs >= 2 labels and something new since the last train."""
    root = root or training_root()
    labels = [lab for lab, files in _dataset_files(root).items() if files]
    if len(labels) < 2:
        return False, "Need samples for at least two truck types (have %d)." % len(labels)
    if new_sample_count(root) == 0 and load_state(root).get("last_ok"):
        return False, "Nothing new to train since the last successful retrain."
    return True, ""


def auto_retrain_due(now, root=None):
    """Return "nightly", "threshold", or None. ``now`` is a datetime."""
    root = root or training_root()
    settings = get_settings(root)
    if not settings.get("auto_enabled"):
        return None
    ok, _reason = can_retrain(root)
    if not ok:
        return None
    state = load_state(root)
    threshold = int(settings.get("threshold") or 0)
    if threshold > 0 and new_sample_count(root) >= threshold:
        return "threshold"
    today = now.strftime("%Y-%m-%d")
    due = (now.hour, now.minute) >= (int(settings.get("auto_hour", 2)), int(settings.get("auto_minute", 0)))
    if due and state.get("last_nightly_date") != today:
        return "nightly"
    return None


def mark_nightly_checked(now, root=None) -> None:
    state = load_state(root)
    state["last_nightly_date"] = now.strftime("%Y-%m-%d")
    save_state(state, root)


def delete_samples(relative_paths, root=None) -> int:
    """Delete bad samples (paths relative to the training root)."""
    root = root or training_root()
    removed = 0
    for rel in relative_paths:
        path = os.path.normpath(os.path.join(root, rel))
        if not path.startswith(os.path.normpath(root)):
            continue
        try:
            os.remove(path)
            removed += 1
        except OSError:
            pass
    return removed


def list_samples(folder: str, root=None) -> list:
    """Relative paths of the images in ``folder`` (e.g. "dataset/USPS")."""
    root = root or training_root()
    base = os.path.join(root, *folder.split("/"))
    if not os.path.isdir(base):
        return []
    return ["%s/%s" % (folder, f) for f in sorted(os.listdir(base))
            if f.lower().endswith((".jpg", ".jpeg", ".png"))]


def read_samples(root=None) -> list:
    """All records from samples.jsonl (bad lines skipped)."""
    path = os.path.join(root or training_root(), "samples.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


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
    files = _dataset_files(root)
    state = load_state(root)
    try:
        weight, bias, counts = fit_carrier_model(data)
        target = user_carrier_model_path(root)
        temp = target + ".tmp.onnx"
        export_onnx(temp, weight, bias)
        # Keep the previous model as a backup; swap only after a good export.
        if os.path.isfile(target):
            os.replace(target, target.replace(".onnx", ".prev.onnx"))
            if os.path.isfile(target + ".json"):
                os.replace(target + ".json", target.replace(".onnx", ".prev.onnx") + ".json")
        os.replace(temp, target)
        os.replace(temp + ".json", target + ".json")
    except Exception as exc:
        state = load_state(root)
        state.update({"last_attempt": time.strftime("%Y-%m-%dT%H:%M:%S"), "last_ok": state.get("last_ok", False),
                      "last_result": "failed: %s" % exc})
        save_state(state, root)
        raise
    total = sum(counts.values())
    state = load_state(root)
    state.update({
        "trained_files": [f for group in files.values() for f in group],
        "last_trained": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_attempt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "last_ok": True,
        "last_result": "success: %d samples (%s)" % (
            total, ", ".join("%s=%d" % kv for kv in counts.items() if kv[1])),
        "last_counts": counts,
    })
    save_state(state, root)
    return counts
