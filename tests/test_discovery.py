"""UPnP and ONVIF discovery must survive Python 3 sockets and bad interfaces.

Ben's backend crashed every few seconds because a search packet was a str,
and ONVIF logged WinError 10049 once per adapter. These cases run the same
constructors the backend main loop uses.
"""

from __future__ import annotations

import logging
import os
import sys

import pytest


def test_upnp_search_packet_is_bytes():
    from vitaToolbox.networking.Upnp import ControlPointManager, _datagram, _kSearchRequest

    packet = _datagram(_kSearchRequest % {"searchTarget": "ssdp:all"})
    assert isinstance(packet, bytes)
    assert packet.startswith(b"M-SEARCH")

    sent = []

    class _Sock:
        def sendto(self, data, addr):
            sent.append((data, addr))

    manager = ControlPointManager.__new__(ControlPointManager)
    manager._requestSocks = {"127.0.0.1": _Sock()}
    manager._searchCountDict = {}
    manager._deviceDict = {}
    manager._pendingDeviceDict = {}
    manager.activeSearch("ssdp:all")
    assert sent
    assert isinstance(sent[0][0], bytes)
    assert b"ssdp:all" in sent[0][0]


def test_control_point_manager_starts_on_loopback(monkeypatch):
    from vitaToolbox.networking.Upnp import ControlPointManager

    monkeypatch.setattr(
        "vitaToolbox.networking.Upnp.ipv4_addresses",
        lambda: [
            {"addr": "0.0.0.0", "netmask": "0.0.0.0"},
            {"addr": b"169.254.1.1", "netmask": "255.255.0.0"},
            {"addr": "127.0.0.1", "netmask": "255.0.0.0"},
            {"addr": "not-an-ip", "netmask": None},
        ],
    )
    logger = logging.getLogger("upnp-test")
    manager = ControlPointManager(logger)
    try:
        changed, gone = manager.pollForChanges()
        assert changed == set()
        assert gone == set()
        manager.activeSearch()
    finally:
        for sock in list(manager._requestSocks.values()):
            sock.close()
        if manager._passiveSock is not None:
            manager._passiveSock.close()


def test_onvif_skips_bad_interfaces_once():
    from vitaToolbox.networking import Onvif

    Onvif._multicast_skip_logged.clear()
    records = []

    class _Logger:
        def warning(self, msg, *args, **_kwargs):
            records.append(msg % args if args else msg)

        def debug(self, *_args, **_kwargs):
            pass

        def error(self, *_args, **_kwargs):
            pass

        def info(self, *_args, **_kwargs):
            pass

    logger = _Logger()
    for ip in ("0.0.0.0", b"0.0.0.0", "169.254.4.4", "255.255.255.255", "nope"):
        assert Onvif.open_multicast_socket(ip, logger) is None
    assert Onvif.open_multicast_socket("0.0.0.0", logger) is None
    logged = [line for line in records if "0.0.0.0" in line or "169.254.4.4" in line]
    # str and bytes forms of the same address share one log line.
    assert len([line for line in logged if "0.0.0.0" in line]) == 1
    assert len([line for line in logged if "169.254.4.4" in line]) == 1


def test_onvif_discovery_runs_against_bad_and_loopback(monkeypatch):
    from vitaToolbox.networking.Onvif import OnvifDiscovery

    monkeypatch.setattr(
        "vitaToolbox.networking.Onvif.ipv4_addresses",
        lambda: [
            {"addr": "0.0.0.0", "netmask": "0.0.0.0"},
            {"addr": "169.254.1.1", "netmask": "255.255.0.0"},
            {"addr": "127.0.0.1", "netmask": "255.0.0.0"},
        ],
    )

    class _Manager:
        def __init__(self):
            self._deviceSettings = {}
            self.done = False

        def onDiscoveryDone(self, devices):
            self.done = True

        def deviceBlacklisted(self, _endpoint):
            return False

        def blacklistDevice(self, _endpoint):
            pass

        def onDeviceDiscovered(self, _state):
            pass

    logger = logging.getLogger("onvif-discovery-test")
    manager = _Manager()
    discovery = OnvifDiscovery(
        logger, timeout=0.05, manager=manager, generation=1,
        numProbes=1, probeDelay=0.01,
    )
    discovery.run()
    assert manager.done


def test_restart_delay_backs_off_and_caps():
    from backEnd.BackEndApp import next_restart_delay

    delay = 0
    seen = []
    for _ in range(8):
        delay = next_restart_delay(delay)
        seen.append(delay)
    assert seen[0] == 2
    assert seen[1] == 4
    assert seen[-1] == 30
    assert next_restart_delay(30) == 30


def test_backend_main_loop_sleeps_after_a_crash(monkeypatch, tmp_path):
    from backEnd import BackEndApp as backend

    sleeps = []
    state = {"runs": 0}

    class _App:
        def __init__(self, _user_dir):
            self.wantQuit = False

        def run(self):
            state["runs"] += 1
            if state["runs"] == 1:
                raise TypeError("a bytes-like object is required, not str")
            self.wantQuit = True

        def cleanup(self):
            pass

    monkeypatch.setattr(backend, "BackEndApp", _App)
    monkeypatch.setattr(backend.time, "sleep", lambda seconds: sleeps.append(seconds))
    backend.main(str(tmp_path))
    assert state["runs"] == 2
    assert sleeps == [2]


def test_backend_main_loop_stops_on_interrupt(monkeypatch, tmp_path):
    from backEnd import BackEndApp as backend

    sleeps = []
    state = {"runs": 0}

    class _App:
        def __init__(self, _user_dir):
            self.wantQuit = False

        def run(self):
            state["runs"] += 1
            raise KeyboardInterrupt()

        def cleanup(self):
            pass

    monkeypatch.setattr(backend, "BackEndApp", _App)
    monkeypatch.setattr(backend.time, "sleep", lambda seconds: sleeps.append(seconds))
    backend.main(str(tmp_path))
    assert state["runs"] == 1
    assert sleeps == []


def test_analytics_does_not_require_intel_redist(monkeypatch):
    from backEnd import BackEndProcessJumper as jumper

    seen = {}

    class _Process:
        def __init__(self, target=None, args=()):
            seen["path"] = os.environ.get("PATH", "")
            seen["target"] = target
            seen["args"] = args

        def start(self):
            seen["started"] = True

    monkeypatch.setattr(jumper, "Process", _Process)
    monkeypatch.delenv("INTEL_DEV_REDIST", raising=False)
    original = os.environ.get("PATH")
    proc = jumper.startPlatformHTTPWrapper("analytics")
    proc.start()
    assert seen["started"] is True
    assert os.environ.get("PATH") == original

    if sys.platform != "darwin":
        intel = "/opt/intel"
        expected = os.path.normpath(
            os.path.join(intel, "redist", "intel64", "compiler")
        )
        monkeypatch.setenv("INTEL_DEV_REDIST", intel)
        jumper.startPlatformHTTPWrapper("analytics").start()
        head = seen["path"].split(os.pathsep, 1)[0]
        assert os.path.normpath(head) == expected
        assert os.environ.get("PATH") == original


def test_upnp_url_round_trip_is_text():
    from vitaToolbox.networking.Upnp import constructUpnpUrl, extractUsnFromUpnpUrl

    url = constructUpnpUrl(
        "uuid:camera-1", username="user", password="p@ss",
        pathPart="/stream", port=554,
    )
    assert isinstance(url, str)
    assert extractUsnFromUpnpUrl(url) == "uuid:camera-1"
    assert extractUsnFromUpnpUrl(url.encode("utf-8")) == "uuid:camera-1"
    old = "upnp://user:pass@uuid%3Acamera-1/stream"
    assert extractUsnFromUpnpUrl(old) == "uuid:camera-1"


def test_missing_port_file_says_backend_is_starting(tmp_path):
    from frontEnd.BackEndClient import BackendNotReady, _BackEndTransport

    missing = tmp_path / "port"
    with pytest.raises(BackendNotReady) as caught:
        _BackEndTransport(str(missing))
    assert "still starting" in str(caught.value)
