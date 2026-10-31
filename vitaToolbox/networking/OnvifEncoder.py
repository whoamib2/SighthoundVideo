"""ONVIF Media (ver10) video encoder configuration: read and change bitrate,
frame rate, resolution and codec of a camera's main/sub stream.

Uses the same WS-UsernameToken header as discovery and falls back to HTTP
Digest on 401 (Dahua/Amcrest). Credentials are never logged.
"""

import urllib.parse
from xml.dom import minidom

from vitaToolbox.networking.Onvif import (
    _addSecurityHeader,
    _kMediaServiceFallbackPaths,
    _kMediaServicePath,
    _kOnvifMedia,
    _kOnvifSchema,
    _kWsSoapEnv,
    digest_post,
)

_kActionGet = "%s/GetVideoEncoderConfigurations" % _kOnvifMedia
_kActionSet = "%s/SetVideoEncoderConfiguration" % _kOnvifMedia
_kTimeout = 8


class OnvifEncoderError(Exception):
    """A user-presentable failure reading or writing encoder settings."""


def _envelope():
    doc = minidom.getDOMImplementation().createDocument(_kWsSoapEnv, "s:Envelope", None)
    env = doc.documentElement
    env.setAttribute("xmlns:s", _kWsSoapEnv)
    env.setAttribute("xmlns:trt", _kOnvifMedia)
    env.setAttribute("xmlns:tt", _kOnvifSchema)
    body = doc.createElementNS(_kWsSoapEnv, "s:Body")
    env.appendChild(body)
    return doc, body


def build_get_request():
    doc, body = _envelope()
    body.appendChild(doc.createElementNS(_kOnvifMedia, "trt:GetVideoEncoderConfigurations"))
    return doc


def _child(el, name):
    if el is None:
        return None
    for node in el.childNodes:
        if node.nodeType == node.ELEMENT_NODE and node.localName == name:
            return node
    return None


def _text(el, name, default=None):
    node = _child(el, name)
    if node is None or node.firstChild is None:
        return default
    return node.firstChild.nodeValue.strip()


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _standalone_xml(el):
    """Serialize el with the namespace prefixes it uses declared on itself."""
    clone = el.cloneNode(True)
    stack = [clone]
    while stack:
        node = stack.pop()
        if node.nodeType != node.ELEMENT_NODE:
            continue
        if node.prefix and node.namespaceURI and not clone.hasAttribute("xmlns:" + node.prefix):
            clone.setAttribute("xmlns:" + node.prefix, node.namespaceURI)
        for attr in list(node.attributes.values()):
            if attr.prefix and attr.prefix != "xmlns" and attr.namespaceURI \
                    and not clone.hasAttribute("xmlns:" + attr.prefix):
                clone.setAttribute("xmlns:" + attr.prefix, attr.namespaceURI)
        stack.extend(node.childNodes)
    return clone.toxml()


def _config_from_element(el):
    res = _child(el, "Resolution")
    rate = _child(el, "RateControl")
    encoding = _text(el, "Encoding", "")
    codec_el = _child(el, {"H264": "H264", "MPEG4": "MPEG4"}.get(encoding.upper(), "H264"))
    return {
        "token": el.getAttribute("token"),
        "name": _text(el, "Name", ""),
        "encoding": encoding,
        "width": _int(_text(res, "Width")),
        "height": _int(_text(res, "Height")),
        "quality": _text(el, "Quality"),
        "fps": _int(_text(rate, "FrameRateLimit")),
        "bitrate_kbps": _int(_text(rate, "BitrateLimit")),
        "gov": _int(_text(codec_el, "GovLength")),
        "profile": _text(codec_el, "H264Profile"),
        "xml": _standalone_xml(el),
    }


def fault_text(data):
    """Short reason from a SOAP Fault, or ''."""
    try:
        doc = minidom.parseString(data)
    except Exception:
        return ""
    texts = [n.firstChild.nodeValue.strip() for n in doc.getElementsByTagNameNS("*", "Text") if n.firstChild]
    codes = [n.firstChild.nodeValue.strip() for n in doc.getElementsByTagNameNS("*", "Value") if n.firstChild]
    return "; ".join(t for t in texts + codes[-1:] if t)


def parse_get_response(data):
    doc = minidom.parseString(data)
    configs = [_config_from_element(el) for el in doc.getElementsByTagNameNS("*", "Configurations")]
    if not configs and doc.getElementsByTagNameNS("*", "Fault"):
        raise OnvifEncoderError("Camera refused: %s" % (fault_text(data) or "SOAP fault"))
    return configs


def _set_child_text(doc, el, name, value):
    node = _child(el, name)
    if node is None:
        node = doc.createElementNS(_kOnvifSchema, "tt:%s" % name)
        el.appendChild(node)
    while node.firstChild:
        node.removeChild(node.firstChild)
    node.appendChild(doc.createTextNode(str(value)))


def build_set_request(config, bitrate_kbps=None, fps=None, width=None, height=None):
    """SetVideoEncoderConfiguration carrying the camera's own configuration
    (from GetVideoEncoderConfigurations) with only the requested fields changed."""
    doc, body = _envelope()
    setEl = doc.createElementNS(_kOnvifMedia, "trt:SetVideoEncoderConfiguration")
    body.appendChild(setEl)
    src = minidom.parseString(config["xml"]).documentElement
    cfg = doc.importNode(src, True)
    cfg.tagName = cfg.nodeName = "trt:Configuration"
    setEl.appendChild(cfg)
    rate = _child(cfg, "RateControl")
    if rate is None and (bitrate_kbps or fps):
        rate = doc.createElementNS(_kOnvifSchema, "tt:RateControl")
        cfg.appendChild(rate)
    if bitrate_kbps:
        _set_child_text(doc, rate, "BitrateLimit", int(bitrate_kbps))
    if fps:
        _set_child_text(doc, rate, "FrameRateLimit", int(fps))
    if width and height:
        res = _child(cfg, "Resolution")
        _set_child_text(doc, res, "Width", int(width))
        _set_child_text(doc, res, "Height", int(height))
    persist = doc.createElementNS(_kOnvifMedia, "trt:ForcePersistence")
    persist.appendChild(doc.createTextNode("true"))
    setEl.appendChild(persist)
    return doc


def onvif_target(device, cameraUri):
    """(host, port, (user, password)) for an ONVIF camera from the device
    found by discovery and the camera's stored ONVIF URL."""
    if not device or not device.validOnvifIpAddrs:
        raise OnvifEncoderError("The camera is not currently reachable over ONVIF.")
    host, port = device.validOnvifIpAddrs[-1][:2]
    split = urllib.parse.urlsplit(cameraUri)
    if not split.username:
        raise OnvifEncoderError("No ONVIF user name is stored for this camera.")
    creds = (urllib.parse.unquote(split.username), urllib.parse.unquote(split.password or ""))
    return host, int(port), creds


def _post(host, port, doc, action, credentials, timeOffset=0):
    headers = {"Content-Type": 'application/soap+xml; charset=utf-8; action="%s"' % action}
    last = (None, "no response")
    for path in [_kMediaServicePath] + list(_kMediaServiceFallbackPaths):
        secured = _addSecurityHeader(doc.cloneNode(True), credentials, timeOffset)
        body = secured.toxml("UTF-8")
        url = "http://%s:%d/%s" % (host, port, path)
        status, data = _plain_post(url, body, headers)
        if status == 401:
            status, data = digest_post(url, body, headers, credentials[0], credentials[1], _kTimeout)
        last = (status, data)
        if status in (404, 503):
            continue
        return last
    return last


def _plain_post(url, body, headers):
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=_kTimeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except Exception as exc:
        return None, str(exc)


def _explain(op, status, data):
    if status is None:
        return "%s: could not reach the camera (%s)." % (op, data)
    reason = fault_text(data) if isinstance(data, (bytes, bytearray)) else ""
    if status == 401 or "NotAuthorized" in reason:
        return "%s: the camera rejected the stored user name/password (HTTP %s). " \
               "The account may need administrator/operator rights." % (op, status)
    return "%s failed (HTTP %s)%s." % (op, status, (": " + reason) if reason else "")


def get_encoder_configs(host, port, credentials):
    status, data = _post(host, port, build_get_request(), _kActionGet, credentials)
    if status != 200:
        raise OnvifEncoderError(_explain("Reading encoder settings", status, data))
    return parse_get_response(data)


def set_encoder_config(host, port, credentials, config, **changes):
    status, data = _post(host, port, build_set_request(config, **changes), _kActionSet, credentials)
    if status != 200:
        raise OnvifEncoderError(_explain("Changing encoder settings", status, data))
    return True
