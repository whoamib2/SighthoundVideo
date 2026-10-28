"""Locate bundled images, sounds, and other data files.

The UI opens files such as ``vitaToolbox/wx/bmps/Date_Arrow_Left_Enabled.png``
with a path relative to the repository root. A frozen executable is not
started with that directory as the working directory, and PyInstaller places
``--add-data`` files under ``sys._MEIPASS`` (the executable directory when the
Windows bundle uses ``--contents-directory .``).

``resource_path`` rewrites only relative paths whose first component is a
known resource root. Absolute paths, user files, and camera URLs are left
unchanged, and calling it twice returns the same path.
"""

from __future__ import annotations

import os
import sys

# First path component of a file that ships inside the application tree.
RESOURCE_ROOTS = frozenset({
    "frontEnd",
    "vitaToolbox",
    "appCommon",
    "backEnd",
    "icons",
    "fonts",
    "config",
    "models",
    "launch",
    "xnat",
})

_PIL_PATCHED = False
_WX_PATCHED = False


def application_root():
    """Directory that contains ``frontEnd``, ``icons``, ``config``, and ``models``.

    Frozen PyInstaller builds use ``sys._MEIPASS``. A frozen build without
    that attribute uses the executable directory, or the Mac ``Resources``
    directory next to it when that directory exists. A source checkout uses
    the repository root (the parent of this file), never the process working
    directory.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return meipass
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if sys.platform == "darwin":
            resources = os.path.normpath(os.path.join(exe_dir, "..", "Resources"))
            if os.path.isdir(resources):
                return resources
        return exe_dir
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(here)


def resource_path(path):
    """Return ``path`` joined to :func:`application_root` when it is a bundled file.

    ``path`` may be ``str`` or ``bytes``. Non-path objects are returned as-is
    so file objects passed to :func:`PIL.Image.open` keep working.
    """
    if isinstance(path, (bytes, bytearray)):
        path = path.decode("utf-8")
    if not isinstance(path, str) or path == "":
        return path
    if os.path.isabs(path) or path.startswith(("\\\\", "//")):
        return path
    normalized = path.replace("\\", "/")
    if normalized.startswith(("./", "../", "/")):
        return path
    first = normalized.split("/", 1)[0]
    if first not in RESOURCE_ROOTS:
        return path
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return os.path.normpath(os.path.join(application_root(), *parts))


def model_dir():
    """Directory of ONNX weights for a source checkout or a frozen build.

    ``SV_DEVEL_LIB_FOLDER_CONAN`` still wins when its sibling ``models``
    directory exists. Otherwise the directory is ``models`` under
    :func:`application_root`.
    """
    dev = os.environ.get("SV_DEVEL_LIB_FOLDER_CONAN")
    if dev:
        candidate = os.path.normpath(os.path.join(dev, "..", "models"))
        if os.path.isdir(candidate):
            return candidate
    return os.path.join(application_root(), "models")


def install_resource_hooks():
    """Rewrite bundled paths passed to Pillow and wx constructors.

    ``wx.Bitmap`` and ``wx.Image`` stay subclasses of the originals, so
    ``FromBuffer`` and the other class methods remain available. String
    arguments that are not bundled resource paths are unchanged, which keeps
    ``wx.Bitmap(width, height)`` working.
    """
    _install_pil_hook()
    _install_wx_hook()


def _install_pil_hook():
    global _PIL_PATCHED
    if _PIL_PATCHED:
        return
    try:
        from PIL import Image
    except Exception:
        return
    original = Image.open
    if getattr(original, "_vigilcam_wrapped", False):
        _PIL_PATCHED = True
        return

    def open_resolved(fp, *args, **kwargs):
        if isinstance(fp, (str, bytes, bytearray)):
            fp = resource_path(fp)
        return original(fp, *args, **kwargs)

    open_resolved._vigilcam_wrapped = True
    Image.open = open_resolved
    _PIL_PATCHED = True


def _install_wx_hook():
    global _WX_PATCHED
    if _WX_PATCHED:
        return
    try:
        import wx
        bitmap = wx.Bitmap
        image = wx.Image
    except Exception:
        # wx may be missing, or a test double may not define the classes yet.
        return
    if getattr(wx, "_vigilcam_resource_paths", False):
        _WX_PATCHED = True
        return
    try:
        wx.Bitmap = _path_aware_subclass(bitmap)
        wx.Image = _path_aware_subclass(image)
        if hasattr(wx, "Icon"):
            wx.Icon = _path_aware_subclass(wx.Icon)
        wx._vigilcam_resource_paths = True
    except Exception:
        return
    _WX_PATCHED = True


def _path_aware_subclass(original):
    """Subclass ``original`` and resolve a bundled path in the first argument."""

    class Resolved(original):
        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], (str, bytes, bytearray)):
                args = (resource_path(args[0]),) + args[1:]
            super().__init__(*args, **kwargs)

    Resolved.__name__ = original.__name__
    Resolved.__qualname__ = getattr(original, "__qualname__", original.__name__)
    return Resolved
