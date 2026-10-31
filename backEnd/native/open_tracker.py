"""Motion gate and a ByteTrack-style multi-object tracker.

The tracker replaces the closed ``svsentry`` pipeline. It calls the same
object-collector methods ``QueuedDataManagerCloud`` already implements:

* ``addObject(timeStart, objType)``
* ``addFrame(objId, frameId, time, bbox, objType)`` with an OpenCV box
  ``(x1, y1, x2, y2)`` whose bottom-right corner is exclusive
* ``frameCompleted(msTimestamp, frameNum)``

Association is greedy IoU. High-score blobs match first, then low-score
blobs recover tracks that the motion gate almost lost. A constant-velocity
prediction shifts a track before that match, which is the small piece of
OC-SORT that matters for a blob moving between frames. Tracks are reported
as ``unknown``; the detector assigns person, vehicle, or animal.

Event filtering follows the ideas in Frigate (github.com/blakeblackshear/
frigate, MIT): motion threshold, contour min/max area relative to the
frame, a motion mask (here the burned-in timestamp bands), a lightning /
global-change guard, a slowly adapting background, a track that must be
seen for several frames before it becomes an object, and stationary
objects that stop producing frames. No Frigate code is copied.
"""

from __future__ import annotations

from typing import Any, Optional

from backEnd.native.errors import NativeDependencyError

_MOTION_THRESHOLD = 30          # Frigate motion.threshold default
_MIN_AREA = 80
_MIN_AREA_FRACTION = 0.0015     # min_area relative to the frame
_MAX_AREA_FRACTION = 0.40       # max_area: bigger blobs are light changes
_GLOBAL_CHANGE_FRACTION = 0.30  # Frigate lightning_threshold idea
_MASK_TOP_FRACTION = 0.08       # timestamp / camera-name overlay bands
_MASK_BOTTOM_FRACTION = 0.05
_BG_ALPHA_STILL = 0.10
_BG_ALPHA_FOREGROUND = 0.02     # parked cars and ghosts melt away
_CONFIRM_HITS = 5               # seen in several frames ...
_CONFIRM_MS = 500               # ... for most of a second ...
_CONFIRM_TRAVEL_FRACTION = 0.04 # ... and actually crossing the frame
_STATIONARY_MS = 10000
_STATIONARY_TRAVEL_FRACTION = 0.02
_HIGH_SCORE = 0.5
_LOW_SCORE = 0.1
_IOU_MATCH = 0.3
_MAX_AGE = 12
_DOWNSAMPLE = 4


def _numpy():
    try:
        import numpy as np
    except Exception as exc:
        raise NativeDependencyError("numpy", str(exc)) from exc
    return np


def frame_rgb_array(frame: Any):
    """Return ``height x width x 3`` uint8 RGB for a pipeline frame."""
    np = _numpy()
    rgb = getattr(frame, "rgb", None)
    width = int(frame.width)
    height = int(frame.height)
    expected = width * height * 3
    if isinstance(rgb, (bytes, bytearray, memoryview)) and len(rgb) >= expected:
        array = np.frombuffer(bytes(rgb[:expected]), dtype=np.uint8)
        return array.reshape((height, width, 3))
    from vitaToolbox.image.ImageConversion import _rgb_bytes_from_frame

    raw = _rgb_bytes_from_frame(frame)
    array = np.frombuffer(raw, dtype=np.uint8)
    return array.reshape((height, width, 3))


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return float(inter) / float(union)


def _intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return _iou(a, b) > 0.0 or (
        a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
    )


def _downsample_mask(mask, factor: int):
    np = _numpy()
    height, width = mask.shape
    height2 = height // factor
    width2 = width // factor
    if height2 < 1 or width2 < 1:
        return mask
    cropped = mask[: height2 * factor, : width2 * factor]
    view = cropped.reshape(height2, factor, width2, factor)
    return view.max(axis=(1, 3)).astype(bool)


def _components(mask, min_area: int) -> list[tuple[int, int, int, int, float]]:
    """Connected components as exclusive boxes plus a 0..1 score."""
    np = _numpy()
    height, width = mask.shape
    if height == 0 or width == 0 or not mask.any():
        return []
    small = _downsample_mask(mask, _DOWNSAMPLE)
    seen = np.zeros(small.shape, dtype=np.uint8)
    sh, sw = small.shape
    boxes = []
    ys, xs = np.where(small)
    for y0, x0 in zip(ys.tolist(), xs.tolist()):
        if seen[y0, x0]:
            continue
        stack = [(y0, x0)]
        seen[y0, x0] = 1
        min_x = max_x = x0
        min_y = max_y = y0
        area = 0
        while stack:
            y, x = stack.pop()
            area += 1
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if ny < 0 or nx < 0 or ny >= sh or nx >= sw:
                    continue
                if seen[ny, nx] or not small[ny, nx]:
                    continue
                seen[ny, nx] = 1
                stack.append((ny, nx))
        full_area = area * _DOWNSAMPLE * _DOWNSAMPLE
        if full_area < min_area:
            continue
        x1 = min_x * _DOWNSAMPLE
        y1 = min_y * _DOWNSAMPLE
        x2 = min(width, (max_x + 1) * _DOWNSAMPLE)
        y2 = min(height, (max_y + 1) * _DOWNSAMPLE)
        sub = mask[y1:y2, x1:x2]
        pixels = np.where(sub)
        if len(pixels[0]) == 0:
            continue
        ry1 = y1 + int(pixels[0].min())
        ry2 = y1 + int(pixels[0].max()) + 1
        rx1 = x1 + int(pixels[1].min())
        rx2 = x1 + int(pixels[1].max()) + 1
        if (rx2 - rx1) * (ry2 - ry1) < min_area:
            continue
        score = min(1.0, full_area / float(max(min_area * 8, 1)))
        boxes.append((rx1, ry1, rx2, ry2, score))
    return boxes


def _center(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)


def _dist(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _greedy_match(tracks: list[tuple[int, int, int, int]], dets: list[tuple[int, int, int, int]], min_iou: float):
    pairs = []
    for ti, track_box in enumerate(tracks):
        for di, det_box in enumerate(dets):
            overlap = _iou(track_box, det_box)
            if overlap >= min_iou:
                pairs.append((overlap, ti, di))
    pairs.sort(reverse=True)
    used_t = set()
    used_d = set()
    matches = []
    for _overlap, ti, di in pairs:
        if ti in used_t or di in used_d:
            continue
        used_t.add(ti)
        used_d.add(di)
        matches.append((ti, di))
    return matches, used_t, used_d


class _Track:
    def __init__(self, bbox: tuple[int, int, int, int], score: float) -> None:
        self.bbox = bbox
        self.score = score
        self.vx = 0.0
        self.vy = 0.0
        self.hits = 1
        self.time_since_update = 0
        self.sentry_id: Optional[int] = None
        self.first_ms: Optional[int] = None
        self.first_center = _center(bbox)
        self.max_travel = 0.0
        self.confirmed = False
        self.anchor = _center(bbox)
        self.anchor_ms: Optional[int] = None
        self.stationary = False

    def predict(self) -> tuple[int, int, int, int]:
        dx = int(round(self.vx))
        dy = int(round(self.vy))
        return (
            self.bbox[0] + dx,
            self.bbox[1] + dy,
            self.bbox[2] + dx,
            self.bbox[3] + dy,
        )

    def update(self, bbox: tuple[int, int, int, int], score: float) -> None:
        self.vx = 0.5 * self.vx + 0.5 * (bbox[0] - self.bbox[0])
        self.vy = 0.5 * self.vy + 0.5 * (bbox[1] - self.bbox[1])
        self.bbox = bbox
        self.score = score
        self.hits += 1
        self.time_since_update = 0


class OpenVideoPipeline:
    """Collector-facing tracker used as ``VideoPipeline``'s base class."""

    def __init__(self, objectCollector: Any, pipelineConfigFile: str = "pipeline.cfg") -> None:
        self.objectCollector = objectCollector
        self.pipelineConfigFile = pipelineConfigFile
        self._tracks: list[_Track] = []
        self._frame_index = 0
        self._last_ms: Optional[int] = None
        self._prev_gray = None
        self._background = None
        self._seen_frame = False
        self._width = 0
        self._height = 0

    def processClipFrame(self, frame: Any, ms: int) -> None:
        np = _numpy()
        rgb = frame_rgb_array(frame)
        gray = rgb.mean(axis=2).astype(np.uint8)
        self._height, self._width = gray.shape
        timestamp = int(ms)
        detections = self._gate(gray)
        self._associate(detections, timestamp)
        self._prev_gray = gray
        self._last_ms = timestamp
        self._seen_frame = True
        if self.objectCollector is not None:
            self.objectCollector.frameCompleted(timestamp, self._frame_index)
        self._frame_index += 1

    def flush(self) -> None:
        """Drop tracks and complete one extra timestamp so lost tracks decide."""
        if self._last_ms is None or self.objectCollector is None:
            self._tracks.clear()
            return
        self._tracks.clear()
        self.objectCollector.frameCompleted(int(self._last_ms) + 1, self._frame_index)
        self._frame_index += 1

    def _gate(self, gray) -> list[tuple[int, int, int, int, float]]:
        np = _numpy()
        if self._background is None:
            self._background = gray.copy()
            return []
        if self._prev_gray is None:
            self._prev_gray = gray
            return []
        if self._prev_gray.shape != gray.shape or self._background.shape != gray.shape:
            self._background = gray.copy()
            self._prev_gray = gray
            return []
        motion = np.abs(gray.astype(np.int16) - self._prev_gray.astype(np.int16)) > _MOTION_THRESHOLD
        foreground = np.abs(gray.astype(np.int16) - self._background.astype(np.int16)) > _MOTION_THRESHOLD
        height, width = gray.shape
        top = int(height * _MASK_TOP_FRACTION)
        bottom = height - int(height * _MASK_BOTTOM_FRACTION)
        for m in (motion, foreground):
            m[:top] = False
            m[bottom:] = False
        frame_area = float(width * height)
        # Light switching, IR mode change, clouds: re-learn, no detections.
        if foreground.mean() > _GLOBAL_CHANGE_FRACTION:
            self._background = gray.copy()
            return []
        alpha = np.where(foreground, _BG_ALPHA_FOREGROUND, _BG_ALPHA_STILL).astype(np.float32)
        updated = self._background.astype(np.float32)
        updated += alpha * (gray.astype(np.float32) - updated)
        self._background = np.clip(updated + 0.5, 0, 255).astype(np.uint8)
        min_area = max(_MIN_AREA, int(frame_area * _MIN_AREA_FRACTION))
        max_area = frame_area * _MAX_AREA_FRACTION
        boxes = [b for b in _components(foreground, min_area)
                 if (b[2] - b[0]) * (b[3] - b[1]) <= max_area]
        if not boxes:
            return []
        kept = []
        track_boxes = [track.predict() for track in self._tracks]
        for box in boxes:
            rect = box[:4]
            touches_motion = bool(motion[rect[1] : rect[3], rect[0] : rect[2]].any()) if motion.size else False
            touches_track = any(_intersects(rect, predicted) for predicted in track_boxes)
            if touches_motion or touches_track:
                score = box[4]
                if not touches_motion:
                    score = min(score, _LOW_SCORE + 0.05)
                kept.append((rect[0], rect[1], rect[2], rect[3], score))
        return kept

    def _associate(self, detections: list[tuple[int, int, int, int, float]], timestamp: int) -> None:
        high = [det for det in detections if det[4] >= _HIGH_SCORE]
        low = [det for det in detections if _LOW_SCORE <= det[4] < _HIGH_SCORE]
        predicted = [track.predict() for track in self._tracks]
        matches, used_tracks, used_high = _greedy_match(
            predicted, [det[:4] for det in high], _IOU_MATCH
        )
        for ti, di in matches:
            self._tracks[ti].update(high[di][:4], high[di][4])
        left_tracks = [i for i in range(len(self._tracks)) if i not in used_tracks]
        left_pred = [predicted[i] for i in left_tracks]
        low_matches, used_left, used_low = _greedy_match(
            left_pred, [det[:4] for det in low], _IOU_MATCH
        )
        for li, di in low_matches:
            self._tracks[left_tracks[li]].update(low[di][:4], low[di][4])
        for di, det in enumerate(high):
            if di not in used_high:
                self._tracks.append(_Track(det[:4], det[4]))
        survivors = []
        for index, track in enumerate(self._tracks):
            matched_now = index in used_tracks or (
                index in left_tracks and left_tracks.index(index) in used_left
            )
            if not matched_now:
                track.time_since_update += 1
            if track.time_since_update <= _MAX_AGE:
                survivors.append(track)
        self._tracks = survivors
        self._emit(timestamp)

    def _emit(self, timestamp: int) -> None:
        collector = self.objectCollector
        if collector is None:
            return
        travel_need = _CONFIRM_TRAVEL_FRACTION * max(self._width, 1)
        still_need = _STATIONARY_TRAVEL_FRACTION * max(self._width, 1)
        for track in self._tracks:
            if track.time_since_update > 0:
                continue
            center = _center(track.bbox)
            if track.first_ms is None:
                track.first_ms = timestamp
                track.anchor_ms = timestamp
            track.max_travel = max(track.max_travel, _dist(center, track.first_center))
            if not track.confirmed:
                size = ((track.bbox[2] - track.bbox[0]) * (track.bbox[3] - track.bbox[1])) ** 0.5
                if (track.hits >= _CONFIRM_HITS
                        and (timestamp - track.first_ms >= _CONFIRM_MS
                             or track.hits >= 2 * _CONFIRM_HITS)
                        and track.max_travel >= max(travel_need, 0.5 * size)):
                    track.confirmed = True
                else:
                    continue
            # Stationary: no real movement for a while -> stop reporting it
            # (no new frames, no new events) until it moves again.
            if _dist(center, track.anchor) > still_need:
                track.anchor = center
                track.anchor_ms = timestamp
                track.stationary = False
            elif timestamp - (track.anchor_ms or timestamp) >= _STATIONARY_MS:
                track.stationary = True
            if track.stationary:
                continue
            if track.sentry_id is None:
                track.sentry_id = collector.addObject(timestamp, "unknown")
            collector.addFrame(
                track.sentry_id,
                self._frame_index,
                timestamp,
                track.bbox,
                "unknown",
            )
