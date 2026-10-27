"""Closed binaries stay behind adapters and do not break import."""

import pytest

from backEnd.native.capture import StreamReader, native_capture_available
from backEnd.native.errors import NativeDependencyError
from backEnd.native.tracking import native_tracking_available


def test_capture_adapter_imports_without_the_binary():
    assert native_capture_available() is False
    with pytest.raises(NativeDependencyError):
        StreamReader()


def test_pipeline_modules_import_and_refuse_to_run_without_sentry():
    assert native_tracking_available() is False
    import backEnd.QueuedDataManagerCloud
    import backEnd.VideoPipeline

    with pytest.raises(NativeDependencyError):
        backEnd.VideoPipeline.VideoPipeline("clip", object())
