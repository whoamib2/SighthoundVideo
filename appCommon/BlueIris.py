"""Blue Iris server support: stream URLs, the JSON API and LAN discovery.

Formats come from the Blue Iris help ("HTTP interface" and "JSON interface"):

* RTSP (Blue Iris 5.3.1+): ``rtsp://host:port/{short-name}`` on the web
  server port, Digest authentication.
* MJPEG: ``http://host:port/mjpg/{short-name}/video.mjpg``.
* JSON API: POST to ``/json``. ``{"cmd":"login"}`` returns a ``session``;
  send it back with ``response = md5("user:session:password")``; then
  ``{"cmd":"camlist","session":...}`` lists cameras and groups.
"""

import concurrent.futures
import hashlib
import ipaddress
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

kDefaultPorts = (81, 80)
kSchemeRtsp = "rtsp"
kSchemeMjpeg = "mjpeg"


class BlueIrisError(Exception):
    """Login or API failure with a message fit for the user."""


def _hostPort(host, port):
    host = (host or "").strip()
    if not host:
        raise ValueError("Enter the Blue Iris server address.")
    if ":" in host and not host.startswith("["):
        host = "[%s]" % host          # IPv6 literal
    return "%s:%d" % (host, int(port))


def _userInfo(user, password):
    if not user:
        return ""
    info = urllib.parse.quote(user, safe="")
    if password:
        info += ":" + urllib.parse.quote(password, safe="")
    return info + "@"


def buildRtspUrl(host, port, shortName, user="", password=""):
    shortName = (shortName or "").strip()
    if not shortName:
        raise ValueError("Pick a Blue Iris camera.")
    return "rtsp://%s%s/%s" % (_userInfo(user, password), _hostPort(host, port),
                               urllib.parse.quote(shortName, safe=""))


def buildMjpegUrl(host, port, shortName, user="", password=""):
    shortName = (shortName or "").strip()
    if not shortName:
        raise ValueError("Pick a Blue Iris camera.")
    return "http://%s%s/mjpg/%s/video.mjpg" % (
        _userInfo(user, password), _hostPort(host, port),
        urllib.parse.quote(shortName, safe=""))


def buildStreamUrl(scheme, host, port, shortName, user="", password=""):
    if scheme == kSchemeMjpeg:
        return buildMjpegUrl(host, port, shortName, user, password)
    return buildRtspUrl(host, port, shortName, user, password)


def loginResponse(user, session, password):
    raw = "%s:%s:%s" % (user, session, password)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _defaultPost(url, payload, timeout):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace") or "{}")


class BlueIrisClient(object):
    """Tiny JSON API client. ``post`` is injectable for tests."""

    def __init__(self, host, port, user, password, timeout=5.0, post=None):
        self.baseUrl = "http://%s/json" % _hostPort(host, port)
        self._user = user or ""
        self._password = password or ""
        self._timeout = timeout
        self._post = post or _defaultPost
        self.session = None
        self.systemName = ""

    def _call(self, payload):
        try:
            return self._post(self.baseUrl, payload, self._timeout)
        except (urllib.error.URLError, socket.timeout, OSError) as e:
            raise BlueIrisError("Could not reach Blue Iris at %s (%s)."
                                % (self.baseUrl[:-5], getattr(e, "reason", e)))
        except ValueError:
            raise BlueIrisError("That server did not answer like Blue Iris.")

    def login(self):
        first = self._call({"cmd": "login"})
        session = first.get("session")
        if not session:
            raise BlueIrisError("That server did not answer like Blue Iris.")
        second = self._call({
            "cmd": "login", "session": session,
            "response": loginResponse(self._user, session, self._password),
        })
        if second.get("result") != "success":
            reason = (second.get("data") or {}).get("reason", "")
            raise BlueIrisError("Blue Iris rejected the user name or password."
                                + ((" (%s)" % reason) if reason else ""))
        self.session = second.get("session", session)
        self.systemName = (second.get("data") or {}).get("system name", "")
        return self.systemName

    def cameras(self):
        if self.session is None:
            self.login()
        reply = self._call({"cmd": "camlist", "session": self.session})
        if reply.get("result") != "success":
            raise BlueIrisError("Blue Iris did not return its camera list.")
        return parseCamList(reply.get("data") or [])


def parseCamList(data):
    """Return cameras (not groups) as dicts: shortName, name, enabled, size."""
    cams = []
    for item in data:
        if not isinstance(item, dict):
            continue
        # The help says optionsValue/optionsDisplay; servers send
        # optionValue/optionDisplay. Accept both.
        short = item.get("optionValue", item.get("optionsValue"))
        name = item.get("optionDisplay", item.get("optionsDisplay")) or short
        if not short or "group" in item:
            continue
        if short.lower() == "index" or short.startswith("@"):
            continue                    # "All cameras" and other groups
        cams.append({
            "shortName": short,
            "name": name,
            "enabled": bool(item.get("isEnabled", True)),
            "online": bool(item.get("isOnline", True)),
            "width": int(item.get("width") or 0),
            "height": int(item.get("height") or 0),
        })
    return cams


def probeServer(host, port, timeout=0.6, post=None):
    """Return True if ``host:port`` answers the Blue Iris JSON login."""
    if post is None:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
        except OSError:
            return False
    try:
        reply = (post or _defaultPost)("http://%s/json" % _hostPort(host, port),
                                       {"cmd": "login"}, max(1.0, timeout * 3))
    except Exception:
        return False
    return isinstance(reply, dict) and bool(reply.get("session"))


def localSubnets():
    """/24 networks of this PC's IPv4 addresses (private ranges only)."""
    addrs = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))      # no packet is sent for UDP connect
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    nets = []
    for a in sorted(addrs):
        ip = ipaddress.ip_address(a)
        if ip.is_private and not ip.is_loopback and not ip.is_link_local:
            net = ipaddress.ip_network("%s/24" % a, strict=False)
            if net not in nets:
                nets.append(net)
    return nets


def discoverServers(networks=None, ports=kDefaultPorts, timeout=0.4,
                    workers=192, probe=probeServer, extraHosts=()):
    """Find Blue Iris servers. Only private /24 subnets are scanned, with a
    short connect timeout and one JSON request per open port.

    @return list of (host, port) sorted by address.
    """
    if networks is None:
        networks = localSubnets()
    hosts = []
    for net in networks:
        hosts.extend(str(h) for h in net.hosts())
    for h in extraHosts:
        if h not in hosts:
            hosts.append(h)
    hosts.append("127.0.0.1")
    found = []
    targets = [(h, p) for h in hosts for p in ports]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe, h, p, timeout): (h, p) for h, p in targets}
        for fut in concurrent.futures.as_completed(futures):
            try:
                if fut.result():
                    found.append(futures[fut])
            except Exception:
                pass
    seen, result = set(), []
    for h, p in sorted(found, key=lambda hp: (_sortKey(hp[0]), hp[1])):
        if h not in seen:               # one entry per server (first port)
            seen.add(h)
            result.append((h, p))
    return result


def _sortKey(host):
    try:
        return (0, int(ipaddress.ip_address(host)))
    except ValueError:
        return (1, host)
