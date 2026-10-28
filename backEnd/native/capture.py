"""Capture and decode interface.

The open implementation is PyAV (FFmpeg) plus optional OpenCV for USB
cameras. See :mod:`backEnd.native.open_capture`. The closed ``videoLib2``
extension is loaded only when ``SIGHTHOUND_USE_CLOSED_CAPTURE=1`` and that
module imports. These names stay stable so camera, clip, and debug call
sites do not need to know which backend is loaded.
"""

from __future__ import annotations

import importlib
import os
from typing import Any, Callable

from backEnd.native.errors import NativeDependencyError

_STREAM = "videoLib2.python.StreamReader"
_CLIP_READER = "videoLib2.python.ClipReader"
_CLIP_UTILS = "videoLib2.python.ClipUtils"
_FFMPEG_LOG = "videoLib2.python.ffmpegLog"
_VIDEO_LIB_UTILS = "videoLib2.python.VideoLibUtils"

_modules: dict[str, Any] = {}
_failures: dict[str, BaseException] = {}


def _load(module_name: str) -> Any:
    if module_name in _modules:
        return _modules[module_name]
    if module_name in _failures:
        raise NativeDependencyError(module_name, str(_failures[module_name]))
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        _failures[module_name] = exc
        raise NativeDependencyError(module_name, str(exc)) from exc
    _modules[module_name] = module
    return module


def _use_closed_capture() -> bool:
    return os.environ.get("SIGHTHOUND_USE_CLOSED_CAPTURE") == "1"


def native_capture_available() -> bool:
    """True when PyAV is installed, or the closed extension was requested and loaded."""
    if _use_closed_capture():
        try:
            _load(_STREAM)
        except NativeDependencyError:
            return False
        return True
    from backEnd.native.open_capture import av_available

    return av_available()


def _class(module_name: str, class_name: str) -> type:
    try:
        module = _load(module_name)
    except NativeDependencyError:
        module = None
    if module is not None and hasattr(module, class_name):
        return getattr(module, class_name)

    class Missing:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise NativeDependencyError(f"{module_name}.{class_name}")

        def __getattr__(self, name: str) -> Any:
            raise NativeDependencyError(f"{module_name}.{class_name}")

    Missing.__name__ = class_name
    Missing.__qualname__ = class_name
    return Missing


def _function(module_name: str, name: str) -> Callable[..., Any]:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        module = _load(module_name)
        return getattr(module, name)(*args, **kwargs)

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    return wrapper


def _list_or_empty(module_name: str, name: str) -> Callable[..., Any]:
    """Debug-menu listings. An open build has no videoLib2, so return []."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            module = _load(module_name)
        except NativeDependencyError:
            return []
        return getattr(module, name)(*args, **kwargs)

    wrapper.__name__ = name
    wrapper.__qualname__ = name
    return wrapper


def _constant(module_name: str, name: str, default: Any) -> Any:
    try:
        return getattr(_load(module_name), name)
    except NativeDependencyError:
        return default


class _LazyModule:
    """Attribute access loads ``videoLib2.python.ClipUtils`` on first use."""

    def __init__(self, module_name: str) -> None:
        self._module_name = module_name

    def __getattr__(self, name: str) -> Any:
        return getattr(_load(self._module_name), name)


if _use_closed_capture():
    StreamReader = _class(_STREAM, "StreamReader")
    ClipReader = _class(_CLIP_READER, "ClipReader")
    FFmpegLog = _class(_FFMPEG_LOG, "FFmpegLog")
    getLocalCameraNames = _function(_STREAM, "getLocalCameraNames")
    getHardwareDevicesList = _function(_STREAM, "getHardwareDevicesList")
    kPacketCaptureErrCodes = _constant(_STREAM, "kPacketCaptureErrCodes", {})
    getMsList = _function(_CLIP_READER, "getMsList")
    getDuration = _function(_CLIP_READER, "getDuration")
    clip_utils = _LazyModule(_CLIP_UTILS)
    remuxClip = _function(_CLIP_UTILS, "remuxClip")
    getRealClipSize = _function(_CLIP_UTILS, "getRealClipSize")
    getTimestampFlags = _function(_VIDEO_LIB_UTILS, "getTimestampFlags")
    GetVideolibModulesList = _list_or_empty(_VIDEO_LIB_UTILS, "GetVideolibModulesList")
    GetVideoLibDebugConfigItems = _list_or_empty(_VIDEO_LIB_UTILS, "GetVideoLibDebugConfigItems")
    GetVideoLibLogLevels = _list_or_empty(_VIDEO_LIB_UTILS, "GetVideoLibLogLevels")
    SetVideoLibDebugConfig = _function(_VIDEO_LIB_UTILS, "SetVideoLibDebugConfig")
    SetVideoLibDebugConfigSimple = _function(
        _VIDEO_LIB_UTILS, "SetVideoLibDebugConfigSimple"
    )
else:
    from backEnd.native.open_capture import (
        ClipReader,
        FFmpegLog,
        GetVideoLibDebugConfigItems,
        GetVideoLibLogLevels,
        GetVideolibModulesList,
        SetVideoLibDebugConfig,
        SetVideoLibDebugConfigSimple,
        StreamReader,
        clip_utils,
        getDuration,
        getHardwareDevicesList,
        getLocalCameraNames,
        getMsList,
        getRealClipSize,
        getTimestampFlags,
        kPacketCaptureErrCodes,
        remuxClip,
    )
