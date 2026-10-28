"""Errors raised when a closed native component cannot be used."""

from __future__ import annotations


class NativeDependencyError(ImportError):
    """The legacy closed extension for this component is not available.

    ``svsentry``, ``videoLib2`` and ``SIOWrapper`` shipped as Python 2.7
    binaries and are not part of this source tree. Callers should depend on
    the interfaces in ``backEnd.native`` so a later backend (PyAV/OpenCV
    capture, a pure-Python tracker, an ONNX Runtime detector) can be swapped
    in without touching the rest of the application.
    """

    def __init__(self, component: str, detail: str = "") -> None:
        self.component = component
        message = (
            f"{component} is unavailable. The legacy closed binary is not in "
            "this source tree and no replacement backend is configured."
        )
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)
