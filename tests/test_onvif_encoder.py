"""ONVIF encoder get/set (canned XML, no network) and bitrate meter."""

from xml.dom import minidom

import pytest

from backEnd.native.open_capture import _BitrateMeter, gb_per_day
from vitaToolbox.networking import OnvifEncoder as oe

GET_RESPONSE = b"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"
 xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:trt="http://www.onvif.org/ver10/media/wsdl">
<SOAP-ENV:Body><trt:GetVideoEncoderConfigurationsResponse>
<trt:Configurations token="000"><tt:Name>mainStream</tt:Name><tt:UseCount>1</tt:UseCount>
<tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution>
<tt:Quality>4</tt:Quality><tt:RateControl><tt:FrameRateLimit>25</tt:FrameRateLimit>
<tt:EncodingInterval>1</tt:EncodingInterval><tt:BitrateLimit>6144</tt:BitrateLimit></tt:RateControl>
<tt:H264><tt:GovLength>50</tt:GovLength><tt:H264Profile>High</tt:H264Profile></tt:H264>
<tt:Multicast><tt:Address><tt:Type>IPv4</tt:Type><tt:IPv4Address>0.0.0.0</tt:IPv4Address></tt:Address>
<tt:Port>0</tt:Port><tt:TTL>0</tt:TTL><tt:AutoStart>false</tt:AutoStart></tt:Multicast>
<tt:SessionTimeout>PT10S</tt:SessionTimeout></trt:Configurations>
<trt:Configurations token="001"><tt:Name>subStream</tt:Name><tt:Encoding>H264</tt:Encoding>
<tt:Resolution><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:Resolution>
<tt:RateControl><tt:FrameRateLimit>10</tt:FrameRateLimit><tt:BitrateLimit>256</tt:BitrateLimit></tt:RateControl>
</trt:Configurations></trt:GetVideoEncoderConfigurationsResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

FAULT = b"""<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" xmlns:ter="http://www.onvif.org/ver10/error">
<s:Body><s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:InvalidArgVal</s:Value></s:Subcode></s:Code>
<s:Reason><s:Text xml:lang="en">Bitrate out of range</s:Text></s:Reason></s:Fault></s:Body></s:Envelope>"""


def test_parse_configs():
    main, sub = oe.parse_get_response(GET_RESPONSE)
    assert (main["token"], main["encoding"], main["width"], main["height"], main["fps"], main["bitrate_kbps"], main["gov"]) == \
        ("000", "H264", 2560, 1440, 25, 6144, 50)
    assert (sub["name"], sub["width"], sub["bitrate_kbps"], sub["fps"]) == ("subStream", 640, 256, 10)


def test_set_request_changes_only_requested_fields():
    main = oe.parse_get_response(GET_RESPONSE)[0]
    xml = oe.build_set_request(main, bitrate_kbps=3072, fps=15).toxml()
    doc = minidom.parseString(xml)  # namespaces resolve
    cfg = doc.getElementsByTagNameNS(oe._kOnvifMedia, "Configuration")[0]
    assert cfg.getAttribute("token") == "000"
    get = lambda n: doc.getElementsByTagNameNS(oe._kOnvifSchema, n)[0].firstChild.nodeValue
    assert get("BitrateLimit") == "3072" and get("FrameRateLimit") == "15"
    assert get("Width") == "2560" and get("GovLength") == "50" and get("SessionTimeout") == "PT10S"
    assert doc.getElementsByTagNameNS(oe._kOnvifMedia, "ForcePersistence")[0].firstChild.nodeValue == "true"


def test_fault_is_explained(monkeypatch):
    main = oe.parse_get_response(GET_RESPONSE)[0]
    monkeypatch.setattr(oe, "_plain_post", lambda *a: (400, FAULT))
    with pytest.raises(oe.OnvifEncoderError) as err:
        oe.set_encoder_config("10.0.0.5", 80, ("u", "p"), main, bitrate_kbps=99999)
    assert "Bitrate out of range" in str(err.value) and "HTTP 400" in str(err.value)
    monkeypatch.setattr(oe, "_plain_post", lambda *a: (None, "timed out"))
    monkeypatch.setattr(oe, "digest_post", lambda *a: (401, b""))
    with pytest.raises(oe.OnvifEncoderError, match="could not reach"):
        oe.get_encoder_configs("10.0.0.5", 80, ("u", "p"))


def test_unauthorized_tries_digest_then_explains(monkeypatch):
    calls = []
    monkeypatch.setattr(oe, "_plain_post", lambda *a: (401, b""))
    monkeypatch.setattr(oe, "digest_post", lambda *a: calls.append(a[0]) or (401, b""))
    with pytest.raises(oe.OnvifEncoderError, match="rejected the stored user name/password"):
        oe.get_encoder_configs("10.0.0.5", 80, ("u", "p"))
    assert calls == ["http://10.0.0.5:80/onvif/Media"]


def test_onvif_target_reads_creds_from_uri():
    class Dev:
        validOnvifIpAddrs = [("192.168.1.20", 8000)]
    host, port, creds = oe.onvif_target(Dev(), "rtsp://admin:p%40ss@abc.sighthoundonvif/Preview_01_main")
    assert (host, port, creds) == ("192.168.1.20", 8000, ("admin", "p@ss"))
    with pytest.raises(oe.OnvifEncoderError):
        oe.onvif_target(None, "rtsp://x")


def test_bitrate_meter_and_gb_per_day():
    meter = _BitrateMeter(window_s=10)
    assert meter.add(500_000, 0.0) is None
    assert meter.add(750_000, 10.0) == 1000  # 1.25 MB over 10 s = 1000 kbps
    assert round(gb_per_day(1000), 1) == 10.8
