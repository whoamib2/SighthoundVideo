"""Startup trace for the desktop app.

The installed executable is a windowed process, so a failure before the main
window exists is invisible. Every launch appends to a log under the user
profile, and a fatal error also opens a message box.
"""

from __future__ import annotations

import os
import sys
import time
import traceback


APP_DIR_NAME = "VigilCam"
LEGACY_DIR_NAME = "Sighthound Video"


def user_data_parent() -> str:
    """Folder that contains the per-user VigilCam directory."""
    if sys.platform == "win32":
        return os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )


def migrate_legacy_data_dir(parent: str | None = None) -> str:
    """Rename a previous Sighthound Video data folder to VigilCam once.

    If VigilCam already exists, the old folder is left in place.
    """
    parent = user_data_parent() if parent is None else parent
    new_dir = os.path.join(parent, APP_DIR_NAME)
    old_dir = os.path.join(parent, LEGACY_DIR_NAME)
    if os.path.isdir(old_dir) and not os.path.lexists(new_dir):
        try:
            os.rename(old_dir, new_dir)
        except OSError:
            pass
    return new_dir


def startup_log_dir() -> str:
    """Directory that receives startup.log. On Windows this is LocalAppData."""
    return os.path.join(migrate_legacy_data_dir(), "logs")


def startup_log_path() -> str:
    return os.path.join(startup_log_dir(), "startup.log")


def log_startup(message: str) -> None:
    """Append one timestamped line. Failures to write are ignored."""
    try:
        directory = startup_log_dir()
        os.makedirs(directory, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(startup_log_path(), "a", encoding="utf-8") as handle:
            handle.write("%s %s\n" % (stamp, message.rstrip()))
    except Exception:
        pass


def log_startup_exception(prefix: str) -> None:
    log_startup(prefix + "\n" + traceback.format_exc())


def console_requested(argv=None) -> bool:
    args = list(sys.argv if argv is None else argv)
    if "--console" in args:
        return True
    return os.environ.get("SIGHTHOUND_CONSOLE") == "1"


def enable_windows_console() -> bool:
    """Open a console for a windowed build when --console was passed.

    A PyInstaller --windowed executable has no console of its own. AllocConsole
    creates one so tracebacks are visible. Other platforms already have the
    terminal the process was started from.
    """
    if sys.platform != "win32" or not console_requested():
        return False
    try:
        import ctypes
        kernel = ctypes.windll.kernel32
        if not kernel.GetConsoleWindow():
            if not kernel.AllocConsole():
                return False
        stream_out = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        stream_err = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)
        sys.stdout = stream_out
        sys.stderr = stream_err
        return True
    except Exception:
        log_startup_exception("could not open a console")
        return False


def instance_lock_name(app_name: str, user_id: str) -> str:
    """File-safe single-instance name.

    wx.GetUserId can contain a domain backslash. SingleInstanceChecker then
    fails to create its lock and reports that another copy is running.
    """
    raw = "%s-%s" % (app_name or "VigilCam", user_id or "user")
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in raw)
    return cleaned or "VigilCam"


def show_fatal(message: str) -> None:
    """Record a fatal startup error and show it in a dialog."""
    log_startup("FATAL: " + message)
    if sys.platform == "win32":
        try:
            import ctypes
            # MB_OK | MB_ICONERROR | MB_SETFOREGROUND
            ctypes.windll.user32.MessageBoxW(None, message, "VigilCam", 0x00000010 | 0x00010000)
            return
        except Exception:
            pass
    try:
        import wx
        if wx.GetApp() is None:
            wx.App(False)
        wx.MessageBox(message, "VigilCam", wx.OK | wx.ICON_ERROR)
    except Exception:
        log_startup_exception("could not show the startup error dialog")
