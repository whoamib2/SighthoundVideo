"""Adapters for the closed native components.

Import capture, tracking and detection through this package instead of
``videoLib2``, ``svsentry`` or ``SIOWrapper``.
"""

from backEnd.native.capture import (
    ClipReader,
    FFmpegLog,
    StreamReader,
    native_capture_available,
)
from backEnd.native.detector import (
    ATTR_DELIVERY_CARRIER,
    ATTR_FACE_IDENTITY,
    ATTR_PLATE_TEXT,
    DELIVERY_CARRIERS,
    Detection,
    ObjectDetector,
    SIOObjectDetector,
)
from backEnd.native.errors import NativeDependencyError
from backEnd.native.tracking import (
    native_tracking_available,
    object_collector_base,
    pipeline_base,
)

__all__ = [
    "ATTR_DELIVERY_CARRIER",
    "ATTR_FACE_IDENTITY",
    "ATTR_PLATE_TEXT",
    "ClipReader",
    "DELIVERY_CARRIERS",
    "Detection",
    "FFmpegLog",
    "NativeDependencyError",
    "ObjectDetector",
    "SIOObjectDetector",
    "StreamReader",
    "native_capture_available",
    "native_tracking_available",
    "object_collector_base",
    "pipeline_base",
]
