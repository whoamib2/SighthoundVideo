"""Tracking-pipeline interface.

The default pipeline is the open motion tracker in
:mod:`backEnd.native.open_tracker`. The closed ``svsentry`` extension is
loaded only when ``SIGHTHOUND_USE_CLOSED_SENTRY=1`` and that module imports.
``VideoPipeline`` call sites stay the same either way.
"""

from __future__ import annotations

import importlib
import os
from typing import Any

from backEnd.native.errors import NativeDependencyError

_MODULE_NAME = "svsentry.Sentry"
_module: Any = None
_tried = False
_import_error: BaseException | None = None
_sentry_loaded = False


def _load() -> Any:
    global _module, _tried, _import_error
    if _tried:
        return _module
    _tried = True
    try:
        _module = importlib.import_module(_MODULE_NAME)
    except Exception as exc:
        _module = None
        _import_error = exc
    return _module


def _use_closed_sentry() -> bool:
    return os.environ.get("SIGHTHOUND_USE_CLOSED_SENTRY") == "1"


def _numpy_available() -> bool:
    try:
        import numpy  # noqa: F401
    except Exception:
        return False
    return True


def native_tracking_available() -> bool:
    """True when the open tracker can run, or the closed sentry was requested."""
    if _use_closed_sentry() and _load() is not None:
        return True
    return _numpy_available()


def ensure_sentry_loaded() -> None:
    """Load the closed sentry library when that backend was requested.

    The open tracker does not use ``svsentry``. ``VideoPipeline`` still calls
    this, and that call succeeds without the binary.
    """
    global _sentry_loaded
    if not _use_closed_sentry():
        return
    module = _load()
    if module is None:
        detail = str(_import_error) if _import_error else ""
        raise NativeDependencyError("svsentry", detail)
    if not _sentry_loaded:
        from vitaToolbox.ctypesUtils.LoadLibrary import LoadLibrary

        module.loadSentry(LoadLibrary, None)
        _sentry_loaded = True


def pipeline_base() -> type:
    """Base class for :class:`backEnd.VideoPipeline.VideoPipeline`.

    The open motion tracker is the default. Set ``SIGHTHOUND_USE_CLOSED_SENTRY=1``
    to prefer the closed ``svsentry`` extension when it imports.
    """
    if _use_closed_sentry():
        module = _load()
        if module is not None:
            ensure_sentry_loaded()
            return module.VideoPipeline
    from backEnd.native.open_tracker import OpenVideoPipeline

    return OpenVideoPipeline


def object_collector_base() -> type:
    """Base class for the queued cloud object collector.

    The open tracker calls the Python methods on ``QueuedDataManagerCloud``.
    The closed collector base is used only when that backend was requested.
    """
    if _use_closed_sentry():
        module = _load()
        if module is not None:
            ensure_sentry_loaded()
            return module.ObjectCollector
    return object


# Default argument for VideoPipeline. Resolved at import; a missing svsentry
# leaves a placeholder string and does not load a native library.
_loaded_module = _load()
kDefaultPipelineConfigFile = (
    _loaded_module.kDefaultPipelineConfigFile
    if _loaded_module is not None and hasattr(_loaded_module, "kDefaultPipelineConfigFile")
    else "pipeline.cfg"
)
