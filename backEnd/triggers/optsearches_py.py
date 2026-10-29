"""Pure-Python port of optsearches.c (same results, including C integer
division and the 10000 ray limit). Used when the native library is absent.

Arguments mirror the C API: bbox/segment objects with x1, y1, x2, y2
attributes (ctypes BBOX) and a sequence of segments.
"""

CENTER_POINT, TOP_POINT, BOTTOM_POINT, LEFT_POINT, RIGHT_POINT = range(5)
IS_LEFT, IS_RIGHT, IS_ON = range(3)
FROM_LEFT, FROM_RIGHT, FROM_ANY = range(3)


def _half(value):
    """C int division by 2 (truncates toward zero)."""
    return int(value / 2) if value < 0 else value // 2


def _track_point(box, location):
    x1, y1, x2, y2 = box.x1, box.y1, box.x2 - 1, box.y2 - 1
    if location == CENTER_POINT:
        return _half(x1 + x2), _half(y1 + y2)
    if location == TOP_POINT:
        return _half(x1 + x2), y1
    if location == BOTTOM_POINT:
        return _half(x1 + x2), y2
    if location == LEFT_POINT:
        return x1, _half(y1 + y2)
    if location == RIGHT_POINT:
        return x2, _half(y1 + y2)
    return 0, 0


def _side(boundary, px, py):
    a = (boundary.x2 - boundary.x1) * (py - boundary.y1)
    b = (boundary.y2 - boundary.y1) * (px - boundary.x1)
    return IS_LEFT if a > b else IS_RIGHT if a < b else IS_ON


def did_obj_cross(prevBox, curBox, boundary, location, direction):
    px, py = _track_point(prevBox, location)
    cx, cy = _track_point(curBox, location)
    prev_dir = _side(boundary, px, py)
    cur_dir = _side(boundary, cx, cy)
    if prev_dir == cur_dir:
        return 0
    obj_xs, obj_ys = px - cx, py - cy
    seg_xs, seg_ys = boundary.x1 - boundary.x2, boundary.y1 - boundary.y2
    denom = float(obj_xs * seg_ys - seg_xs * obj_ys)
    if denom == 0:
        return 0
    obj_cp = px * cy - cx * py
    seg_cp = boundary.x1 * boundary.y2 - boundary.x2 * boundary.y1
    int_x = (obj_cp * seg_xs - seg_cp * obj_xs) / denom
    int_y = (obj_cp * seg_ys - seg_cp * obj_ys) / denom
    if (((px <= int_x <= cx) or (cx <= int_x <= px)) and
            ((boundary.x1 <= int_x <= boundary.x2) or (boundary.x2 <= int_x <= boundary.x1))):
        if boundary.x1 == boundary.x2:
            if not (boundary.y2 <= int_y <= boundary.y1) and not (boundary.y1 <= int_y <= boundary.y2):
                return 0
        if px == cx:
            if not (cy <= int_y <= py) and not (py <= int_y <= cy):
                return 0
        if direction == FROM_ANY:
            if prev_dir != IS_ON:
                return 1
        elif direction == prev_dir:
            return 1
    return 0


def is_obj_inside(box, location, segments, numSegments):
    if location in (CENTER_POINT, TOP_POINT, BOTTOM_POINT, LEFT_POINT, RIGHT_POINT):
        t_x1, t_y1 = _track_point(box, location)
    else:
        t_x1, t_y1 = 0, 0
    t_x2, t_y2 = -1, t_y1
    t_xs, t_ys = t_x1 - t_x2, 0
    t_cp = t_xs * t_y1
    count = 0
    for i in range(int(numSegments)):
        seg = segments[i]
        x1, y1, x2, y2 = seg.x1, seg.y1, seg.x2, seg.y2
        xs, ys = x1 - x2, y1 - y2
        denom = float(t_xs * ys - xs * t_ys)
        if denom == 0:
            continue
        cp = x1 * y2 - x2 * y1
        int_x = (t_cp * xs - cp * t_xs) / denom
        int_y = (t_cp * ys - cp * t_ys) / denom
        if (((t_x1 <= int_x <= 10000) or (10000 <= int_x <= t_x1)) and
                ((x1 <= int_x <= x2) or (x2 <= int_x <= x1))):
            if x1 == x2:
                if not (y2 <= int_y <= y1) and not (y1 <= int_y <= y2):
                    continue
            if t_x1 == t_x2:
                if not (t_y2 <= int_y <= t_y1) and not (t_y1 <= int_y <= t_y2):
                    continue
            if (x1 == int_x and y1 == int_y and y2 > int_y) or (x2 == int_x and y2 == int_y and y1 > int_y):
                continue
            count += 1
    return count % 2
