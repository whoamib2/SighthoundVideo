"""Attribute triggers, gallery storage, and database migration."""

import os
import sqlite3

import pytest

from appCommon.CommonStrings import kObjDbFile
from appCommon.DbRecovery import DbRecovery
from backEnd.DataManager import DataManager
from backEnd.SavedQueryDataModel import SavedQueryDataModel
from backEnd.triggers.AttributeTriggers import DeliveryTrigger, FaceTrigger, PlateTrigger
from backEnd.triggers.TargetTrigger import TargetTrigger
from tests.open_backend_support import Log

pytest.importorskip("numpy")


def _manager(tmp_path):
    logger = Log()
    manager = DataManager(logger)
    manager.open(str(tmp_path / "objects.db"))
    return logger, manager


def _track(manager, obj_type, when, bbox, camera="curb"):
    obj_id = manager.addObject(when, obj_type, camera)
    manager.addFrame(obj_id, 1, when, bbox, obj_type, None)
    return obj_id


def test_plate_equals_wildcard_and_watchlist(tmp_path):
    _logger, manager = _manager(tmp_path)
    try:
        first = _track(manager, "vehicle", 1000, (0, 0, 20, 20))
        second = _track(manager, "vehicle", 2000, (0, 0, 20, 20))
        manager.setObjectAttributes(first, {"plate_text": "AB12", "plate_confidence": 0.9}, 1000)
        manager.setObjectAttributes(second, {"plate_text": "ZZZ999", "plate_confidence": 0.9}, 2000)
        equals = PlateTrigger(manager, "equals", "ab 12")
        assert [hit[0] for hit in equals.search()] == [first]
        matches = PlateTrigger(manager, "matches", "AB*")
        assert [hit[0] for hit in matches.search()] == [first]
        manager.addPlateWatch("Z?Z999")
        manager.addPlateWatch("NOPE")
        watch = PlateTrigger(manager, "watchlist")
        assert [hit[0] for hit in watch.search()] == [second]
        assert "Z?Z999" in manager.listPlateWatchlist()
        manager.removePlateWatch("Z?Z999")
        assert manager.listPlateWatchlist() == ["NOPE"]
    finally:
        manager.close()


def test_face_known_unknown_and_specific(tmp_path):
    import numpy as np

    _logger, manager = _manager(tmp_path)
    try:
        known = _track(manager, "person", 1000, (0, 0, 10, 40))
        stranger = _track(manager, "person", 2000, (0, 0, 10, 40))
        truck = _track(manager, "vehicle", 3000, (0, 0, 30, 20))
        manager.setObjectAttributes(known, {"face_identity": "Ada", "face_confidence": 0.91}, 1000)
        vector = np.arange(4, dtype=np.float32)
        manager.enrollFace("Ada", vector.tobytes(), 4)
        assert manager.listFaces()[0][0] == "Ada"
        assert [hit[0] for hit in FaceTrigger(manager, "known").search()] == [known]
        assert [hit[0] for hit in FaceTrigger(manager, "specific", "ada").search()] == [known]
        unknown = FaceTrigger(manager, "unknown")
        assert stranger in [hit[0] for hit in unknown.search()]
        assert known not in [hit[0] for hit in unknown.search()]
        assert truck not in [hit[0] for hit in unknown.search()]
        manager.deleteFace("Ada")
        assert manager.listFaces() == []
        manager.setFaceThreshold(0.55)
        assert manager.getFaceThreshold() == pytest.approx(0.55)
    finally:
        manager.close()


def test_delivery_zone_and_cooldown(tmp_path):
    _logger, manager = _manager(tmp_path)
    try:
        inside = _track(manager, "vehicle", 1000, (10, 10, 20, 20))
        outside = _track(manager, "vehicle", 1500, (200, 200, 220, 220))
        later = _track(manager, "vehicle", 10000, (12, 12, 22, 22))
        for obj_id, when in ((inside, 1000), (outside, 1500), (later, 10000)):
            manager.setObjectAttributes(
                obj_id, {"delivery_carrier": "UPS", "delivery_confidence": 0.8}, when
            )
        zone = [(0, 0), (100, 0), (100, 100), (0, 100)]
        trigger = DeliveryTrigger(manager, ["UPS"], cooldown_ms=5000, zone_points=zone)
        hits = trigger.search(type="single")
        assert [hit[0] for hit in hits] == [inside, later]
        live = DeliveryTrigger(manager, ["UPS"], cooldown_ms=5000, zone_points=zone)
        first = live.search(0, 2000, type="realtime")
        assert [hit[0] for hit in first] == [inside]
        assert live.search(1000, 2000, type="realtime") == []
        assert [hit[0] for hit in live.search(9000, 11000, type="realtime")] == [later]
        live.reset()
        assert [hit[0] for hit in live.search(type="realtime")] == [inside, later]
    finally:
        manager.close()


def test_old_database_gains_attribute_tables_and_recovery_keeps_them(tmp_path):
    logger = Log()
    legacy = tmp_path / "legacy.db"
    connection = sqlite3.connect(str(legacy))
    connection.execute(
        "CREATE TABLE objects (uid INTEGER PRIMARY KEY, fileName TEXT, camLoc TEXT, "
        "timeStart INTEGER, timeStop INTEGER, rX1 INTEGER, rY1 INTEGER, rX2 INTEGER, "
        "rY2 INTEGER, type TEXT, confidence REAL, thumbnail BLOB)"
    )
    connection.execute(
        "INSERT INTO objects (camLoc, timeStart, timeStop, type) VALUES ('porch', 5, 5, 'person')"
    )
    connection.commit()
    connection.close()

    manager = DataManager(logger)
    manager.open(str(legacy))
    try:
        names = [row[0] for row in manager._cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )]
        for table in ("objAttributes", "faceIdentities", "plateWatchlist", "attributeSettings"):
            assert table in names
        version = manager._cur.execute("PRAGMA user_version").fetchone()[0]
        assert version == 3
        columns = [row[1] for row in manager._cur.execute("PRAGMA table_info(objects)")]
        assert "minWidth" in columns
        assert manager._cur.execute("SELECT type FROM objects").fetchone()[0] == "person"
        obj_id = manager.addObject(9, "vehicle", "curb")
        manager.setObjectAttributes(obj_id, {"delivery_carrier": "FedEx", "delivery_confidence": 0.7}, 9)
        import numpy as np

        manager.enrollFace("Bea", np.ones(4, dtype=np.float32).tobytes(), 4)
        manager.addPlateWatch("ABC*")
        manager.save()
    finally:
        manager.close()

    folder = tmp_path / "recover"
    folder.mkdir()
    os.replace(legacy, folder / kObjDbFile)
    assert DbRecovery(str(folder), logger).recover(kObjDbFile, None)
    manager = DataManager(logger)
    manager.open(str(folder / kObjDbFile))
    try:
        rows = manager._cur.execute(
            "SELECT value FROM objAttributes WHERE key='delivery_carrier'"
        ).fetchall()
        assert rows == [("FedEx",)]
        assert manager.listFaces()[0][0] == "Bea"
        assert manager.listPlateWatchlist() == ["ABC*"]
    finally:
        manager.close()


def test_saved_query_defaults_and_wraps_triggers(tmp_path):
    _logger, manager = _manager(tmp_path)
    try:
        model = SavedQueryDataModel("front door")
        state = dict(model.__dict__)
        for key in (
            "_plateMode", "_platePattern", "_faceMode", "_faceName",
            "_deliveryCarriers", "_deliveryCooldownSec", "_deliveryUseZone",
        ):
            state.pop(key, None)
        restored = SavedQueryDataModel.__new__(SavedQueryDataModel)
        restored.__setstate__(state)
        assert restored.getPlateMode() == ""
        assert restored.getDeliveryCarriers() == []
        plain = restored.getUsableQuery(manager)
        assert isinstance(plain, TargetTrigger)

        restored.setPlateMode("equals")
        restored.setPlatePattern("ABC123")
        restored.setFaceMode("specific")
        restored.setFaceName("Ada")
        restored.setDeliveryCarriers(["UPS"])
        restored.setDeliveryUseZone(True)
        restored.setDeliveryCooldownSec(120)
        restored.getTriggers()[0].setRegionName("the curb")
        assert "UPS truck at the curb" in restored.getAutoName()
        wrapped = restored.getUsableQuery(manager)
        assert isinstance(wrapped, DeliveryTrigger)
        assert "UPS truck at the curb" in restored.getAutoName()
    finally:
        manager.close()
