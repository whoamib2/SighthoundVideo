import hashlib
import ipaddress

import pytest

from appCommon import BlueIris as bi


def test_urls():
    assert bi.buildRtspUrl("192.168.1.5", 81, "Front") == "rtsp://192.168.1.5:81/Front"
    assert bi.buildMjpegUrl("bi.local", 81, "Front") == "http://bi.local:81/mjpg/Front/video.mjpg"
    assert bi.buildRtspUrl("h", 81, "cam1", "ben", "p@ss:w/d") == "rtsp://ben:p%40ss%3Aw%2Fd@h:81/cam1"
    assert bi.buildStreamUrl(bi.kSchemeMjpeg, "h", 80, "c", "u", "") == "http://u@h:80/mjpg/c/video.mjpg"
    with pytest.raises(ValueError):
        bi.buildRtspUrl("h", 81, "")
    with pytest.raises(ValueError):
        bi.buildRtspUrl("", 81, "c")


class FakeServer:
    def __init__(self, password="secret", keys=("optionDisplay", "optionValue")):
        self.password, self.keys, self.calls = password, keys, []

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload))
        cmd = payload["cmd"]
        if cmd == "login" and "response" not in payload:
            return {"result": "fail", "session": "S1"}
        if cmd == "login":
            good = hashlib.md5(("ben:S1:" + self.password).encode()).hexdigest()
            if payload["response"] == good:
                return {"result": "success", "session": "S1", "data": {"system name": "BI-PC"}}
            return {"result": "fail", "data": {"reason": "Invalid login"}}
        if cmd == "camlist":
            d, v = self.keys
            return {"result": "success", "data": [
                {d: "All cameras", v: "Index", "group": ["Front", "Back"]},
                {d: "Outside", v: "@Outside", "group": ["Front"]},
                {d: "Front door", v: "Front", "isEnabled": True, "width": 1920, "height": 1080},
                {d: "Back yard", v: "Back", "isEnabled": False},
            ]}
        raise AssertionError(cmd)


@pytest.mark.parametrize("keys", [("optionDisplay", "optionValue"), ("optionsDisplay", "optionsValue")])
def test_login_and_camlist(keys):
    srv = FakeServer(keys=keys)
    c = bi.BlueIrisClient("10.0.0.2", 81, "ben", "secret", post=srv)
    assert c.login() == "BI-PC"
    cams = c.cameras()
    assert [x["shortName"] for x in cams] == ["Front", "Back"]
    assert cams[0]["name"] == "Front door" and cams[0]["width"] == 1920
    assert cams[1]["enabled"] is False
    assert srv.calls[0][0] == "http://10.0.0.2:81/json"


def test_bad_password():
    c = bi.BlueIrisClient("h", 81, "ben", "wrong", post=FakeServer())
    with pytest.raises(bi.BlueIrisError, match="rejected"):
        c.login()


def test_not_blue_iris_and_unreachable():
    c = bi.BlueIrisClient("h", 81, "u", "p", post=lambda *a: {"hello": 1})
    with pytest.raises(bi.BlueIrisError):
        c.login()

    def boom(*a):
        raise OSError("refused")
    with pytest.raises(bi.BlueIrisError, match="Could not reach"):
        bi.BlueIrisClient("h", 81, "u", "p", post=boom).login()


def test_probe_and_discover():
    assert bi.probeServer("h", 81, post=lambda *a: {"result": "fail", "session": "x"})
    assert not bi.probeServer("h", 81, post=lambda *a: {"result": "fail"})
    hits = {("192.168.7.20", 81), ("192.168.7.20", 80), ("192.168.7.3", 80)}
    found = bi.discoverServers(networks=[ipaddress.ip_network("192.168.7.0/27")],
                               probe=lambda h, p, t: (h, p) in hits)
    assert found == [("192.168.7.3", 80), ("192.168.7.20", 80)]
    assert bi.discoverServers(networks=[], probe=lambda h, p, t: False) == []
