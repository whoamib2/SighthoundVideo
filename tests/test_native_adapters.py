"""Open backends are the default. Closed binaries stay optional."""

import pytest

from backEnd.native.capture import StreamReader, native_capture_available
from backEnd.native.tracking import native_tracking_available


def test_open_capture_is_available_when_pyav_imports():
    pytest.importorskip("av")
    assert native_capture_available() is True
    reader = StreamReader("cam", None, None, ".", ".", ".", None, False)
    assert reader.locationName == "cam"
    assert reader.open("", {}) is False


def test_open_pipeline_constructs_without_svsentry():
    pytest.importorskip("numpy")
    assert native_tracking_available() is True
    import backEnd.VideoPipeline

    collector = type("Collector", (), {})()
    pipeline = backEnd.VideoPipeline.VideoPipeline("clip", collector)
    assert pipeline.objectCollector is collector
    pipeline.flush()
