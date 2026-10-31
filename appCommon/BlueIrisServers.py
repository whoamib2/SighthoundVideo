"""Saved Blue Iris servers for the Add Camera wizard.

Stores host, port, user name, stream type and the password. The password is
encrypted for the current Windows user with DPAPI (CryptProtectData) and is
never written in plain text or logged. Cameras added from Blue Iris keep the
server id in their extras (``blueIrisServer``) so a password change can
rebuild their stream URLs.
"""

import base64
import ctypes
import json
import os
import sys
import time
import urllib.parse

from appCommon import BlueIris

_kFileName = "blueiris_servers.json"
_kEntropy = b"VigilCam Blue Iris"
EXTRA_SERVER = "blueIrisServer"
EXTRA_SHORT_NAME = "blueIrisShortName"
EXTRA_STREAM = "blueIrisStream"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _dpapi(data, protect):
    if sys.platform != "win32":
        raise OSError("DPAPI is only available on Windows")
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    inBlob, _keep1 = _blob(data)
    entropy, _keep2 = _blob(_kEntropy)
    out = _Blob()
    func = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = func(ctypes.byref(inBlob), None, ctypes.byref(entropy), None, None,
              0x1, ctypes.byref(out))  # CRYPTPROTECT_UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI call failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


def protect(text):
    return base64.b64encode(_dpapi(text.encode("utf-8"), True)).decode("ascii")


def unprotect(token):
    return _dpapi(base64.b64decode(token), False).decode("utf-8")


def default_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "VigilCam", _kFileName)


def server_id(host, port):
    return "%s:%d" % ((host or "").strip().lower(), int(port))


def load_servers(path=None):
    """Saved servers, most recently used first (no passwords)."""
    try:
        with open(path or default_path(), encoding="utf-8") as handle:
            servers = json.load(handle).get("servers", [])
    except (OSError, ValueError):
        servers = []
    return sorted(servers, key=lambda s: s.get("last_used", 0), reverse=True)


def _write(servers, path=None):
    path = path or default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump({"servers": servers}, handle, indent=1)
    os.replace(tmp, path)


def get_server(sid, path=None):
    for server in load_servers(path):
        if server.get("id") == sid:
            return server
    return None


def get_password(sid, path=None):
    server = get_server(sid, path)
    if not server or not server.get("password"):
        return ""
    try:
        return unprotect(server["password"])
    except Exception:
        return ""


def save_server(host, port, user, password, stream, path=None):
    """Save after a successful connect. Returns (id, passwordChanged)."""
    sid = server_id(host, port)
    servers = [s for s in load_servers(path) if s.get("id") != sid]
    changed = bool(get_server(sid, path)) and get_password(sid, path) != (password or "")
    servers.insert(0, {"id": sid, "host": host.strip(), "port": int(port), "user": user or "",
                       "stream": stream, "password": protect(password) if password else "",
                       "last_used": time.time()})
    _write(servers, path)
    return sid, changed


def forget_server(sid, path=None):
    _write([s for s in load_servers(path) if s.get("id") != sid], path)


def _shortNameFromUri(uri, host, port):
    try:
        parts = urllib.parse.urlsplit(uri)
        if (parts.hostname or "").lower() != host.strip().lower() or parts.port != int(port):
            return None
        segs = [urllib.parse.unquote(s) for s in parts.path.split("/") if s]
        if parts.scheme == "rtsp" and segs:
            return segs[-1]
        if len(segs) >= 2 and segs[0] == "mjpg":
            return segs[1]
    except (ValueError, TypeError):
        pass
    return None


def added_short_names(cameras, host, port):
    """Short names of Blue Iris cameras already configured from host:port.

    ``cameras`` is a list of (uri, extra) for every configured camera.
    """
    sid, found = server_id(host, port), set()
    for uri, extra in cameras:
        extra = extra or {}
        if extra.get(EXTRA_SERVER) == sid and extra.get(EXTRA_SHORT_NAME):
            found.add(extra[EXTRA_SHORT_NAME])
            continue
        name = _shortNameFromUri(uri or "", host, port)
        if name:
            found.add(name)
    return found


def camera_extras(sid, shortName, stream):
    return {EXTRA_SERVER: sid, EXTRA_SHORT_NAME: shortName, EXTRA_STREAM: stream}


def update_cameras_for_server(backEndClient, sid, path=None):
    """Rebuild stream URLs of cameras that reference ``sid``. Returns count."""
    server = get_server(sid, path)
    if not server:
        return 0
    password = get_password(sid, path)
    count = 0
    for location in backEndClient.getCameraLocations():
        camType, uri, _enabled, extra = backEndClient.getCameraSettings(location)
        extra = dict(extra or {})
        if extra.get(EXTRA_SERVER) != sid or not extra.get(EXTRA_SHORT_NAME):
            continue
        newUri = BlueIris.buildStreamUrl(extra.get(EXTRA_STREAM) or BlueIris.kSchemeRtsp,
                                         server["host"], server["port"], extra[EXTRA_SHORT_NAME],
                                         server.get("user", ""), password)
        if newUri != uri:
            backEndClient.editCamera(location, location, camType, newUri, time.time(), extra)
            count += 1
    return count
