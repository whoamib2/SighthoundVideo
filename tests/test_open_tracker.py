"""Motion gate and multi-object tracker."""

import pytest

np = pytest.importorskip("numpy")

from backEnd.native.open_tracker import OpenVideoPipeline  # noqa: E402


class _Frame:
    def __init__(self, array) -> None:
        self.rgb = array.tobytes()
        self.width = int(array.shape[1])
        self.height = int(array.shape[0])
        self.wasResized = False
        self.dummy = False
        self.filename = "clip.mp4"
        self.ms = 0

    def getLargeFrame(self):
        return self


class _Collector:
    def __init__(self) -> None:
        self.frames = []
        self._next = 1

    def addObject(self, time_start, obj_type):
        ident = self._next
        self._next += 1
        return ident

    def addFrame(self, obj_id, frame_id, time, bbox, obj_type):
        self.frames.append((obj_id, frame_id, time, bbox, obj_type))

    def frameCompleted(self, ms, frame_num):
        return None


def _rect_iou(box, x, y, w, h) -> float:
    x1, y1, x2, y2 = box
    ix1 = max(x1, x)
    iy1 = max(y1, y)
    ix2 = min(x2, x + w)
    iy2 = min(y2, y + h)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = max(0, x2 - x1) * max(0, y2 - y1)
    area_b = w * h
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / float(union)


def test_tracker_follows_a_moving_rectangle():
    collector = _Collector()
    pipeline = OpenVideoPipeline(collector, "pipeline.cfg")
    width, height = 320, 240
    for index in range(12):
        array = np.zeros((height, width, 3), dtype=np.uint8)
        x = 20 + index * 10
        array[80:180, x : x + 40] = 255
        pipeline.processClipFrame(_Frame(array), index * 100)
    pipeline.flush()
    assert collector.frames
    # The box on the later frames should sit on the rectangle, not the whole image.
    last = collector.frames[-1][3]
    x = 20 + 11 * 10
    assert _rect_iou(last, x, 80, 40, 100) > 0.3
    assert all(box[2] > box[0] and box[3] > box[1] for _i, _f, _t, box, _k in collector.frames)
