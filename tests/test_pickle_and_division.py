"""Pickle blobs and integer pixel math survive the Python 3 port."""

import pickle

from backEnd.triggers.TriggerUtils import getBboxTrackingPoint
from vitaToolbox.strUtils.EnsureUnicode import ensureUnicode, ensureUtf8
from vitaToolbox.strUtils.PickleCompat import loads_pickle


def test_loads_pickle_accepts_blobs_not_str_of_bytes():
    blob = pickle.dumps({"camera": "porch"})
    assert loads_pickle(blob) == {"camera": "porch"}
    assert loads_pickle(blob.decode("latin-1")) == {"camera": "porch"}
    with_str = str(blob)
    assert with_str.startswith("b'")
    try:
        pickle.loads(with_str.encode("utf-8"))
        raised = False
    except Exception:
        raised = True
    assert raised


def test_ensure_unicode_and_utf8():
    assert ensureUnicode("café") == "café"
    assert ensureUnicode("café".encode("utf-8")) == "café"
    assert ensureUtf8("café") == "café".encode("utf-8")
    assert isinstance(ensureUtf8("café"), bytes)


def test_tracking_point_is_integral():
    point = getBboxTrackingPoint((1, 1, 4, 6), "center")
    assert point == (2, 3)
    assert all(isinstance(value, int) for value in point)
