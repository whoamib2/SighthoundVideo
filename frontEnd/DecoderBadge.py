"""HW/SW decoder badge text for camera tiles and the large-view status line.

Reads live/<camera>.decoder.json written by the capture process
(backEnd/native/open_capture.py) and caches it by file mtime.
"""

import json
import os

_cache = {}


def read_decoder_info(dataDir, camera):
    path = os.path.join(dataDir, "live", "%s.decoder.json" % camera)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            info = json.load(handle)
    except (OSError, ValueError):
        return hit[1] if hit else {}
    _cache[path] = (mtime, info)
    return info


def badge_for(info):
    """(badge, tooltip): "HW"/"SW"/"" and a one-line description."""
    decoder = str((info or {}).get("decoder") or "")
    if not decoder:
        return "", "Decoder: not reported yet"
    badge = "HW" if decoder.lower().startswith("hardware") else "SW"
    return badge, "Decoder: %s" % decoder


def status_line(camera, info):
    if not info or not info.get("decoder"):
        return "%s: decoder not reported yet" % camera if camera else ""
    badge, _ = badge_for(info)
    parts = ["%s %s" % (badge, info.get("decoder"))]
    if info.get("codec"):
        parts.append("%s %sx%s" % (info.get("codec"), info.get("width"), info.get("height")))
    if info.get("fps"):
        parts.append("%s fps" % info.get("fps"))
    if info.get("analytics_stream"):
        parts.append("analytics: %s stream" % info.get("analytics_stream"))
    if info.get("recording"):
        parts.append("recording: %s" % info.get("recording"))
    return "%s \u2014 %s" % (camera, " \u00b7 ".join(parts))
