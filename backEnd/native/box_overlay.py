"""Draw stored object boxes onto clip frames during search playback.

DataManager hands ClipReader a ``boxList`` of
``[absMs, "drawbox=x:y:w:h:procW:procH:uid:color:t=0"]`` entries (the old
native player's format). This module parses that list once per clip and
draws the box nearest in time for each object onto each RGB frame.
"""

from __future__ import annotations

import bisect
from typing import Any, Optional

# Boxes are stored at the analytics rate (a few per second), so a frame
# shows the closest box within this window, or nothing.
MAX_GAP_MS = 600

_COLORS = {
    "yellow": (255, 220, 0), "orange": (255, 140, 0), "pink": (255, 105, 180),
    "green": (0, 220, 0), "blue": (40, 120, 255), "red": (255, 40, 40),
}


def parse_drawbox(spec: str) -> Optional[tuple]:
    """Return (x, y, w, h, procW, procH, uid, color) or None."""
    if not isinstance(spec, str) or not spec.startswith("drawbox="):
        return None
    parts = spec[len("drawbox="):].split(":")
    if len(parts) < 8:
        return None
    try:
        nums = [int(float(p)) for p in parts[:7]]
    except ValueError:
        return None
    return (*nums, parts[7])


class BoxOverlay:
    def __init__(self, boxList: Any, labels: Any = None, zonesList: Any = None) -> None:
        self._tracks: dict[int, tuple[list[int], list[tuple]]] = {}
        self._labels = dict(labels or {})
        self._zones: list[tuple] = []
        for entry in boxList or []:
            try:
                when, spec = int(entry[0]), entry[1]
            except (TypeError, ValueError, IndexError):
                continue
            box = parse_drawbox(spec)
            if box is None:
                continue
            times, boxes = self._tracks.setdefault(box[6], ([], []))
            pos = bisect.bisect(times, when)
            times.insert(pos, when)
            boxes.insert(pos, box)
        for entry in zonesList or []:
            try:
                box = parse_drawbox(entry[1])
            except (TypeError, IndexError):
                box = None
            if box is not None:
                self._zones.append(box)

    def __bool__(self) -> bool:
        return bool(self._tracks or self._zones)

    def boxes_at(self, absMs: int) -> list[tuple]:
        """Boxes to draw at ``absMs``: (x, y, w, h, procW, procH, uid, color)."""
        found = []
        for times, boxes in self._tracks.values():
            pos = bisect.bisect_left(times, absMs)
            best = None
            for cand in (pos - 1, pos):
                if 0 <= cand < len(times):
                    gap = abs(times[cand] - absMs)
                    if gap <= MAX_GAP_MS and (best is None or gap < best[0]):
                        best = (gap, boxes[cand])
            if best is not None:
                found.append(best[1])
        return found

    def label_for(self, uid: int) -> str:
        return str(self._labels.get(uid, ""))

    def draw(self, rgb: bytes, width: int, height: int, absMs: int) -> bytes:
        boxes = self.boxes_at(absMs)
        if not boxes and not self._zones:
            return rgb
        from PIL import Image, ImageDraw

        image = Image.frombytes("RGB", (width, height), bytes(rgb))
        draw = ImageDraw.Draw(image)
        thick = max(2, width // 400)
        for x1, y1, x2, y2, pw, ph, _uid, color in self._zones:
            if pw > 0 and ph > 0:
                sx, sy = width / pw, height / ph
                draw.line([(x1 * sx, y1 * sy), (x2 * sx, y2 * sy)],
                          fill=_COLORS.get(color, (255, 40, 40)), width=thick)
        for x, y, w, h, pw, ph, uid, color in boxes:
            if pw <= 0 or ph <= 0:
                continue
            sx, sy = width / pw, height / ph
            left, top = x * sx, y * sy
            right, bottom = (x + w) * sx, (y + h) * sy
            fill = _COLORS.get(color, (0, 220, 0))
            draw.rectangle([left, top, right, bottom], outline=fill, width=thick)
            text = self.label_for(uid)
            if text:
                tw = draw.textlength(text) if hasattr(draw, "textlength") else 6 * len(text)
                ty = top - 14 if top >= 14 else top
                draw.rectangle([left, ty, left + tw + 6, ty + 13], fill=fill)
                draw.text((left + 3, ty), text, fill=(0, 0, 0))
        return image.tobytes()
