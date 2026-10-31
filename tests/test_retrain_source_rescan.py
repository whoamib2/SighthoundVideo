"""Every successful retrain re-scans the clips its training samples came from."""
import json
import sqlite3

from backEnd import rescan, training_samples as ts


def _dbs(tmp_path):
    clip_db, obj_db = str(tmp_path / "clips.db"), str(tmp_path / "obj.db")
    c = sqlite3.connect(clip_db)
    c.execute("CREATE TABLE clips (camLoc, filename, firstMs, lastMs, procWidth, procHeight, isCache)")
    c.executemany("INSERT INTO clips VALUES (?,?,?,?,?,?,0)", [
        ("Front", "a.mp4", 1000, 5000, 320, 240), ("Front", "b.mp4", 9000, 12000, 320, 240),
        ("Side", "c.mp4", 1000, 5000, 320, 240)])
    c.commit(); c.close()
    o = sqlite3.connect(obj_db)
    o.execute("CREATE TABLE objects (uid, camLoc, type, timeStart, timeStop)")
    o.executemany("INSERT INTO objects VALUES (?,?,?,?,?)", [
        (1, "Front", "vehicle", 1500, 2500), (2, "Front", "vehicle", 4000, 4800),
        (3, "Front", "vehicle", 9500, 9900), (4, "Side", "vehicle", 1500, 2000),
        (5, "Front", "person", 1500, 2000)])
    o.commit(); o.close()
    return obj_db, clip_db


def test_source_clips_and_object_ids(tmp_path, monkeypatch):
    obj_db, clip_db = _dbs(tmp_path)
    samples = [{"camera": "Front", "timestamp_ms": 2000}, {"camera": "Front", "timestamp_ms": 3000},
               {"camera": "Nope", "timestamp_ms": 2000}, {"camera": "", "timestamp_ms": 0}]
    assert rescan.sample_source_clips(sqlite3.connect(clip_db), samples) == [("Front", 1000, 5000)]
    seen = {}

    def fake(obj, clip, vid, start, end, cameras, ids, **kw):
        seen.update(start=start, end=end, cameras=cameras, ids=ids)
        res = rescan.RescanResult(); res.clips = 1; res.changed = 2
        return res

    monkeypatch.setattr(rescan, "run_rescan", fake)
    res = rescan.rescan_sample_sources(obj_db, clip_db, str(tmp_path), samples)
    assert seen == {"start": 1000, "end": 5000, "cameras": ["Front"], "ids": [1, 2]}
    assert res.clips == 1


def test_no_samples_no_scan(tmp_path, monkeypatch):
    obj_db, clip_db = _dbs(tmp_path)
    monkeypatch.setattr(rescan, "run_rescan", lambda *a, **k: 1 / 0)
    assert rescan.rescan_sample_sources(obj_db, clip_db, str(tmp_path), []).clips == 0


def test_read_samples_skips_bad_lines(tmp_path):
    (tmp_path / "samples.jsonl").write_text(json.dumps({"camera": "A", "timestamp_ms": 5}) + "\nnot json\n")
    assert ts.read_samples(str(tmp_path)) == [{"camera": "A", "timestamp_ms": 5}]


def test_result_line():
    from frontEnd.TrainingDialogs import sourceRescanLine
    res = rescan.RescanResult(); res.clips = 3; res.changed = 1
    assert sourceRescanLine(res) == "Re-scanned 3 source clips (1 label changed)."
