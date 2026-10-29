"""Re-run the truck-type classifier on recorded vehicle objects.

Only vehicle objects are touched. For each one the largest stored box
(``motion`` table) is cropped from the nearest decoded frame of its clip,
classified, and ``objAttributes.delivery_carrier`` is replaced (or removed
when the classifier now says none). Each clip file is decoded once for all
of its objects. No full re-detection is done.

Runs on its own SQLite connections so it can live on a worker thread.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Callable, Iterable, Optional

from backEnd.native.detector import BoundingBox, Detection

_CARRIER = "delivery_carrier"
_BUS_LABELS = ("Bus", "School bus")


class RescanResult:
    def __init__(self):
        self.objects = 0
        self.scanned = 0
        self.changed = 0
        self.skipped = 0
        self.clips = 0
        self.cancelled = False
        self.error = ""

    def __repr__(self):
        return ("RescanResult(clips=%d objects=%d scanned=%d changed=%d skipped=%d cancelled=%s error=%r)"
                % (self.clips, self.objects, self.scanned, self.changed, self.skipped, self.cancelled, self.error))


_PIPELINE_LOCK = threading.Lock()
_RESCAN_LOCK = threading.Lock()
_CACHED_PIPELINE = None


def _default_pipeline():
    """Carrier-only pipeline on the CPU, built once and shared.

    Loading every attribute model (plates, faces...) on a worker thread the
    first time re-scan ran was slow and could take down the UI process;
    re-scan only needs the truck-type classifier.
    """
    global _CACHED_PIPELINE
    with _PIPELINE_LOCK:
        if _CACHED_PIPELINE is None:
            from backEnd.native.attribute_recognizers import (
                _DEFAULT_FILES,
                _ENV_CARRIER,
                AttributePipeline,
                OnnxGraph,
                resolve_optional_model,
            )

            carrier = None
            path = resolve_optional_model(_ENV_CARRIER, _DEFAULT_FILES[_ENV_CARRIER])
            if path:
                try:
                    carrier = OnnxGraph(path, enable_gpu=False)
                except Exception:
                    carrier = None
            pipeline = AttributePipeline(carrier=carrier)
            if _ENV_CARRIER not in os.environ:
                from backEnd.community import active_carrier_model_path

                pipeline.watch_user_carrier_model(active_carrier_model_path, enable_gpu=False)
            _CACHED_PIPELINE = pipeline
        else:
            _CACHED_PIPELINE._maybe_reload_user_carrier()
        return _CACHED_PIPELINE


def _classify(pipeline, crop, previous: str):
    """Return ``(label or None, confidence)`` for one crop."""
    if previous in _BUS_LABELS:
        det = Detection(type="vehicle", bounding_box=BoundingBox(0, 0, 1, 1), score=0.5,
                        attributes={"vehicle_class": "bus"})
        found = pipeline._read_bus(crop, det)
    else:
        found = pipeline._read_carrier(crop)
    label = found.get(_CARRIER)
    return (str(label) if label else None), float(found.get("delivery_confidence") or 0.0)


def sample_source_clips(clip_conn, samples):
    """Distinct ``(camera, firstMs, lastMs)`` clips the training samples came from."""
    found = set()
    for sample in samples:
        cam, ms = sample.get("camera"), sample.get("timestamp_ms")
        if not cam or not ms:
            continue
        clip = _clip_for(clip_conn, cam, int(ms))
        if clip:
            found.add((cam, int(clip[1]), int(clip[2])))
    return sorted(found)


def rescan_sample_sources(obj_db: str, clip_db: str, video_dir: str, samples, **kwargs) -> RescanResult:
    """Re-scan every vehicle in the clips the given training samples came from."""
    clip_conn = sqlite3.connect(clip_db, timeout=30)
    obj_conn = sqlite3.connect(obj_db, timeout=30)
    try:
        clips = sample_source_clips(clip_conn, samples)
        ids = set()
        for cam, first, last in clips:
            ids.update(int(r[0]) for r in find_vehicle_objects(obj_conn, first, last, [cam]))
    finally:
        clip_conn.close()
        obj_conn.close()
    if not clips:
        return RescanResult()
    result = run_rescan(obj_db, clip_db, video_dir, min(c[1] for c in clips), max(c[2] for c in clips),
                        sorted({c[0] for c in clips}), sorted(ids), **kwargs)
    return result


def find_vehicle_objects(obj_conn, start_ms, end_ms, cameras=None, obj_ids=None):
    sql = "SELECT uid, camLoc FROM objects WHERE type='vehicle' AND timeStop>=? AND timeStart<=?"
    args: list = [int(start_ms), int(end_ms)]
    if cameras:
        sql += " AND camLoc IN (%s)" % ",".join("?" * len(cameras))
        args += list(cameras)
    rows = obj_conn.execute(sql, args).fetchall()
    if obj_ids is not None:
        wanted = {int(x) for x in obj_ids}
        rows = [r for r in rows if int(r[0]) in wanted]
    return rows


def _best_box(obj_conn, uid):
    return obj_conn.execute(
        "SELECT time, x1, y1, x2, y2 FROM motion WHERE objUid=? "
        "ORDER BY (x2-x1)*(y2-y1) DESC LIMIT 1", (int(uid),)).fetchone()


def _clip_for(clip_conn, cam, ms):
    return clip_conn.execute(
        "SELECT filename, firstMs, lastMs, procWidth, procHeight FROM clips "
        "WHERE camLoc=? AND firstMs<=? AND lastMs>=? ORDER BY isCache, firstMs DESC LIMIT 1",
        (cam, int(ms), int(ms))).fetchone()


def _resolve_clip(video_dir, filename):
    """Clip paths in clipdb are relative to the "archive" folder under the video dir."""
    parts = str(filename).split("/")
    for base in (os.path.join(video_dir, "archive"), video_dir):
        path = os.path.join(base, *parts)
        if os.path.isfile(path):
            return path
    return os.path.join(video_dir, "archive", *parts)


def run_rescan(*args, **kwargs) -> "RescanResult":
    """Serialized wrapper: only one re-scan touches the DB at a time."""
    with _RESCAN_LOCK:
        return _run_rescan(*args, **kwargs)


def _run_rescan(obj_db: str, clip_db: str, video_dir: str, start_ms: int, end_ms: int,
               cameras: Optional[Iterable[str]] = None, obj_ids: Optional[Iterable[int]] = None,
               pipeline=None, progress: Optional[Callable[[int, int], None]] = None,
               cancel: Optional[threading.Event] = None, frame_iter=None,
               pause_s: float = 0.002) -> RescanResult:
    """Re-classify vehicle objects. ``progress(done, total)`` is called often."""
    result = RescanResult()
    if frame_iter is None:
        from backEnd.native.open_capture import _iter_video_frames as frame_iter
    pipeline = pipeline if pipeline is not None else _default_pipeline()
    obj_conn = sqlite3.connect(obj_db, timeout=30)
    clip_conn = sqlite3.connect(clip_db, timeout=30)
    try:
        rows = find_vehicle_objects(obj_conn, start_ms, end_ms,
                                    list(cameras) if cameras else None,
                                    list(obj_ids) if obj_ids is not None else None)
        result.objects = len(rows)
        by_file: dict = {}
        for uid, cam in rows:
            box = _best_box(obj_conn, uid)
            clip = _clip_for(clip_conn, cam, box[0]) if box else None
            if not box or not clip:
                result.skipped += 1
                continue
            by_file.setdefault(clip, []).append((int(uid), box))
        result.clips = len(by_file)
        done = result.skipped
        if progress:
            progress(done, result.objects)
        import numpy as np

        for (filename, first_ms, _last, proc_w, proc_h), targets in by_file.items():
            if cancel is not None and cancel.is_set():
                result.cancelled = True
                break
            path = _resolve_clip(video_dir, filename)
            best: dict = {}
            want = {uid: int(box[0]) for uid, box in targets}
            last_needed = max(want.values()) + 1000
            try:
                origin = None
                for ms, rgb, width, height, _iw, _ih in frame_iter(path):
                    if cancel is not None and cancel.is_set():
                        break
                    origin = ms if origin is None else origin
                    absolute = int(first_ms) + (ms - origin)
                    for uid, t in want.items():
                        gap = abs(absolute - t)
                        if uid not in best or gap < best[uid][0]:
                            best[uid] = (gap, rgb, width, height)
                    if absolute > last_needed:
                        break
            except Exception as exc:
                result.error = "%s: %s" % (filename, exc)
            for uid, box in targets:
                if cancel is not None and cancel.is_set():
                    result.cancelled = True
                    break
                done += 1
                if uid not in best:
                    result.skipped += 1
                    continue
                _gap, rgb, width, height = best[uid]
                frame = np.frombuffer(rgb, dtype=np.uint8).reshape((height, width, 3))
                sx = width / float(proc_w or width)
                sy = height / float(proc_h or height)
                x1, y1, x2, y2 = (int(box[1] * sx), int(box[2] * sy), int(box[3] * sx), int(box[4] * sy))
                x1, x2 = max(0, min(x1, x2)), min(width, max(x1, x2))
                y1, y2 = max(0, min(y1, y2)), min(height, max(y1, y2))
                if x2 - x1 < 4 or y2 - y1 < 4:
                    result.skipped += 1
                    continue
                row = obj_conn.execute("SELECT value FROM objAttributes WHERE objUid=? AND key=?",
                                       (uid, _CARRIER)).fetchone()
                previous = str(row[0]) if row else ""
                label, confidence = _classify(pipeline, frame[y1:y2, x1:x2], previous)
                result.scanned += 1
                if (label or "") != previous:
                    if label:
                        obj_conn.execute(
                            "INSERT OR REPLACE INTO objAttributes (objUid, key, value, confidence, time) "
                            "VALUES (?, ?, ?, ?, ?)", (uid, _CARRIER, label, confidence, int(box[0])))
                    else:
                        obj_conn.execute("DELETE FROM objAttributes WHERE objUid=? AND key=?", (uid, _CARRIER))
                    result.changed += 1
                if progress:
                    progress(done, result.objects)
                if pause_s:
                    time.sleep(pause_s)  # yield to recording / UI
            obj_conn.commit()
            if result.cancelled:
                break
        obj_conn.commit()
    except Exception as exc:
        result.error = str(exc)
    finally:
        obj_conn.close()
        clip_conn.close()
    return result
