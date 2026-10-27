"""Text for plate, face, and delivery attributes.

Rule names, search rows, and push/email notifications share these phrases.
A delivery at a named place reads like ``UPS truck at the curb``.
"""

from __future__ import annotations

PLATE_TEXT = "plate_text"
PLATE_CONFIDENCE = "plate_confidence"
FACE_IDENTITY = "face_identity"
FACE_CONFIDENCE = "face_confidence"
DELIVERY_CARRIER = "delivery_carrier"
DELIVERY_CONFIDENCE = "delivery_confidence"

_ATTRIBUTE_KEYS = (
    (PLATE_TEXT, PLATE_CONFIDENCE),
    (FACE_IDENTITY, FACE_CONFIDENCE),
    (DELIVERY_CARRIER, DELIVERY_CONFIDENCE),
)


def _confidence(attributes: dict, key: str) -> float:
    try:
        return float(attributes.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def merge_track_attributes(current, incoming):
    """Keep the best plate, identity, and carrier seen on one track.

    A later frame can outrank an earlier read. An empty face or a ``none``
    carrier does not erase a stronger earlier result. Type decisions do not
    freeze this merge; the best read is the one with the higher confidence.
    """
    merged = dict(current or {})
    new = dict(incoming or {})
    if not new:
        return merged

    plate = str(new.get(PLATE_TEXT) or "").strip()
    if plate and _confidence(new, PLATE_CONFIDENCE) >= _confidence(merged, PLATE_CONFIDENCE):
        merged[PLATE_TEXT] = plate
        merged[PLATE_CONFIDENCE] = _confidence(new, PLATE_CONFIDENCE)

    identity = str(new.get(FACE_IDENTITY) or "").strip()
    if identity and _confidence(new, FACE_CONFIDENCE) >= _confidence(merged, FACE_CONFIDENCE):
        merged[FACE_IDENTITY] = identity
        merged[FACE_CONFIDENCE] = _confidence(new, FACE_CONFIDENCE)

    carrier = str(new.get(DELIVERY_CARRIER) or "").strip()
    if carrier and carrier.lower() != "none":
        better = _confidence(new, DELIVERY_CONFIDENCE) >= _confidence(merged, DELIVERY_CONFIDENCE)
        previous = str(merged.get(DELIVERY_CARRIER) or "").strip().lower()
        if better or previous in ("", "none"):
            merged[DELIVERY_CARRIER] = carrier
            merged[DELIVERY_CONFIDENCE] = _confidence(new, DELIVERY_CONFIDENCE)
    return merged


def stored_attribute_rows(attributes, time_ms: int = 0):
    """Rows to write for one object: ``(key, value, confidence, time)``."""
    rows = []
    data = attributes or {}
    for key, confidence_key in _ATTRIBUTE_KEYS:
        value = str(data.get(key) or "").strip()
        if not value or value.lower() == "none":
            continue
        rows.append((key, value, _confidence(data, confidence_key), int(time_ms)))
    return rows


def notification_sentence(attributes, place: str = "") -> str:
    """One short sentence for a single object's attributes.

    ``notification_sentence({"delivery_carrier": "UPS"}, "the curb")`` is
    ``UPS truck at the curb``.
    """
    data = attributes or {}
    carrier = str(data.get(DELIVERY_CARRIER) or "").strip()
    if carrier and carrier.lower() != "none":
        text = "%s truck" % carrier
        where = (place or "").strip()
        if where:
            text = "%s at %s" % (text, where)
        return text
    plate = str(data.get(PLATE_TEXT) or "").strip()
    if plate:
        return "plate %s" % plate
    identity = str(data.get(FACE_IDENTITY) or "").strip()
    if identity:
        return identity
    return ""


def attribute_search_label(attributes) -> str:
    """Compact plate / identity / carrier text for a search-result row."""
    data = attributes or {}
    parts = []
    carrier = str(data.get(DELIVERY_CARRIER) or "").strip()
    if carrier and carrier.lower() != "none":
        parts.append(carrier)
    plate = str(data.get(PLATE_TEXT) or "").strip()
    if plate:
        parts.append(plate)
    identity = str(data.get(FACE_IDENTITY) or "").strip()
    if identity:
        parts.append(identity)
    return ", ".join(parts)


def choose_notification(sentence: str, rule_name: str) -> str:
    """Notification body. Prefer the rule name when it already contains the phrase.

    An auto-named delivery rule is ``UPS truck at the curb``, and the attribute
    sentence is ``UPS truck``. The longer rule name is what the device shows.
    """
    sentence = (sentence or "").strip()
    rule_name = (rule_name or "").strip()
    if sentence and rule_name and sentence in rule_name:
        return rule_name
    if sentence:
        return sentence
    if rule_name:
        return 'Alert for rule "%s"' % rule_name
    return ""
