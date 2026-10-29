"""Amcrest/Dahua 'General IPC' auto-config, UPnP/ONVIF merge, ONVIF keep-alive."""
import time
from types import SimpleNamespace

from frontEnd import CameraTable
from frontEnd.CameraSetupWizard import _findGenericCamTypes, onvifIpSet, upnpDeviceIp
from vitaToolbox.networking import Onvif


def test_general_ipc_maps_to_amcrest_realmonitor():
    for model in ("General IPC", "IPC"):
        types = _findGenericCamTypes(model)
        assert "Amcrest / Dahua (other - main stream)" in types
        assert "Amcrest / Dahua (other - sub stream)" in types
    paths = {t: CameraTable.kTypeToStreamPath[t][1] for t in types}
    assert paths["Amcrest / Dahua (other - main stream)"] == "/cam/realmonitor?channel=1&subtype=0"
    assert paths["Amcrest / Dahua (other - sub stream)"] == "/cam/realmonitor?channel=1&subtype=1"


def test_upnp_entry_hidden_when_onvif_has_same_ip():
    onvif = {"u": SimpleNamespace(validOnvifIpAddrs=[("192.168.0.23", 80)])}
    dev = SimpleNamespace(getPresentationUrl=lambda: ("http://192.168.0.23:80/", False))
    other = SimpleNamespace(getPresentationUrl=lambda: ("http://192.168.0.28/", False))
    ips = onvifIpSet(onvif)
    assert upnpDeviceIp(dev) in ips and upnpDeviceIp(other) not in ips


def test_device_kept_while_device_service_answers(monkeypatch):
    monkeypatch.setattr(Onvif, "onvif_alive", lambda h, p, timeout=3: True)
    mgr = Onvif.OnvifDeviceManager.__new__(Onvif.OnvifDeviceManager)
    import threading
    mgr._lock = threading.RLock()
    mgr._deviceStates = {"u": SimpleNamespace(validOnvifIpAddrs=[("192.168.0.23", 80)])}
    mgr._deviceLastSeenTime = {"u": time.time() - 120}
    mgr._removeUuid("u")
    for _ in range(50):
        if time.time() - mgr._deviceLastSeenTime["u"] < 5:
            break
        time.sleep(0.05)
    assert "u" in mgr._deviceStates
    assert time.time() - mgr._deviceLastSeenTime["u"] < 5


def test_username_token_uses_time_offset(monkeypatch):
    from xml.dom import minidom
    doc = minidom.parseString(
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"><s:Header/><s:Body/></s:Envelope>')
    monkeypatch.setattr(Onvif.time, "time", lambda: 1_000_000.0)
    out = Onvif._addSecurityHeader(doc, ("admin", "x"), 3600).toxml()
    assert "1970-01-12T14:46:40Z" in out  # 1_003_600 s
