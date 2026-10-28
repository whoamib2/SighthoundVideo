"""Object-detector interface and the local RGB HTTP contract.

The on-box detector speaks a small HTTP API that this module owns:

* ``POST /`` with a raw RGB body (``width * height * 3`` bytes, row-major,
  no padding).
* Request headers ``X-Width`` and ``X-Height`` (and ``Content-Length``).
* JSON reply ``{"objects": [ ... ]}``.

Each object has ``type``, ``boundingBox`` (``x``, ``y``, ``width``,
``height``) and ``score``. Extra recognition results are carried in
``attributes`` and, for a few well-known keys, also copied to the top level
so older clients keep working while newer ones can read them directly:

* ``plate_text`` — license-plate text
* ``face_identity`` — a face identity label
* ``delivery_carrier`` — one of USPS, FedEx, UPS, Amazon

Phase 1 does not run those recognizers. The fields are parsed and preserved
so a later ONNX Runtime model can fill them in without changing the wire
format. The historical implementation is the closed ``SIOWrapper`` binary;
``SIOObjectDetector`` is the only adapter that imports it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Protocol, Sequence

from backEnd.native.errors import NativeDependencyError

HEADER_WIDTH = "X-Width"
HEADER_HEIGHT = "X-Height"
HEADER_CONTENT_LENGTH = "Content-Length"

ATTR_PLATE_TEXT = "plate_text"
ATTR_FACE_IDENTITY = "face_identity"
ATTR_DELIVERY_CARRIER = "delivery_carrier"

# Stable vocabulary for ``delivery_carrier``. Not detected in this phase.
DELIVERY_CARRIERS = ("USPS", "FedEx", "UPS", "Amazon")

RESERVED_ATTRIBUTE_KEYS = (
    ATTR_PLATE_TEXT,
    ATTR_FACE_IDENTITY,
    ATTR_DELIVERY_CARRIER,
)


@dataclass
class BoundingBox:
    """Axis-aligned box in image pixels, origin at the top left."""

    x: int
    y: int
    width: int
    height: int

    def to_json(self) -> dict[str, int]:
        return {
            "x": int(self.x),
            "y": int(self.y),
            "width": int(self.width),
            "height": int(self.height),
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "BoundingBox":
        return cls(
            x=int(value.get("x", 0) or 0),
            y=int(value.get("y", 0) or 0),
            width=int(value.get("width", 0) or 0),
            height=int(value.get("height", 0) or 0),
        )


@dataclass
class Detection:
    """One detected object, plus room for recognizer attributes."""

    type: str
    bounding_box: BoundingBox
    score: float = 0.0
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_json_object(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "boundingBox": self.bounding_box.to_json(),
            "score": float(self.score),
        }
        if self.attributes:
            payload["attributes"] = dict(self.attributes)
            for key in RESERVED_ATTRIBUTE_KEYS:
                if key in self.attributes:
                    payload[key] = self.attributes[key]
        return payload

    @classmethod
    def from_json_object(cls, value: Mapping[str, Any]) -> "Detection":
        attributes = dict(value.get("attributes") or {})
        for key in RESERVED_ATTRIBUTE_KEYS:
            if key in value and key not in attributes:
                attributes[key] = value[key]
        box = value.get("boundingBox") or {}
        score = value.get("score", 0) or 0
        return cls(
            type=str(value.get("type", "unknown")),
            bounding_box=BoundingBox.from_json(box),
            score=float(score),
            attributes=attributes,
        )


class TrackMatch(tuple):
    """A tracker/detector association that still unpacks as four values.

    Existing call sites do ``obj, type, score, overlap = match``. Attributes
    from the detector ride along without changing that unpacking.
    """

    attributes: dict[str, Any]

    def __new__(
        cls,
        obj: Any,
        det_type: str,
        score: float,
        overlap: float,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> "TrackMatch":
        return tuple.__new__(cls, (obj, det_type, score, overlap))

    def __init__(
        self,
        obj: Any,
        det_type: str,
        score: float,
        overlap: float,
        attributes: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.attributes = dict(attributes or {})


class ObjectDetector(Protocol):
    """Something that classifies a raw RGB frame."""

    def detect_rgb(self, rgb: bytes, width: int, height: int) -> list[Detection]:
        """Return detections for an 8-bit RGB buffer of ``width * height * 3`` bytes."""


def rgb_byte_count(width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid frame size {width}x{height}")
    return int(width) * int(height) * 3


def parse_rgb_body(body: bytes, width: int, height: int) -> bytes:
    """Validate a raw RGB POST body and return exactly one frame.

    Longer bodies are truncated to the declared frame. Shorter bodies are
    rejected so a detector never reads off the end of the buffer.
    """
    if not isinstance(body, (bytes, bytearray, memoryview)):
        raise TypeError("RGB body must be bytes")
    expected = rgb_byte_count(width, height)
    raw = bytes(body)
    if len(raw) < expected:
        raise ValueError(
            f"RGB body is {len(raw)} bytes; {width}x{height} needs {expected}"
        )
    return raw[:expected]


def parse_detection_response(payload: bytes | str) -> list[Detection]:
    """Parse the JSON object list returned by the local detector."""
    if isinstance(payload, bytes):
        data = json.loads(payload.decode("utf-8"))
    else:
        data = json.loads(payload)
    objects = data.get("objects") or []
    return [Detection.from_json_object(item) for item in objects]


def encode_detection_response(detections: Sequence[Detection]) -> bytes:
    """Serialize detections as the JSON body of the local detector."""
    payload = {"objects": [det.to_json_object() for det in detections]}
    return json.dumps(payload).encode("utf-8")


class SIOObjectDetector:
    """Adapter over the closed ``SIOWrapper`` extension.

    ``SIOWrapper`` is a Python 2.7 binary. This class is the only module that
    imports it. ``detect_rgb`` speaks the same logical contract as the HTTP
    service: raw RGB in, :class:`Detection` objects out, including any extra
    attributes the binary (or a future model) already places on each object.
    """

    def __init__(self, logger: Any, model_dir: str, enable_gpu: bool = False) -> None:
        try:
            from SIOWrapper.SIOWrapper import SIOWrapper
        except Exception as exc:
            raise NativeDependencyError("SIOWrapper", str(exc)) from exc
        self._logger = logger
        self._sio = SIOWrapper(logger, model_dir, enable_gpu)
        if self._sio.init() <= 0:
            raise NativeDependencyError("SIOWrapper", "init failed")

    def detect_rgb(self, rgb: bytes, width: int, height: int) -> list[Detection]:
        frame = parse_rgb_body(rgb, width, height)
        id_in = self._sio.addInput(frame, int(width), int(height))
        if id_in < 0:
            raise RuntimeError("SIOWrapper rejected the input frame")
        id_out, detections = self._sio.getOutput(True)
        if id_out != id_in:
            raise RuntimeError("SIOWrapper returned a mismatched result id")
        if isinstance(detections, (bytes, str)):
            return parse_detection_response(detections)
        objects = []
        if isinstance(detections, Mapping):
            objects = detections.get("objects") or []
        return [Detection.from_json_object(item) for item in objects]
