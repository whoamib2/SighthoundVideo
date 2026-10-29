"""Plate, face, and delivery-carrier recognizers on ONNX Runtime.

Recognizers run on the person and vehicle crops already produced by the YOLO
detector. Missing weight files are skipped. Detection itself still returns.

Windows execution providers are DirectML, then CUDA, then CPU. Linux and
macOS use CUDA, then CPU. The same order as the YOLO detector.

Fetched weights are documented in ``models/README.md``. Tests use the tiny
graphs in ``test_attribute_models.py`` and do not download anything.

Face embeddings for enrolled people are read from the local object database.
They are not sent anywhere by this module.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional, Sequence

from backEnd.attribute_text import (
    DELIVERY_CARRIER,
    DELIVERY_CONFIDENCE,
    FACE_CONFIDENCE,
    FACE_IDENTITY,
    PLATE_CONFIDENCE,
    PLATE_TEXT,
)
from backEnd.native.carrier_features import (
    CARRIER_LABELS,
    heuristic_carrier,
    image_features,
    is_school_bus_yellow,
)
from backEnd.native.onnx_detector import installed_models_dirs, select_execution_providers

_ENV_PLATE_DETECTOR = "SIGHTHOUND_PLATE_DETECTOR_MODEL"
_ENV_PLATE_OCR = "SIGHTHOUND_PLATE_OCR_MODEL"
_ENV_FACE_DETECTOR = "SIGHTHOUND_FACE_DETECTOR_MODEL"
_ENV_FACE_EMBED = "SIGHTHOUND_FACE_EMBED_MODEL"
_ENV_CARRIER = "SIGHTHOUND_CARRIER_MODEL"
_ENV_GALLERY = "SIGHTHOUND_OBJECT_DB"
_ENV_THRESHOLD = "SIGHTHOUND_FACE_THRESHOLD"
_ENV_HEURISTIC = "SIGHTHOUND_CARRIER_HEURISTIC"

_DEFAULT_FILES = {
    _ENV_PLATE_DETECTOR: "plate_detector.onnx",
    _ENV_PLATE_OCR: "plate_ocr_cct_xs.onnx",
    _ENV_FACE_DETECTOR: "face_detector_yunet.onnx",
    _ENV_FACE_EMBED: "face_recognition_sface.onnx",
    _ENV_CARRIER: "carrier.onnx",
}

# YuNet landmark order matches the OpenCV SFace alignment template.
_SFACE_TEMPLATE = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)

_FAST_PLATE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_"


def _models_dir() -> str:
    folders = installed_models_dirs()
    for folder in folders:
        if os.path.isdir(folder):
            return folder
    return folders[0]


def resolve_optional_model(env_name: str, filename: str) -> Optional[str]:
    """Return a model path. An env var set to empty disables the default file."""
    if env_name in os.environ:
        value = os.environ.get(env_name) or ""
        if value and os.path.isfile(value):
            return value
        return None
    candidate = os.path.join(_models_dir(), filename)
    if os.path.isfile(candidate):
        return candidate
    return None


def _open_session(path: str, enable_gpu: bool):
    import onnxruntime as ort

    available = ort.get_available_providers()
    providers = select_execution_providers(available, enable_gpu=enable_gpu)
    try:
        return ort.InferenceSession(path, providers=providers)
    except Exception:
        return ort.InferenceSession(path, providers=["CPUExecutionProvider"])


def _read_info(path: str, session) -> dict:
    info: dict[str, Any] = {}
    sidecar = path + ".json"
    if os.path.isfile(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                info.update(loaded)
        except (OSError, ValueError):
            pass
    try:
        meta = session.get_modelmeta().custom_metadata_map
    except Exception:
        meta = {}
    layout = meta.get("sighthound_layout") or meta.get("layout")
    if layout and "layout" not in info:
        info["layout"] = layout
    return info


def _spatial_size(session, info: dict) -> tuple[Optional[int], Optional[int], str]:
    shape = list(session.get_inputs()[0].shape)
    layout = "nchw"

    def _dim(value, fallback):
        if isinstance(value, int) and value > 0:
            return int(value)
        return fallback

    height = _dim(info.get("img_height"), None)
    width = _dim(info.get("img_width"), None)
    if len(shape) == 4 and shape[-1] == 3:
        layout = "nhwc"
        height = _dim(shape[1], height)
        width = _dim(shape[2], width)
    elif len(shape) == 4:
        height = _dim(shape[2], height)
        width = _dim(shape[3], width)
    return height, width, layout


def _resize(image, out_h: int, out_w: int):
    import numpy as np

    height, width = image.shape[:2]
    if height == out_h and width == out_w:
        return image
    if height <= 0 or width <= 0:
        return np.zeros((out_h, out_w, image.shape[2]), dtype=image.dtype)
    ys = (np.arange(out_h) * height) // out_h
    xs = (np.arange(out_w) * width) // out_w
    ys = np.clip(ys, 0, height - 1)
    xs = np.clip(xs, 0, width - 1)
    return image[ys][:, xs]


def _as_image(rgb: bytes, width: int, height: int):
    import numpy as np

    return np.frombuffer(rgb, dtype=np.uint8).reshape((int(height), int(width), 3))


def _blob(image, channels: str, scale: float):
    import numpy as np

    values = image.astype(np.float32) * float(scale)
    if channels == "nhwc":
        return values.reshape((1, values.shape[0], values.shape[1], values.shape[2]))
    return np.transpose(values, (2, 0, 1))[None, ...]


def _crop_image(image, x1: int, y1: int, x2: int, y2: int):
    height, width = image.shape[:2]
    left = max(0, min(width - 1, int(x1)))
    top = max(0, min(height - 1, int(y1)))
    right = max(left + 1, min(width, int(x2)))
    bottom = max(top + 1, min(height, int(y2)))
    return image[top:bottom, left:right]


class OnnxGraph:
    """One ONNX file plus the layout hint that says how to read it."""

    def __init__(self, path: str, enable_gpu: bool = False) -> None:
        self.path = path
        self.session = _open_session(path, enable_gpu)
        self.info = _read_info(path, self.session)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [item.name for item in self.session.get_outputs()]
        self.height, self.width, self.channels = _spatial_size(self.session, self.info)
        self.layout = str(self.info.get("layout") or self._guess_layout())

    def _guess_layout(self) -> str:
        names = set(self.output_names)
        if "cls_8" in names and "bbox_8" in names:
            return "yunet"
        if "chars" in names:
            return "strip-ocr"
        if "embedding" in names:
            return "embedding"
        if "logits" in names and self.input_name == "features":
            return "carrier-linear"
        if "logits" in names:
            return "carrier-logits"
        if "plate" in names or "face" in names:
            return "box"
        return ""

    def run(self, feed):
        return self.session.run(None, {self.input_name: feed})

    def run_image(self, image, scale: Optional[float] = None):
        if not self.height or not self.width:
            raise ValueError("model %s has no fixed input size" % self.path)
        if scale is None:
            scale = float(self.info.get("scale") or 1.0)
        resized = _resize(image, int(self.height), int(self.width))
        return self.run(_blob(resized, self.channels, scale)), resized


def _output_map(graph: OnnxGraph, values) -> dict:
    return {name: values[index] for index, name in enumerate(graph.output_names)}


def _box_from_network(values, net_w: int, net_h: int, crop_w: int, crop_h: int):
    import numpy as np

    raw = np.asarray(values, dtype=np.float32).reshape(-1)
    if raw.size < 5:
        return None
    x1, y1, x2, y2, score = [float(item) for item in raw[:5]]
    if score < 0.3 or net_w <= 0 or net_h <= 0:
        return None
    if x2 < x1:
        x1, x2 = x2, x1
    if y2 < y1:
        y1, y2 = y2, y1
    sx = float(crop_w) / float(net_w)
    sy = float(crop_h) / float(net_h)
    left = int(round(x1 * sx))
    top = int(round(y1 * sy))
    right = int(round(x2 * sx))
    bottom = int(round(y2 * sy))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom, score


def _decode_strip(values) -> str:
    chars = []
    for value in values.reshape(-1).tolist():
        code = int(round(float(value)))
        if 48 <= code <= 57 or 65 <= code <= 90 or 97 <= code <= 122:
            chars.append(chr(code).upper())
    return "".join(chars)


def _decode_fast_plate(values, alphabet: str, pad: str) -> tuple[str, float]:
    import numpy as np

    logits = np.asarray(values, dtype=np.float32)
    if logits.ndim == 3:
        logits = logits[0]
    if logits.ndim != 2 or logits.shape[-1] < 2:
        return "", 0.0
    choice = logits.argmax(axis=-1)
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exp = np.exp(shifted)
    prob = exp / np.maximum(exp.sum(axis=-1, keepdims=True), 1e-6)
    confidence = float(prob.max(axis=-1).mean())
    chars = []
    for index in choice.tolist():
        if int(index) < 0 or int(index) >= len(alphabet):
            continue
        char = alphabet[int(index)]
        if char == pad or char == "_":
            continue
        chars.append(char)
    return "".join(chars), confidence


def _softmax_choice(logits) -> tuple[int, float]:
    import numpy as np

    row = np.asarray(logits, dtype=np.float32).reshape(-1)
    if row.size == 0:
        return 0, 0.0
    shifted = row - row.max()
    exp = np.exp(shifted)
    total = float(exp.sum()) or 1.0
    index = int(exp.argmax())
    return index, float(exp[index] / total)


def _cosine(left, right) -> float:
    import numpy as np

    a = np.asarray(left, dtype=np.float32).reshape(-1)
    b = np.asarray(right, dtype=np.float32).reshape(-1)
    if a.size == 0 or a.size != b.size:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom <= 1e-8:
        return 0.0
    return float(np.dot(a, b) / denom)


def _align_face(image, landmarks):
    """Warp a face onto the 112x112 SFace template. ``None`` if it fails."""
    import numpy as np

    if landmarks is None or len(landmarks) < 5:
        return None
    source = np.asarray(landmarks[:5], dtype=np.float64)
    dest = np.asarray(_SFACE_TEMPLATE, dtype=np.float64)
    src_mean = source.mean(axis=0)
    dst_mean = dest.mean(axis=0)
    src_c = source - src_mean
    dst_c = dest - dst_mean
    cov = (dst_c.T @ src_c) / float(source.shape[0])
    try:
        u_mat, singular, vt_mat = np.linalg.svd(cov)
    except np.linalg.LinAlgError:
        return None
    sign = np.ones(2, dtype=np.float64)
    if np.linalg.det(u_mat) * np.linalg.det(vt_mat) < 0:
        sign[-1] = -1.0
    rotation = u_mat @ np.diag(sign) @ vt_mat
    variance = float((src_c ** 2).sum() / source.shape[0])
    if variance <= 1e-8:
        return None
    scale = float((singular * sign).sum() / variance)
    matrix = np.zeros((2, 3), dtype=np.float64)
    matrix[:, :2] = scale * rotation
    matrix[:, 2] = dst_mean - matrix[:, :2] @ src_mean
    linear = matrix[:, :2]
    try:
        inverse = np.linalg.inv(linear)
    except np.linalg.LinAlgError:
        return None
    out_h, out_w = 112, 112
    ys, xs = np.mgrid[0:out_h, 0:out_w]
    dest_xy = np.stack([xs.reshape(-1), ys.reshape(-1)], axis=1).astype(np.float64)
    src_xy = (dest_xy - matrix[:, 2]) @ inverse.T
    src_x = np.rint(src_xy[:, 0]).astype(np.int32)
    src_y = np.rint(src_xy[:, 1]).astype(np.int32)
    height, width = image.shape[:2]
    valid = (src_x >= 0) & (src_x < width) & (src_y >= 0) & (src_y < height)
    canvas = np.zeros((out_h, out_w, image.shape[2]), dtype=image.dtype)
    flat = canvas.reshape(-1, image.shape[2])
    flat[valid] = image[src_y[valid], src_x[valid]]
    return canvas


def _decode_yunet(outputs: dict, net_w: int, net_h: int, score_threshold: float, nms_threshold: float):
    import numpy as np

    strides = (8, 16, 32)
    faces = []
    for stride in strides:
        cls = outputs.get("cls_%d" % stride)
        obj = outputs.get("obj_%d" % stride)
        bbox = outputs.get("bbox_%d" % stride)
        kps = outputs.get("kps_%d" % stride)
        if cls is None or obj is None or bbox is None:
            continue
        cls_v = np.asarray(cls, dtype=np.float32).reshape(-1)
        obj_v = np.asarray(obj, dtype=np.float32).reshape(-1)
        box_v = np.asarray(bbox, dtype=np.float32).reshape(-1, 4)
        kps_v = None if kps is None else np.asarray(kps, dtype=np.float32).reshape(-1, 10)
        cols = max(1, int(net_w) // stride)
        rows = max(1, int(net_h) // stride)
        count = min(cls_v.size, obj_v.size, box_v.shape[0], rows * cols)
        for index in range(count):
            cls_score = float(min(1.0, max(0.0, cls_v[index])))
            obj_score = float(min(1.0, max(0.0, obj_v[index])))
            score = (cls_score * obj_score) ** 0.5
            if score < score_threshold:
                continue
            row = index // cols
            col = index % cols
            cx = (col + float(box_v[index, 0])) * stride
            cy = (row + float(box_v[index, 1])) * stride
            bw = float(np.exp(box_v[index, 2])) * stride
            bh = float(np.exp(box_v[index, 3])) * stride
            x1 = cx - bw / 2.0
            y1 = cy - bh / 2.0
            landmarks = None
            if kps_v is not None and index < kps_v.shape[0]:
                landmarks = []
                for point in range(5):
                    lx = (float(kps_v[index, point * 2]) + col) * stride
                    ly = (float(kps_v[index, point * 2 + 1]) + row) * stride
                    landmarks.append((lx, ly))
            faces.append((x1, y1, bw, bh, score, landmarks))
    if not faces:
        return None
    faces.sort(key=lambda item: item[4], reverse=True)
    kept = []
    for face in faces:
        if len(kept) >= 20:
            break
        ax1, ay1, aw, ah = face[0], face[1], face[2], face[3]
        ax2, ay2 = ax1 + aw, ay1 + ah
        overlaps = False
        for other in kept:
            bx1, by1, bw, bh = other[0], other[1], other[2], other[3]
            bx2, by2 = bx1 + bw, by1 + bh
            ix1, iy1 = max(ax1, bx1), max(ay1, by1)
            ix2, iy2 = min(ax2, bx2), min(ay2, by2)
            iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
            inter = iw * ih
            union = aw * ah + bw * bh - inter
            if union > 0.0 and inter / union > nms_threshold:
                overlaps = True
                break
        if not overlaps:
            kept.append(face)
    return kept[0] if kept else None


def _load_gallery(path: str):
    """Return ``(vectors, threshold)`` from the local object database."""
    import sqlite3

    import numpy as np

    vectors = []
    threshold = None
    if not path or not os.path.isfile(path):
        return vectors, threshold
    connection = sqlite3.connect(path)
    try:
        try:
            rows = connection.execute(
                "SELECT name, embedding, dim FROM faceIdentities"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        for name, blob, dim in rows:
            if not blob:
                continue
            vector = np.frombuffer(bytes(blob), dtype=np.float32)
            width = int(dim or 0)
            if width > 0 and vector.size >= width:
                vector = vector[:width].copy()
            if vector.size == 0:
                continue
            vectors.append((str(name), vector))
        try:
            row = connection.execute(
                "SELECT value FROM attributeSettings WHERE key='face_threshold'"
            ).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0] not in (None, ""):
            try:
                threshold = float(row[0])
            except (TypeError, ValueError):
                threshold = None
    finally:
        connection.close()
    return vectors, threshold


class AttributePipeline:
    """Optional plate, face, and carrier models applied to detector crops."""

    def __init__(
        self,
        plate_detector: Optional[OnnxGraph] = None,
        plate_ocr: Optional[OnnxGraph] = None,
        face_detector: Optional[OnnxGraph] = None,
        face_embed: Optional[OnnxGraph] = None,
        carrier: Optional[OnnxGraph] = None,
        gallery: Optional[Sequence[tuple]] = None,
        threshold: Optional[float] = None,
        gallery_db: Optional[str] = None,
        use_heuristic: Optional[bool] = None,
        logger: Any = None,
    ) -> None:
        self.plate_detector = plate_detector
        self.plate_ocr = plate_ocr
        self.face_detector = face_detector
        self.face_embed = face_embed
        self.carrier = carrier
        self._gallery = list(gallery or [])
        self._threshold_override = threshold
        self._gallery_db = gallery_db if gallery_db is not None else os.environ.get(_ENV_GALLERY)
        self._db_mtime = None
        self._db_gallery: list = []
        self._db_threshold = None
        if use_heuristic is None:
            use_heuristic = os.environ.get(_ENV_HEURISTIC, "1") != "0"
        self.use_heuristic = bool(use_heuristic) and carrier is None
        self._logger = logger
        self._enable_gpu = False
        self._user_carrier_path: Optional[str] = None
        self._user_carrier_mtime: Optional[float] = None
        self._user_carrier_checked = 0.0

    def watch_user_carrier_model(self, path, enable_gpu: bool = False) -> None:
        """Hot-load ``path`` (the in-app "Retrain now" output) when it changes."""
        self._user_carrier_path = path or None
        self._user_carrier_mtime = None
        self._user_carrier_checked = 0.0
        self._enable_gpu = bool(enable_gpu)
        self._maybe_reload_user_carrier(force=True)

    def _maybe_reload_user_carrier(self, force: bool = False) -> None:
        path = self._user_carrier_path
        if callable(path):  # e.g. community.active_carrier_model_path
            try:
                path = path()
            except Exception:
                return
        if not path:
            return
        now = time.monotonic()
        if not force and now - self._user_carrier_checked < 15.0:
            return
        self._user_carrier_checked = now
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if (path, mtime) == getattr(self, "_user_carrier_key", None):
            return
        try:
            graph = OnnxGraph(path, enable_gpu=self._enable_gpu)
        except Exception as exc:
            if self._logger is not None and hasattr(self._logger, "error"):
                self._logger.error("user carrier model %s failed to load: %s" % (path, exc))
            self._user_carrier_mtime = mtime
            self._user_carrier_key = (path, mtime)
            return
        self.carrier = graph
        self.use_heuristic = False
        self._user_carrier_mtime = mtime
        self._user_carrier_key = (path, mtime)
        if self._logger is not None and hasattr(self._logger, "info"):
            self._logger.info("loaded user-trained carrier model %s" % path)

    @classmethod
    def from_environment(cls, enable_gpu: bool = False, logger: Any = None) -> "AttributePipeline":
        def _graph(env_name):
            path = resolve_optional_model(env_name, _DEFAULT_FILES[env_name])
            if not path:
                return None
            try:
                return OnnxGraph(path, enable_gpu=enable_gpu)
            except Exception as exc:
                if logger is not None and hasattr(logger, "error"):
                    logger.error("attribute model %s failed to load: %s" % (path, exc))
                return None

        pipeline = cls(
            plate_detector=_graph(_ENV_PLATE_DETECTOR),
            plate_ocr=_graph(_ENV_PLATE_OCR),
            face_detector=_graph(_ENV_FACE_DETECTOR),
            face_embed=_graph(_ENV_FACE_EMBED),
            carrier=_graph(_ENV_CARRIER),
            logger=logger,
        )
        if _ENV_CARRIER not in os.environ:
            from backEnd.community import active_carrier_model_path

            pipeline.watch_user_carrier_model(active_carrier_model_path, enable_gpu=enable_gpu)
        return pipeline

    def set_gallery_db(self, path: Optional[str]) -> None:
        self._gallery_db = path or None
        self._db_mtime = None
        self._db_gallery = []
        self._db_threshold = None

    def annotate(self, rgb: bytes, width: int, height: int, detections) -> None:
        if not detections:
            return
        try:
            frame = _as_image(rgb, width, height)
        except Exception:
            return
        for detection in detections:
            try:
                self._annotate_one(frame, detection)
            except Exception as exc:
                if self._logger is not None and hasattr(self._logger, "error"):
                    self._logger.error("attribute recognizer failed: %s" % exc)

    def _annotate_one(self, frame, detection) -> None:
        box = detection.bounding_box
        crop = _crop_image(frame, box.x, box.y, box.x + box.width, box.y + box.height)
        kind = str(getattr(detection, "type", "") or "")
        found = {}
        if kind == "vehicle":
            found.update(self._read_plate(crop))
            if str(detection.attributes.get("vehicle_class") or "") == "bus":
                found.update(self._read_bus(crop, detection))
            else:
                found.update(self._read_carrier(crop))
        elif kind == "person":
            found.update(self._read_face(crop))
        if found:
            detection.attributes.update(found)

    def embed_image(self, image):
        """Embedding for an enrollment image, or ``None`` when no face is usable."""
        if self.face_embed is None:
            return None
        crop = image
        landmarks = None
        if self.face_detector is not None:
            found = self._detect_face(image)
            if found is None:
                return None
            x1, y1, x2, y2, _score, landmarks = found
            crop = _crop_image(image, x1, y1, x2, y2)
        return self._embed(crop, landmarks)

    def _read_plate(self, crop) -> dict:
        if self.plate_ocr is None and self.plate_detector is None:
            return {}
        plate_image = crop
        score = 0.5
        if self.plate_detector is not None:
            found = self._detect_box(self.plate_detector, crop, "plate")
            if found is None:
                return {}
            x1, y1, x2, y2, score = found
            plate_image = _crop_image(crop, x1, y1, x2, y2)
        elif crop.shape[0] >= 8:
            # Plates sit low on a vehicle when no detector weight is configured.
            top = int(crop.shape[0] * 0.55)
            plate_image = crop[top:, :, :]
        if self.plate_ocr is None:
            return {}
        text, confidence = self._ocr(plate_image)
        if not text:
            return {}
        if self.plate_detector is not None:
            confidence = float(score)
        return {PLATE_TEXT: text, PLATE_CONFIDENCE: float(confidence)}

    def _ocr(self, image) -> tuple[str, float]:
        graph = self.plate_ocr
        if graph is None:
            return "", 0.0
        values, _resized = graph.run_image(image)
        outputs = _output_map(graph, values)
        layout = graph.layout
        if layout == "strip-ocr" or "chars" in outputs:
            text = _decode_strip(outputs.get("chars", values[0]))
            return text, 0.9 if text else 0.0
        alphabet = str(graph.info.get("alphabet") or _FAST_PLATE_ALPHABET)
        pad = str(graph.info.get("pad_char") or "_")
        tensor = outputs.get(graph.output_names[-1], values[-1])
        return _decode_fast_plate(tensor, alphabet, pad)

    def _detect_box(self, graph: OnnxGraph, image, output_name: str):
        if graph.layout == "yunet":
            return self._detect_yunet_box(graph, image)
        values, _resized = graph.run_image(image)
        outputs = _output_map(graph, values)
        tensor = outputs.get(output_name, values[0])
        if not graph.width or not graph.height:
            return None
        return _box_from_network(
            tensor, int(graph.width), int(graph.height), image.shape[1], image.shape[0]
        )

    def _detect_yunet_box(self, graph: OnnxGraph, image):
        values, _resized = graph.run_image(image, scale=1.0)
        outputs = _output_map(graph, values)
        score = float(graph.info.get("score_threshold") or 0.6)
        nms = float(graph.info.get("nms_threshold") or 0.3)
        face = _decode_yunet(outputs, int(graph.width), int(graph.height), score, nms)
        if face is None:
            return None
        x1, y1, bw, bh, det_score, landmarks = face
        sx = float(image.shape[1]) / float(graph.width)
        sy = float(image.shape[0]) / float(graph.height)
        left = int(round(x1 * sx))
        top = int(round(y1 * sy))
        right = int(round((x1 + bw) * sx))
        bottom = int(round((y1 + bh) * sy))
        mapped = None
        if landmarks:
            mapped = [(px * sx, py * sy) for px, py in landmarks]
        return left, top, right, bottom, det_score, mapped

    def _detect_face(self, image):
        graph = self.face_detector
        if graph is None:
            return None
        if graph.layout == "yunet":
            return self._detect_yunet_box(graph, image)
        found = self._detect_box(graph, image, "face")
        if found is None:
            return None
        x1, y1, x2, y2, score = found
        return x1, y1, x2, y2, score, None

    def _read_face(self, crop) -> dict:
        if self.face_embed is None:
            return {}
        landmarks = None
        face_image = crop
        if self.face_detector is not None:
            found = self._detect_face(crop)
            if found is None:
                return {}
            x1, y1, x2, y2, _score, landmarks = found
            face_image = _crop_image(crop, x1, y1, x2, y2)
        embedding = self._embed(face_image, landmarks)
        if embedding is None:
            return {}
        name, score = self._match(embedding)
        if not name:
            return {}
        return {FACE_IDENTITY: name, FACE_CONFIDENCE: float(score)}

    def _embed(self, image, landmarks):
        graph = self.face_embed
        if graph is None:
            return None
        import numpy as np

        prepared = image
        if graph.layout == "sface" and landmarks:
            aligned = _align_face(image, landmarks)
            if aligned is not None:
                prepared = aligned
        if graph.layout == "sface":
            values, _resized = graph.run_image(prepared, scale=1.0)
        else:
            values, _resized = graph.run_image(prepared)
        outputs = _output_map(graph, values)
        tensor = outputs.get("embedding", values[-1])
        vector = np.asarray(tensor, dtype=np.float32).reshape(-1).copy()
        if vector.size == 0:
            return None
        return vector

    def _match(self, embedding) -> tuple[str, float]:
        gallery = self._gallery_vectors()
        if not gallery:
            return "", 0.0
        threshold = self._threshold()
        best_name = ""
        best_score = -1.0
        for name, vector in gallery:
            score = _cosine(embedding, vector)
            if score > best_score:
                best_score = score
                best_name = name
        if best_name and best_score >= threshold:
            return best_name, best_score
        return "", 0.0

    def _threshold(self) -> float:
        if self._threshold_override is not None:
            return float(self._threshold_override)
        env = os.environ.get(_ENV_THRESHOLD)
        if env:
            try:
                return float(env)
            except ValueError:
                pass
        self._gallery_vectors()
        if self._db_threshold is not None:
            return float(self._db_threshold)
        return 0.4

    def _gallery_vectors(self):
        path = self._gallery_db
        if not path:
            return list(self._gallery)
        try:
            mtime = os.path.getmtime(path) if os.path.isfile(path) else None
        except OSError:
            mtime = None
        if mtime != self._db_mtime:
            vectors, threshold = _load_gallery(path)
            self._db_gallery = vectors
            self._db_threshold = threshold
            self._db_mtime = mtime
        return list(self._db_gallery)

    def _read_bus(self, crop, detection) -> dict:
        """COCO said bus: label Bus, or School bus when the body is school-bus yellow.

        A trained model that answers "School bus" also counts.
        """
        score = float(getattr(detection, "score", 0.5) or 0.5)
        if is_school_bus_yellow(crop):
            return {DELIVERY_CARRIER: "School bus", DELIVERY_CONFIDENCE: score}
        self._maybe_reload_user_carrier()
        if self.carrier is not None:
            try:
                label, confidence = self._carrier_model(crop)
            except Exception:
                label, confidence = "none", 0.0
            if label == "School bus":
                return {DELIVERY_CARRIER: label, DELIVERY_CONFIDENCE: float(confidence)}
        return {DELIVERY_CARRIER: "Bus", DELIVERY_CONFIDENCE: score}

    def _read_carrier(self, crop) -> dict:
        self._maybe_reload_user_carrier()
        if self.carrier is not None:
            label, confidence = self._carrier_model(crop)
        elif self.use_heuristic:
            guessed = heuristic_carrier(crop)
            if guessed is None:
                return {}
            label, confidence = guessed
        else:
            return {}
        if not label or str(label).lower() == "none":
            return {}
        return {DELIVERY_CARRIER: label, DELIVERY_CONFIDENCE: float(confidence)}

    def _carrier_model(self, image) -> tuple[str, float]:
        graph = self.carrier
        if graph is None:
            return "none", 0.0
        if graph.layout == "carrier-linear" or graph.input_name == "features":
            import numpy as np

            prepared = _resize(image, 64, 64)
            features = image_features(prepared)
            values = graph.run(features.astype(np.float32))
        else:
            values, _resized = graph.run_image(image)
        outputs = _output_map(graph, values)
        logits = outputs.get("logits", values[-1])
        index, confidence = _softmax_choice(logits)
        labels = graph.info.get("labels") or list(CARRIER_LABELS)
        if index < 0 or index >= len(labels):
            return "none", 0.0
        return str(labels[index]), confidence


def enroll_image(pipeline: AttributePipeline, image, name: str, data_manager) -> None:
    """Store one face embedding in the local gallery."""
    vector = pipeline.embed_image(image)
    if vector is None:
        raise RuntimeError(
            "No face embedding was produced. Download the face models "
            "(scripts/fetch_attribute_models.py) or use a clearer image."
        )
    data_manager.enrollFace(name, vector.tobytes(), int(vector.shape[0]))


def enroll_image_file(path: str, name: str, data_manager, enable_gpu: bool = False) -> None:
    from PIL import Image

    image = Image.open(path).convert("RGB")
    pipeline = AttributePipeline.from_environment(enable_gpu=enable_gpu)
    import numpy as np

    array = np.asarray(image)
    enroll_image(pipeline, array, name, data_manager)


def enroll_track(data_manager, obj_id: int, name: str, enable_gpu: bool = False) -> None:
    """Enroll the first stored box of a tracked person."""
    import numpy as np

    row = data_manager._cur.execute(
        "SELECT camLoc, timeStart FROM objects WHERE uid=?", (int(obj_id),)
    ).fetchone()
    if row is None:
        raise ValueError("object %s was not found" % obj_id)
    camera, started = row
    motion = data_manager._cur.execute(
        "SELECT x1, y1, x2, y2 FROM motion WHERE objUid=? ORDER BY time ASC LIMIT 1",
        (int(obj_id),),
    ).fetchone()
    if motion is None:
        raise ValueError("object %s has no boxes" % obj_id)
    frame = data_manager.getSingleFrame(camera, int(started), (0, 0), True, True)
    image = frame[0] if frame else None
    if image is None:
        raise RuntimeError("could not read a frame for object %s" % obj_id)
    proc = frame[2] if len(frame) > 2 else None
    array = np.asarray(image.convert("RGB"))
    x1, y1, x2, y2 = [int(value) for value in motion]
    if proc and proc[0] and proc[1] and array.shape[1] and array.shape[0]:
        x1 = int(round(x1 * array.shape[1] / float(proc[0])))
        x2 = int(round(x2 * array.shape[1] / float(proc[0])))
        y1 = int(round(y1 * array.shape[0] / float(proc[1])))
        y2 = int(round(y2 * array.shape[0] / float(proc[1])))
    crop = _crop_image(array, x1, y1, x2, y2)
    pipeline = AttributePipeline.from_environment(enable_gpu=enable_gpu)
    enroll_image(pipeline, crop, name, data_manager)
