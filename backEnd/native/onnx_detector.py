"""ONNX Runtime YOLO detector on the local RGB contract.

The default weights are Megvii YOLOX-Nano (Apache-2.0), fetched by
``scripts/fetch_detector_model.py`` and not stored in git. COCO classes map
onto the rule engine as ``person``, ``vehicle``, and ``animal``.

Ultralytics YOLOv8 and YOLOv10 exports are AGPL-3.0. This module can run a
raw YOLOv8 ONNX tensor if you point ``SIGHTHOUND_DETECTOR_MODEL`` at one, but
the fetch script does not download those weights. Prefer YOLOX-Nano.

Execution providers, in order, when ``enable_gpu`` is set:

* Windows: DirectML, then CUDA, then CPU
* Linux and macOS: CUDA, then CPU

The Windows installer ships ``onnxruntime-directml``. DirectML is used when
that provider loads, and CPU otherwise. After boxes are decoded, plate, face, and
delivery recognizers in ``attribute_recognizers`` run on the person and
vehicle crops when their ONNX files are present. If a detector output is
named ``plate_text``, ``face_identity``, or ``delivery_carrier``, that value
is still copied onto each detection.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional, Sequence

from backEnd.native.detector import (
    RESERVED_ATTRIBUTE_KEYS,
    BoundingBox,
    Detection,
)

_COCO_PERSON = {0}
_COCO_VEHICLE = {1, 2, 3, 5, 6, 7}  # bicycle, car, motorcycle, bus, train, truck
_COCO_ANIMAL = {14, 15, 16, 17, 18, 19, 20, 21, 22, 23}
_SIGHTHOUND_LABELS = {0: "person", 1: "vehicle", 2: "animal"}

_ENV_MODEL = "SIGHTHOUND_DETECTOR_MODEL"


def select_execution_providers(
    available: Sequence[str],
    platform: Optional[str] = None,
    enable_gpu: bool = True,
) -> list[str]:
    """Provider order for this machine. CPU is always last."""
    names = list(available)
    system = platform or sys.platform
    chosen: list[str] = []
    if enable_gpu:
        if system == "win32" and "DmlExecutionProvider" in names:
            chosen.append("DmlExecutionProvider")
        if "CUDAExecutionProvider" in names:
            chosen.append("CUDAExecutionProvider")
    if "CPUExecutionProvider" in names:
        chosen.append("CPUExecutionProvider")
    if not chosen:
        chosen.append("CPUExecutionProvider")
    return chosen


def installed_models_dirs() -> list[str]:
    """Model folders for a frozen app, then the source-tree ``models`` directory."""
    folders = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        folders.append(os.path.join(exe_dir, "models"))
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            folders.append(os.path.join(meipass, "models"))
    folders.append(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "models"))
    )
    unique: list[str] = []
    seen = set()
    for folder in folders:
        key = os.path.normcase(os.path.abspath(folder))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.abspath(folder))
    return unique


def default_model_candidates(model_dir: Optional[str] = None) -> list[str]:
    candidates = []
    env = os.environ.get(_ENV_MODEL)
    if env:
        candidates.append(env)
    if model_dir:
        candidates.append(os.path.join(model_dir, "yolox_nano.onnx"))
        candidates.append(os.path.join(model_dir, "detector.onnx"))
    for folder in installed_models_dirs():
        candidates.append(os.path.join(folder, "yolox_nano.onnx"))
        candidates.append(os.path.join(folder, "detector.onnx"))
    return candidates


def resolve_model_path(model_dir: Optional[str] = None, model_path: Optional[str] = None) -> str:
    """Find an ONNX file. Raises ``FileNotFoundError`` with setup instructions."""
    ordered = []
    if model_path:
        ordered.append(model_path)
    ordered.extend(default_model_candidates(model_dir))
    for candidate in ordered:
        if candidate and os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "No ONNX detector model found. Run `python scripts/fetch_detector_model.py` "
        "or set %s to a .onnx file. Weights are not committed to the repository."
        % _ENV_MODEL
    )


def _read_spec(model_path: str, session: Any) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "layout": "",
        "normalize": True,
        "pad": 114,
        "resize": "bilinear",
        "classes": "coco",
        "score_threshold": 0.25,
        "input_size": 0,
    }
    sidecar = model_path + ".json"
    if os.path.isfile(sidecar):
        with open(sidecar, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        if isinstance(loaded, dict):
            spec.update(loaded)
    metadata = {}
    for prop in getattr(session, "get_modelmeta")().custom_metadata_map.items():
        metadata[prop[0]] = prop[1]
    # Session metadata is a dict already.
    meta = session.get_modelmeta().custom_metadata_map
    if meta.get("sighthound_layout"):
        spec["layout"] = spec["layout"] or meta["sighthound_layout"]
    if "sighthound_normalize" in meta and "normalize" not in (spec.get("_from_sidecar") or []):
        if not os.path.isfile(sidecar):
            spec["normalize"] = meta["sighthound_normalize"] not in ("0", "false", "False")
    if meta.get("sighthound_classes") and not os.path.isfile(sidecar):
        spec["classes"] = meta["sighthound_classes"]
    if meta.get("sighthound_resize") and not os.path.isfile(sidecar):
        spec["resize"] = meta["sighthound_resize"]
    if meta.get("sighthound_pad") and not os.path.isfile(sidecar):
        spec["pad"] = int(meta["sighthound_pad"])
    _ = metadata
    return spec


def _input_hw(session: Any, spec: dict[str, Any]) -> tuple[int, int, bool]:
    """Return ``(height, width, channels_first)``."""
    shape = session.get_inputs()[0].shape
    channels_first = True
    if len(shape) == 4 and shape[-1] == 3:
        channels_first = False
    height = spec.get("input_size") or 0
    width = spec.get("input_size") or 0
    if len(shape) == 4:
        spatial = shape[2:4] if channels_first else shape[1:3]
        if _is_dim(spatial[0]):
            height = int(spatial[0])
        if _is_dim(spatial[1]):
            width = int(spatial[1])
    if not height:
        height = 640
    if not width:
        width = height
    return int(height), int(width), channels_first


def _is_dim(value: Any) -> bool:
    return isinstance(value, int) and value > 0


def _letterbox(rgb: bytes, width: int, height: int, size: int, pad: int, normalize: bool, resize: str, channels_first: bool):
    import numpy as np
    from PIL import Image

    image = Image.frombytes("RGB", (width, height), rgb)
    scale = min(size / float(height), size / float(width))
    new_w = max(1, int(round(width * scale)))
    new_h = max(1, int(round(height * scale)))
    resample = Image.Resampling.NEAREST if resize == "nearest" else Image.Resampling.BILINEAR
    resized = image.resize((new_w, new_h), resample)
    canvas = Image.new("RGB", (size, size), (int(pad), int(pad), int(pad)))
    pad_x = (size - new_w) // 2
    pad_y = (size - new_h) // 2
    canvas.paste(resized, (pad_x, pad_y))
    array = np.asarray(canvas).astype(np.float32)
    if normalize:
        array *= 1.0 / 255.0
    if channels_first:
        blob = array.transpose(2, 0, 1)[None, ...]
    else:
        blob = array[None, ...]
    return np.ascontiguousarray(blob), scale, pad_x, pad_y


def _yolox_grids(count: int, size: int):
    import numpy as np

    for strides in ([8, 16, 32], [8, 16, 32, 64]):
        grids = []
        expanded = []
        total = 0
        for stride in strides:
            cells_h = size // stride
            cells_w = size // stride
            if cells_h < 1 or cells_w < 1:
                total = -1
                break
            total += cells_h * cells_w
            # Same order as torch.meshgrid(arange(h), arange(w), indexing="ij"):
            # x is the column and increases across each row.
            yv, xv = np.meshgrid(
                np.arange(cells_h), np.arange(cells_w), indexing="ij",
            )
            grid = np.stack((xv, yv), axis=2).reshape(-1, 2).astype(np.float32)
            grids.append(grid)
            expanded.append(np.full((grid.shape[0], 1), stride, dtype=np.float32))
        if total == count:
            return np.concatenate(grids, 0), np.concatenate(expanded, 0)
    return None, None


def _cxcywh_to_xyxy(boxes):
    import numpy as np

    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] / 2.0
    out[:, 1] = boxes[:, 1] - boxes[:, 3] / 2.0
    out[:, 2] = boxes[:, 0] + boxes[:, 2] / 2.0
    out[:, 3] = boxes[:, 1] + boxes[:, 3] / 2.0
    return out


def _rows(array):
    import numpy as np

    arr = np.asarray(array)
    if arr.ndim == 3 and arr.shape[0] == 1:
        arr = arr[0]
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2:
        arr = arr.reshape(-1, arr.shape[-1])
    # Channel-first YOLO heads are [C, N] with C much smaller than the anchor count.
    # A single xyxy row is [1, 6] and must stay a row.
    if arr.shape[0] < arr.shape[1] and arr.shape[0] <= 128 and arr.shape[1] >= 100:
        arr = arr.T
    return arr


def _decode_predictions(array, layout: str, input_size: int):
    import numpy as np

    rows = _rows(array).astype(np.float32)
    if layout == "xyxy" or rows.shape[1] == 6:
        boxes = rows[:, 0:4]
        scores = rows[:, 4]
        classes = rows[:, 5].astype(np.int32)
        return boxes, scores, classes
    if layout == "yolox" or rows.shape[1] >= 6 and layout != "yolov8":
        if layout == "yolox" or rows.shape[1] >= 85:
            grids, strides = _yolox_grids(rows.shape[0], input_size) if layout == "yolox" or rows.shape[1] > 6 else (None, None)
            pred = rows.copy()
            if grids is not None:
                pred[:, 0:2] = (pred[:, 0:2] + grids) * strides
                pred[:, 2:4] = np.exp(np.clip(pred[:, 2:4], -8.0, 8.0)) * strides
            boxes = _cxcywh_to_xyxy(pred[:, 0:4])
            objectness = pred[:, 4:5]
            class_scores = pred[:, 5:]
            if class_scores.size == 0:
                scores = objectness.reshape(-1)
                classes = np.zeros(scores.shape, dtype=np.int32)
            else:
                weighted = objectness * class_scores
                classes = weighted.argmax(axis=1).astype(np.int32)
                scores = weighted.max(axis=1)
            return boxes, scores, classes
    # YOLOv8 raw: cx, cy, w, h, class scores. No objectness.
    boxes = _cxcywh_to_xyxy(rows[:, 0:4])
    class_scores = rows[:, 4:]
    classes = class_scores.argmax(axis=1).astype(np.int32)
    scores = class_scores.max(axis=1)
    return boxes, scores, classes


def _map_label(class_id: int, mode: str) -> Optional[str]:
    if mode == "sighthound":
        return _SIGHTHOUND_LABELS.get(int(class_id))
    class_id = int(class_id)
    if class_id in _COCO_PERSON:
        return "person"
    if class_id in _COCO_VEHICLE:
        return "vehicle"
    if class_id in _COCO_ANIMAL:
        return "animal"
    return None


def _unmap_box(box, scale: float, pad_x: int, pad_y: int, width: int, height: int) -> BoundingBox:
    x1 = (float(box[0]) - pad_x) / scale
    y1 = (float(box[1]) - pad_y) / scale
    x2 = (float(box[2]) - pad_x) / scale
    y2 = (float(box[3]) - pad_y) / scale
    x1 = max(0.0, min(float(width - 1), x1))
    y1 = max(0.0, min(float(height - 1), y1))
    x2 = max(0.0, min(float(width), x2))
    y2 = max(0.0, min(float(height), y2))
    box_w = max(1, int(round(x2 - x1)))
    box_h = max(1, int(round(y2 - y1)))
    return BoundingBox(int(round(x1)), int(round(y1)), box_w, box_h)


def _iou(a: BoundingBox, b: BoundingBox) -> float:
    ax2 = a.x + a.width
    ay2 = a.y + a.height
    bx2 = b.x + b.width
    by2 = b.y + b.height
    ix1 = max(a.x, b.x)
    iy1 = max(a.y, b.y)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = a.width * a.height + b.width * b.height - inter
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


def _nms(boxes: list[BoundingBox], scores: list[float], limit: int = 50) -> list[int]:
    order = sorted(range(len(boxes)), key=lambda i: scores[i], reverse=True)
    kept = []
    while order and len(kept) < limit:
        current = order.pop(0)
        kept.append(current)
        order = [other for other in order if _iou(boxes[current], boxes[other]) < 0.5]
    return kept


def _attribute_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    try:
        import numpy as np

        array = np.asarray(value)
        if array.shape == () or array.size == 1:
            item = array.reshape(-1)[0]
            if isinstance(item, (bytes, np.bytes_)):
                return item.decode("utf-8", "replace")
            return str(item)
    except Exception:
        return str(value)
    return None


class OnnxYoloDetector:
    """``detect_rgb`` implementation backed by ONNX Runtime."""

    def __init__(self, logger: Any, model_path: str, enable_gpu: bool = False) -> None:
        import onnxruntime as ort

        self._logger = logger
        if model_path and os.path.isdir(model_path):
            model_path = resolve_model_path(model_path)
        elif not model_path or not os.path.isfile(model_path):
            model_path = resolve_model_path(None, model_path if model_path and os.path.isfile(model_path or "") else None)
        self.model_path = model_path
        available = ort.get_available_providers()
        providers = select_execution_providers(available, enable_gpu=enable_gpu)
        try:
            self._session = ort.InferenceSession(model_path, providers=providers)
        except Exception:
            self._session = ort.InferenceSession(
                model_path, providers=["CPUExecutionProvider"]
            )
        self.providers = list(self._session.get_providers())
        self._spec = _read_spec(model_path, self._session)
        self._input_name = self._session.get_inputs()[0].name
        self._output_names = [item.name for item in self._session.get_outputs()]
        self._height, self._width, self._channels_first = _input_hw(self._session, self._spec)
        self._log("info", "ONNX detector %s providers=%s" % (model_path, ",".join(self.providers)))
        self._attributes = None
        try:
            from backEnd.native.attribute_recognizers import AttributePipeline

            self._attributes = AttributePipeline.from_environment(
                enable_gpu=enable_gpu, logger=logger
            )
        except Exception as exc:
            self._log("error", "attribute recognizers were not attached: %s" % exc)

    def set_gallery_db(self, path: Optional[str]) -> None:
        """Point face matching at the local object database."""
        pipeline = self._attributes
        if pipeline is not None:
            pipeline.set_gallery_db(path)

    def _log(self, level: str, message: str) -> None:
        logger = self._logger
        if logger is None:
            return
        method = getattr(logger, level, None) or getattr(logger, "info", None)
        if method is not None:
            method(message)

    def detect_rgb(self, rgb: bytes, width: int, height: int) -> list[Detection]:
        from backEnd.native.detector import parse_rgb_body

        frame = parse_rgb_body(rgb, width, height)
        side = int(self._height)
        blob, scale, pad_x, pad_y = _letterbox(
            frame,
            int(width),
            int(height),
            side,
            int(self._spec.get("pad", 114) or 0),
            bool(self._spec.get("normalize", True)),
            str(self._spec.get("resize", "bilinear")),
            self._channels_first,
        )
        outputs = self._session.run(None, {self._input_name: blob})
        attributes = self._extra_attributes(outputs)
        layout = str(self._spec.get("layout") or "")
        boxes, scores, classes = _decode_predictions(outputs[0], layout, side)
        threshold = float(self._spec.get("score_threshold", 0.25) or 0.25)
        mode = str(self._spec.get("classes") or "coco")
        found_boxes: list[BoundingBox] = []
        found_scores: list[float] = []
        found_types: list[str] = []
        for box, score, class_id in zip(boxes, scores, classes):
            if float(score) < threshold:
                continue
            label = _map_label(int(class_id), mode)
            if not label:
                continue
            found_boxes.append(_unmap_box(box, scale, pad_x, pad_y, int(width), int(height)))
            found_scores.append(float(score))
            found_types.append(label)
        detections = []
        for index in _nms(found_boxes, found_scores):
            detections.append(
                Detection(
                    type=found_types[index],
                    bounding_box=found_boxes[index],
                    score=found_scores[index],
                    attributes=dict(attributes),
                )
            )
        if detections and self._attributes is not None:
            self._attributes.annotate(rgb, width, height, detections)
        return detections

    def _extra_attributes(self, outputs: list[Any]) -> dict[str, Any]:
        attributes: dict[str, Any] = {}
        for name, value in zip(self._output_names, outputs):
            if name not in RESERVED_ATTRIBUTE_KEYS:
                continue
            text = _attribute_text(value)
            if text:
                attributes[name] = text
        return attributes


def create_detector(logger: Any, model_dir: Optional[str] = None, enable_gpu: bool = False, model_path: Optional[str] = None):
    """Open the ONNX detector used by the HTTP service and the in-process client."""
    path = model_path if model_path and os.path.isfile(model_path) else None
    if path is None:
        path = resolve_model_path(model_dir, model_path)
    return OnnxYoloDetector(logger, path, enable_gpu=enable_gpu)
