"""Rule triggers for plates, faces, and delivery carriers.

These wrap another trigger. A hit has to satisfy the child search and the
attribute test. Old queries leave the modes unset, so they never build one
of these wrappers.

Plate text is compared without spaces, case-insensitively. ``matches`` and
watchlist patterns use ``*`` and ``?``. Face identities come from the local
gallery; an unknown person is a ``person`` with no identity stored. Delivery
can require a zone and suppresses another hit for the same carrier until the
cooldown has passed.
"""

from __future__ import annotations

import fnmatch

from .BaseTrigger import BaseTrigger


def _normalize_plate(text) -> str:
    return "".join(str(text or "").upper().split())


def _pattern_match(text: str, pattern: str) -> bool:
    wanted = _normalize_plate(pattern)
    if not text or not wanted:
        return False
    if any(mark in wanted for mark in "*?"):
        return fnmatch.fnmatchcase(text, wanted)
    return text == wanted


def _point_inside(x: float, y: float, points) -> bool:
    if not points or len(points) < 3:
        return False
    inside = False
    previous = len(points) - 1
    for index, point in enumerate(points):
        xi, yi = float(point[0]), float(point[1])
        xj, yj = float(points[previous][0]), float(points[previous][1])
        crosses = (yi > y) != (yj > y)
        if crosses:
            denom = (yj - yi) or 1e-12
            if x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        previous = index
    return inside


class _AttributeTrigger(BaseTrigger):
    """Shared child-trigger plumbing. Subclasses implement ``_accept``."""

    def __init__(self, data_mgr, child=None):
        BaseTrigger.__init__(self)
        self._dataMgr = data_mgr
        self._child = child

    def setProcessingCoordSpace(self, coordSpace):
        if self._child is not None:
            self._child.setProcessingCoordSpace(coordSpace)

    def reset(self):
        if self._child is not None:
            self._child.reset()

    def setDataManager(self, dataManager):
        self._dataMgr = dataManager
        if self._child is not None:
            self._child.setDataManager(dataManager)

    def shouldCombineClips(self):
        if self._child is not None:
            return self._child.shouldCombineClips()
        return False

    def spatiallyAware(self):
        if self._child is not None:
            return self._child.spatiallyAware()
        return False

    def getPlayTimeOffset(self):
        if self._child is not None:
            return self._child.getPlayTimeOffset()
        return BaseTrigger.getPlayTimeOffset(self)

    def getClipLengthOffsets(self):
        if self._child is not None:
            return self._child.getClipLengthOffsets()
        return BaseTrigger.getClipLengthOffsets(self)

    def _hits(self, timeStart, timeStop, search_type, procSizesMsRange):
        if self._child is not None:
            return self._child.search(timeStart, timeStop, search_type, procSizesMsRange)
        obj_ids = self._dataMgr.getObjectsBetweenTimes(timeStart, timeStop)
        boxes = self._dataMgr.getObjectBboxesBetweenTimes(obj_ids, timeStart, timeStop)
        return [(row[6], row[4], row[5]) for row in boxes]

    def _attributes(self, obj_id) -> dict:
        getter = getattr(self._dataMgr, "getObjectAttributes", None)
        if getter is None:
            return {}
        return getter(obj_id) or {}

    def search(self, timeStart=None, timeStop=None, type="single", procSizesMsRange=None):
        hits = self._hits(timeStart, timeStop, type, procSizesMsRange)
        return [hit for hit in hits if self._accept(hit, type)]


class PlateTrigger(_AttributeTrigger):
    """Fire when a plate equals a string, matches a wildcard, or is on the watchlist."""

    def __init__(self, data_mgr, mode, pattern="", child=None):
        _AttributeTrigger.__init__(self, data_mgr, child)
        self._mode = str(mode or "")
        self._pattern = pattern or ""

    def __str__(self):
        return "PlateTrigger %s %s" % (self._mode, self._pattern)

    def _watchlist(self):
        listing = getattr(self._dataMgr, "listPlateWatchlist", None)
        if listing is None:
            return []
        return listing()

    def _accept(self, hit, search_type):
        _ = search_type
        plate = _normalize_plate(self._attributes(hit[0]).get("plate_text"))
        if not plate:
            return False
        if self._mode == "equals":
            return plate == _normalize_plate(self._pattern)
        if self._mode == "matches":
            return _pattern_match(plate, self._pattern)
        if self._mode == "watchlist":
            return any(_pattern_match(plate, pattern) for pattern in self._watchlist())
        return False


class FaceTrigger(_AttributeTrigger):
    """Fire for a known person, an unknown person, or one enrolled name."""

    def __init__(self, data_mgr, mode, name="", child=None):
        _AttributeTrigger.__init__(self, data_mgr, child)
        self._mode = str(mode or "")
        self._name = str(name or "")

    def __str__(self):
        return "FaceTrigger %s %s" % (self._mode, self._name)

    def _accept(self, hit, search_type):
        _ = search_type
        identity = str(self._attributes(hit[0]).get("face_identity") or "").strip()
        if self._mode == "known":
            return bool(identity)
        if self._mode == "specific":
            return identity.casefold() == self._name.strip().casefold() and bool(identity)
        if self._mode == "unknown":
            if identity:
                return False
            obj_type = self._dataMgr.getObjectType(hit[0])
            return str(obj_type or "") == "person"
        return False


class DeliveryTrigger(_AttributeTrigger):
    """Fire when a chosen carrier is seen, once per visit.

    ``cooldown_ms`` suppresses another hit for that carrier until the gap has
    elapsed. Realtime search keeps that memory on the trigger. A historical
    search applies the same gap inside the one result list. ``zone_points``
    is an optional polygon in the same coordinate space as the stored boxes.
    """

    def __init__(self, data_mgr, carriers, cooldown_ms=300000, zone_points=None, child=None):
        _AttributeTrigger.__init__(self, data_mgr, child)
        self._carriers = {str(name) for name in (carriers or [])}
        self._cooldown_ms = int(cooldown_ms or 0)
        self._zone = list(zone_points) if zone_points else None
        self._last_fire = {}

    def __str__(self):
        return "DeliveryTrigger %s" % ", ".join(sorted(self._carriers))

    def reset(self):
        self._last_fire = {}
        _AttributeTrigger.reset(self)

    def _in_zone(self, obj_id, frame) -> bool:
        if not self._zone:
            return True
        bbox = self._dataMgr.getBboxAtFrame(obj_id, frame)
        if not bbox:
            return False
        x1, y1, x2, y2 = [float(value) for value in bbox[:4]]
        return _point_inside((x1 + x2) / 2.0, (y1 + y2) / 2.0, self._zone)

    def search(self, timeStart=None, timeStop=None, type="single", procSizesMsRange=None):
        hits = self._hits(timeStart, timeStop, type, procSizesMsRange)
        last = self._last_fire if type == "realtime" else {}
        kept = []
        for obj_id, frame, when in sorted(hits, key=lambda item: (int(item[2]), int(item[1]))):
            carrier = str(self._attributes(obj_id).get("delivery_carrier") or "")
            if carrier not in self._carriers:
                continue
            if not self._in_zone(obj_id, frame):
                continue
            seen = last.get(carrier)
            if (
                self._cooldown_ms > 0
                and seen is not None
                and int(when) - int(seen) < self._cooldown_ms
            ):
                continue
            last[carrier] = int(when)
            kept.append((obj_id, frame, when))
        if type == "realtime":
            self._last_fire = last
        return kept
