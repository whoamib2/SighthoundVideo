"""ONNX Runtime detector, provider order, and the local HTTP contract."""

import threading
from http.server import HTTPServer

import pytest

from backEnd.native.detector import parse_detection_response
from backEnd.native.onnx_detector import (
    _map_label,
    _yolox_grids,
    select_execution_providers,
)

np = pytest.importorskip("numpy")
pytest.importorskip("onnxruntime")
pytest.importorskip("onnx")

from backEnd.PlatformHTTPWrapper import HandlerFactory  # noqa: E402
from backEnd.native.onnx_detector import OnnxYoloDetector  # noqa: E402
from backEnd.native.test_detector_model import build_bright_blob_model  # noqa: E402
from tests.open_backend_support import Log  # noqa: E402


def test_coco_labels_map_onto_rule_types_and_yolox_nano_grid():
    assert _map_label(0, "coco") == "person"
    assert _map_label(2, "coco") == "vehicle"  # car
    assert _map_label(7, "coco") == "vehicle"  # truck
    assert _map_label(16, "coco") == "animal"  # dog
    assert _map_label(39, "coco") is None  # bottle is not a target type
    assert _map_label(0, "sighthound") == "person"
    assert _map_label(1, "sighthound") == "vehicle"
    assert _map_label(2, "sighthound") == "animal"
    grids, strides = _yolox_grids(3549, 416)
    assert grids is not None and strides is not None
    assert grids.shape == (3549, 2)
    assert strides.shape == (3549, 1)


def test_provider_order_prefers_directml_on_windows_and_cuda_on_linux():
    windows = select_execution_providers(
        ["CPUExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"],
        platform="win32",
        enable_gpu=True,
    )
    assert windows[0] == "DmlExecutionProvider"
    assert windows[-1] == "CPUExecutionProvider"
    linux = select_execution_providers(
        ["CPUExecutionProvider", "CUDAExecutionProvider"],
        platform="linux",
        enable_gpu=True,
    )
    assert linux == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    cpu = select_execution_providers(["CPUExecutionProvider"], platform="win32", enable_gpu=True)
    assert cpu == ["CPUExecutionProvider"]


def test_bright_blob_model_returns_a_person_box(tmp_path):
    model = tmp_path / "blob.onnx"
    build_bright_blob_model(str(model))
    detector = OnnxYoloDetector(Log(), str(model), enable_gpu=False)
    assert "CPUExecutionProvider" in detector.providers
    width, height = 320, 240
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[60:180, 40:100] = 255
    found = detector.detect_rgb(image.tobytes(), width, height)
    assert found
    person = found[0]
    assert person.type == "person"
    assert person.score > 0.5
    assert person.attributes == {}
    box = person.bounding_box
    assert box.x < 80 and box.x + box.width > 60
    assert box.y < 100 and box.y + box.height > 80
    blank = np.zeros((height, width, 3), dtype=np.uint8)
    assert detector.detect_rgb(blank.tobytes(), width, height) == []


def test_http_post_uses_the_onnx_detector(tmp_path):
    model = tmp_path / "blob.onnx"
    build_bright_blob_model(str(model))
    detector = OnnxYoloDetector(Log(), str(model), enable_gpu=False)
    handler = HandlerFactory(Log(), detector)
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        import http.client

        width, height = 320, 240
        image = np.zeros((height, width, 3), dtype=np.uint8)
        image[60:180, 40:100] = 255
        body = image.tobytes()
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
        conn.request(
            "POST",
            "/",
            body=body,
            headers={
                "Content-Length": str(len(body)),
                "X-Width": str(width),
                "X-Height": str(height),
            },
        )
        response = conn.getresponse()
        payload = response.read()
        assert response.status == 200
        detections = parse_detection_response(payload)
        assert detections and detections[0].type == "person"
        assert "attributes" not in detections[0].to_json_object() or detections[0].attributes == {}
    finally:
        server.shutdown()
        thread.join(timeout=5)
