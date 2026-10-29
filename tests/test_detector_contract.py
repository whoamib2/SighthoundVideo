"""The local detector HTTP contract, including room for later attributes."""

import json

import pytest

from backEnd.native.detector import (
    ATTR_DELIVERY_CARRIER,
    ATTR_FACE_IDENTITY,
    ATTR_PLATE_TEXT,
    DELIVERY_CARRIERS,
    Detection,
    TrackMatch,
    encode_detection_response,
    parse_detection_response,
    parse_rgb_body,
)
from backEnd.native.errors import NativeDependencyError


def test_rgb_body_must_cover_the_frame():
    with pytest.raises(ValueError):
        parse_rgb_body(b"\x00" * 10, 4, 4)
    body = parse_rgb_body(b"\x01" * (2 * 2 * 3 + 5), 2, 2)
    assert body == b"\x01" * 12
    with pytest.raises(TypeError):
        parse_rgb_body("not-bytes", 1, 1)


def test_attributes_roundtrip_on_the_wire():
    detection = Detection.from_json_object(
        {
            "type": "vehicle",
            "boundingBox": {"x": 1, "y": 2, "width": 3, "height": 4},
            "score": 0.5,
            "plate_text": "ABC123",
            "attributes": {"color": "blue"},
        }
    )
    detection.attributes[ATTR_FACE_IDENTITY] = "sam"
    detection.attributes[ATTR_DELIVERY_CARRIER] = "UPS"
    payload = encode_detection_response([detection])
    decoded = json.loads(payload)
    obj = decoded["objects"][0]
    assert obj["plate_text"] == "ABC123"
    assert obj["face_identity"] == "sam"
    assert obj["delivery_carrier"] == "UPS"
    assert obj["attributes"]["color"] == "blue"
    again = parse_detection_response(payload)
    assert again[0].attributes[ATTR_PLATE_TEXT] == "ABC123"
    assert again[0].attributes["color"] == "blue"


def test_reserved_carriers_and_track_match_unpacking():
    assert DELIVERY_CARRIERS == ("USPS", "FedEx", "UPS", "Amazon", "Garbage")
    match = TrackMatch(7, "person", 0.9, 0.4, attributes={"note": "later"})
    obj_id, det_type, score, overlap = match
    assert (obj_id, det_type, score, overlap) == (7, "person", 0.9, 0.4)
    assert match.attributes["note"] == "later"
    assert len(match) == 4


def test_closed_detector_is_isolated():
    from backEnd.native.detector import SIOObjectDetector

    with pytest.raises(NativeDependencyError):
        SIOObjectDetector(logger=None, model_dir="/tmp/missing-model")
