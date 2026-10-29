"""Portrait live-view alignment, HW decode selection/labels, tolerant decode, diagnostics."""
import json
import os
from types import SimpleNamespace

import av
import pytest

from backEnd.native import open_capture as oc
from frontEnd.CameraDiagnosticsDialog import collect_diagnostics


def test_portrait_live_frame_rows_are_4_byte_aligned():
    rgb, w, h = oc._scale_rgb(bytes(1280 * 1706 * 3), 1280, 1706, 480, 360)
    assert (w * 3) % 4 == 0 and w == 268 and h == 360 and len(rgb) == w * h * 3


def test_hw_device_selection(monkeypatch):
    import av.codec.hwaccel as hw

    monkeypatch.setattr(hw, "hwdevices_available", lambda: ["cuda", "d3d11va", "dxva2"])
    monkeypatch.setattr(oc, "_HW_PREFERENCE", ("d3d11va", "dxva2", "cuda", "qsv"))
    assert oc.hw_device_for("auto") == "d3d11va"
    assert oc.hw_device_for("cuda") == "cuda"
    assert oc.hw_device_for("none") is None
    assert oc.hw_device_for("bogus") == "d3d11va"
    assert oc.getHardwareDevicesList() == ["d3d11va", "dxva2", "cuda"]


def test_decoder_label():
    assert oc.decoder_label(SimpleNamespace(is_hwaccel=True), "d3d11va") == "Hardware (D3D11VA)"
    assert oc.decoder_label(SimpleNamespace(is_hwaccel=False), "d3d11va") == "Software"
    assert oc.decoder_label(SimpleNamespace(is_hwaccel=True), None) == "Software"


class _Packet:
    def __init__(self, result):
        self.result = result

    def decode(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _bad():
    return av.error.InvalidDataError(1094995529, "Invalid data")


def test_tolerant_decode_skips_corrupt_packets():
    packets = [_Packet(["f1"]), _Packet(_bad()), _Packet([]), _Packet(["f2"])]
    container = SimpleNamespace(demux=lambda stream: iter(packets))
    assert list(oc._tolerant_decode(container, None)) == ["f1", "f2"]


def test_tolerant_decode_gives_up_on_endless_corruption():
    container = SimpleNamespace(demux=lambda stream: iter([_Packet(_bad())] * 5))
    with pytest.raises(RuntimeError):
        list(oc._tolerant_decode(container, None, max_bad=3))


def test_collect_diagnostics(tmp_path):
    (tmp_path / "live").mkdir()
    (tmp_path / "logs" / "cameras").mkdir(parents=True)
    (tmp_path / "live" / "Front Doorbell.decoder.json").write_text(json.dumps(
        {"decoder": "Hardware (D3D11VA)", "codec": "h264", "width": 1280, "height": 1706,
         "pix_fmt": "nv12", "fps": 15.0, "started": "x"}))
    (tmp_path / "logs" / "cameras" / "Front Doorbell.log").write_text(
        "2026-10-29 10:00:00 Beginning stream open\n2026-10-29 10:01:00 Beginning stream open\n"
        "2026-10-28 10:00:00 Beginning stream open\n")
    (tmp_path / "logs" / "cameras" / "Old.log").write_text("")
    rows = {r["camera"]: r for r in collect_diagnostics(str(tmp_path), today="2026-10-29")}
    assert rows["Front Doorbell"]["decoder"] == "Hardware (D3D11VA)"
    assert rows["Front Doorbell"]["resolution"] == "1280x1706"
    assert rows["Front Doorbell"]["reconnects"] == 1
    assert rows["Old"]["decoder"].startswith("Unknown")
    assert os.path.basename(str(tmp_path))
