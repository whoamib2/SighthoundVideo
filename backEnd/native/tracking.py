"""Tracking-pipeline interface.

The historical implementation is the closed ``svsentry`` extension. This
module is the only place that imports it. ``pipeline_base()`` and
``object_collector_base()`` return the native classes when that extension
imports, and plain ``object`` otherwise, so the Python pipeline modules can
still be imported on a machine that does not have the binary.

A later tracker can replace ``pipeline_base`` without changing
``VideoPipeline`` call sites.
"""

from __future__ import annotations

import importlib
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


def native_tracking_available() -> bool:
    return _load() is not None


def ensure_sentry_loaded() -> None:
    """Load the native sentry library once. Raises if it is not installed."""
    global _sentry_loaded
    module = _load()
    if module is None:
        detail = str(_import_error) if _import_error else ""
        raise NativeDependencyError("svsentry", detail)
    if not _sentry_loaded:
        from vitaToolbox.ctypesUtils.LoadLibrary import LoadLibrary

        module.loadSentry(LoadLibrary, None)
        _sentry_loaded = True


def pipeline_base() -> type:
    """Base class for :class:`backEnd.VideoPipeline.VideoPipeline`."""
    module = _load()
    if module is None:
        return object
    ensure_sentry_loaded()
    return module.VideoPipeline


def object_collector_base() -> type:
    """Base class for the queued cloud object collector."""
    module = _load()
    if module is None:
        return object
    ensure_sentry_loaded()
    return module.ObjectCollector


# Default argument for VideoPipeline. Resolved at import; a missing svsentry
# leaves a placeholder string and does not load a native library.
_loaded_module = _load()
kDefaultPipelineConfigFile = (
    _loaded_module.kDefaultPipelineConfigFile
    if _loaded_module is not None and hasattr(_loaded_module, "kDefaultPipelineConfigFile")
    else "pipeline.cfg"
)
