"""Stream & Bitrate dialog smoke test (no network)."""

import pickle

import pytest

wx = pytest.importorskip("wx")

from frontEnd import CameraEncoderDialog as ced  # noqa: E402
from tests.test_onvif_encoder import GET_RESPONSE  # noqa: E402
from vitaToolbox.networking import OnvifEncoder  # noqa: E402


class _Dev:
    validOnvifIpAddrs = [("10.0.0.9", 80)]


class _Client:
    def __init__(self, uuid):
        self.uuid = uuid

    def getOnvifDevices(self):
        return pickle.dumps({self.uuid: _Dev()})


def test_texts():
    assert "not measured yet" in ced.measured_text({})
    assert "1000 kbps" in ced.measured_text({"measured_kbps": 1000}) and "10.8 GB/day" in ced.measured_text({"measured_kbps": 1000})


def test_dialog_reads_and_reports_write_failure(tmp_path, monkeypatch):
    app = wx.App(False)
    configs = OnvifEncoder.parse_get_response(GET_RESPONSE)
    monkeypatch.setattr(ced, "get_encoder_configs", lambda *a: configs)

    def fail(*a, **k):
        raise OnvifEncoder.OnvifEncoderError("Changing encoder settings failed (HTTP 400): Bitrate out of range.")
    monkeypatch.setattr(ced, "set_encoder_config", fail)
    monkeypatch.setattr(ced.wx, "MessageBox", lambda *a, **k: None)
    from vitaToolbox.networking.Onvif import constructOnvifUrl, extractUuidFromOnvifUrl
    uri = constructOnvifUrl("uuid-1234", "admin", "secret", "rtsp://x/Preview_01_main")
    dlg = ced.CameraEncoderDialog(None, "Drive Cam 12", uri, _Client(extractUuidFromOnvifUrl(uri)), str(tmp_path))
    dlg._done(dlg._read(), None)
    assert dlg._list.GetCount() == 2 and dlg._bitrate.GetValue() == 6144
    try:
        dlg._write(configs[0], 3000, 15)
    except OnvifEncoder.OnvifEncoderError as exc:
        dlg._done(None, str(exc))
    assert "Bitrate out of range" in dlg._status.GetLabel()
    dlg.Destroy()
    blue = ced.CameraEncoderDialog(None, "Front Doorbell", "rtsp://bi:8090/Cam8", None, str(tmp_path))
    assert not blue._isOnvif
    blue.Destroy()
    app.Destroy()
