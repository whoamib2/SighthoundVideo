# Detector weights

ONNX weights are not part of the git tree. Download the default model with:

```bash
python scripts/fetch_detector_model.py
```

That fetches Megvii YOLOX-Nano (Apache-2.0, about 3.5 MB) to `models/yolox_nano.onnx` and writes a sidecar `models/yolox_nano.onnx.json` describing the input layout. The runtime also honors `SIGHTHOUND_DETECTOR_MODEL`.

COCO labels from that model are mapped to the rule-engine types `person`, `vehicle`, and `animal`.

Ultralytics YOLOv8 and YOLOv10 are AGPL-3.0. The fetch script does not download them. Point `SIGHTHOUND_DETECTOR_MODEL` at your own export if you accept that license. The raw YOLOv8 output layout is recognized.

Plate text, face identity, and delivery carrier are fields on the detection JSON. This phase does not run those recognizers.

## Windows

* Python 3.11 or 3.12.
* `pip install av numpy onnxruntime pillow`
* CPU inference is the default and is what CI runs.
* DirectML: uninstall `onnxruntime` and install `onnxruntime-directml`. The detector selects `DmlExecutionProvider` when it is present.
* CUDA: install `onnxruntime-gpu` and a matching CUDA driver. `CUDAExecutionProvider` is selected on Windows and Linux when it is present.
* USB cameras: `pip install opencv-python-headless`. DirectShow is used on Windows. RTSP, HTTP, and files use PyAV and do not need OpenCV.
* PyAV wheels include FFmpeg. A separate FFmpeg install is not required for capture.
