"""Retrain bookkeeping, auto-retrain scheduling, and clip re-scan."""

import datetime
import os
import sqlite3
import threading

import numpy as np
import pytest
from PIL import Image

from backEnd import training_samples as ts
from backEnd.native.attribute_recognizers import AttributePipeline
from backEnd.rescan import run_rescan


def _add(root, label, color, n=2):
    for _ in range(n):
        ts.save_sample(Image.new("RGB", (40, 40), color), label, None, "cam", 1, root=root)


def test_retrain_tracks_new_and_trained_and_keeps_backup(tmp_path):
    pytest.importorskip("onnx")
    root = str(tmp_path)
    ok, reason = ts.can_retrain(root)
    assert not ok and "two" in reason
    _add(root, "Garbage truck", (30, 120, 50))
    _add(root, "USPS", (20, 40, 200))
    assert ts.new_sample_count(root) == 4
    assert ts.can_retrain(root)[0]
    ts.retrain_carrier_model(root)
    state = ts.load_state(root)
    assert state["last_ok"] and state["last_result"].startswith("success: 4 samples")
    assert ts.new_sample_count(root) == 0
    ok, reason = ts.can_retrain(root)
    assert not ok and "Nothing new" in reason
    rows = {r[0]: r for r in ts.label_status(root)}
    assert rows["dataset/Garbage"][1:3] == (0, 2)
    # Samples are kept after training.
    assert len(ts.list_samples("dataset/Garbage", root)) == 2
    _add(root, "UPS", (150, 90, 40), n=1)
    assert ts.label_status(root) and {r[0]: r for r in ts.label_status(root)}["dataset/UPS"][1] == 1
    ts.retrain_carrier_model(root)
    model = ts.user_carrier_model_path(root)
    assert os.path.isfile(model.replace(".onnx", ".prev.onnx"))
    # Deleting a bad sample.
    bad = ts.list_samples("dataset/UPS", root)
    assert ts.delete_samples(bad, root) == 1
    assert ts.delete_samples(["../../outside.txt"], root) == 0


def test_auto_retrain_due(tmp_path):
    root = str(tmp_path)
    now = datetime.datetime(2026, 10, 29, 2, 30)
    assert ts.auto_retrain_due(now, root) is None  # nothing to train
    _add(root, "Garbage truck", (30, 120, 50))
    _add(root, "USPS", (20, 40, 200))
    ts.save_settings({"threshold": 0}, root)
    assert ts.auto_retrain_due(datetime.datetime(2026, 10, 29, 1, 0), root) is None
    assert ts.auto_retrain_due(now, root) == "nightly"
    ts.mark_nightly_checked(now, root)
    assert ts.auto_retrain_due(now, root) is None
    ts.save_settings({"threshold": 3}, root)
    assert ts.auto_retrain_due(now, root) == "threshold"
    ts.save_settings({"auto_enabled": False}, root)
    assert ts.auto_retrain_due(now, root) is None
    assert ts.get_settings(root)["threshold"] == 3


def _dbs(tmp_path, uids_prev):
    obj = str(tmp_path / "objdb2")
    clip = str(tmp_path / "clipdb")
    o = sqlite3.connect(obj)
    o.execute("CREATE TABLE objects (uid INTEGER PRIMARY KEY, camLoc TEXT, timeStart INTEGER, timeStop INTEGER, type TEXT)")
    o.execute("CREATE TABLE motion (objUid INTEGER, frame INTEGER, time INTEGER, x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER)")
    o.execute("CREATE TABLE objAttributes (objUid INTEGER, key TEXT, value TEXT, confidence REAL, time INTEGER, PRIMARY KEY (objUid, key))")
    for uid, prev in uids_prev:
        o.execute("INSERT INTO objects VALUES (?, 'Cam', 1000, 2000, 'vehicle')", (uid,))
        o.execute("INSERT INTO motion VALUES (?, 1, 1500, 10, 10, 50, 40)", (uid,))
        o.execute("INSERT INTO motion VALUES (?, 2, 1600, 10, 10, 12, 12)", (uid,))
        if prev:
            o.execute("INSERT INTO objAttributes VALUES (?, 'delivery_carrier', ?, 0.5, 1500)", (uid, prev))
    o.execute("INSERT INTO objects VALUES (99, 'Cam', 1000, 2000, 'person')")
    o.commit()
    o.close()
    c = sqlite3.connect(clip)
    c.execute("CREATE TABLE clips (filename TEXT, camLoc TEXT, firstMs INTEGER, lastMs INTEGER, isCache INTEGER, procWidth INTEGER, procHeight INTEGER)")
    c.execute("INSERT INTO clips VALUES ('Cam/1000.mp4', 'Cam', 1000, 3000, 0, 64, 48)")
    c.commit()
    c.close()
    return obj, clip


def _frames(color):
    def iterate(path):
        frame = np.zeros((96, 128, 3), dtype=np.uint8)
        frame[:, :] = color
        for ms in range(0, 2000, 100):
            yield ms, frame.tobytes(), 128, 96, 128, 96
    return iterate


def _labels(obj):
    conn = sqlite3.connect(obj)
    try:
        return dict(conn.execute("SELECT objUid, value FROM objAttributes WHERE key='delivery_carrier'"))
    finally:
        conn.close()


def test_rescan_updates_and_clears_labels(tmp_path):
    obj, clip = _dbs(tmp_path, [(1, ""), (2, "UPS"), (3, "Bus")])
    pipeline = AttributePipeline(use_heuristic=True)
    seen = []
    result = run_rescan(obj, clip, str(tmp_path), 0, 10 ** 6, pipeline=pipeline,
                        frame_iter=_frames((30, 120, 50)), progress=lambda d, t: seen.append((d, t)),
                        pause_s=0)
    assert result.objects == 3 and result.scanned == 3 and not result.error
    labels = _labels(obj)
    assert labels[1] == "Garbage" and labels[2] == "Garbage" and labels[3] == "Bus"
    assert seen[-1] == (3, 3)
    # Yellow bus becomes School bus; a gray frame clears the carrier label.
    run_rescan(obj, clip, str(tmp_path), 0, 10 ** 6, pipeline=pipeline,
               frame_iter=_frames((245, 190, 20)), obj_ids=[3], pause_s=0)
    assert _labels(obj)[3] == "School bus"
    result = run_rescan(obj, clip, str(tmp_path), 0, 10 ** 6, cameras=["Cam"], pipeline=pipeline,
                        frame_iter=_frames((128, 128, 128)), obj_ids=[1], pause_s=0)
    assert result.changed == 1 and 1 not in _labels(obj)


def test_rescan_cancel_and_out_of_range(tmp_path):
    obj, clip = _dbs(tmp_path, [(1, "")])
    cancel = threading.Event()
    cancel.set()
    result = run_rescan(obj, clip, str(tmp_path), 0, 10 ** 6, pipeline=AttributePipeline(use_heuristic=True),
                        frame_iter=_frames((30, 120, 50)), cancel=cancel, pause_s=0)
    assert result.cancelled and _labels(obj) == {}
    result = run_rescan(obj, clip, str(tmp_path), 5000, 6000, pipeline=AttributePipeline(use_heuristic=True),
                        frame_iter=_frames((30, 120, 50)), pause_s=0)
    assert result.objects == 0
