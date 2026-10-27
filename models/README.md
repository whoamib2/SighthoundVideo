# Detector weights

ONNX weights are not part of the git tree. Download the default model with:

```bash
python scripts/fetch_detector_model.py
```

That fetches Megvii YOLOX-Nano (Apache-2.0, about 3.5 MB) to `models/yolox_nano.onnx` and writes a sidecar `models/yolox_nano.onnx.json` describing the input layout. The runtime also honors `SIGHTHOUND_DETECTOR_MODEL`.

COCO labels from that model are mapped to the rule-engine types `person`, `vehicle`, and `animal`.

Ultralytics YOLOv8 and YOLOv10 are AGPL-3.0. The fetch script does not download them. Point `SIGHTHOUND_DETECTOR_MODEL` at your own export if you accept that license. The raw YOLOv8 output layout is recognized.

## Attribute models

`python scripts/fetch_attribute_models.py` downloads these gitignored files:

| File | License | Role |
| --- | --- | --- |
| `plate_ocr_cct_xs.onnx` | MIT (fast-plate-ocr / cnn-ocr-lp) | Latin plate OCR, 64x128 RGB, nine slots |
| `face_detector_yunet.onnx` | Apache-2.0 (OpenCV Zoo YuNet, March 2023) | Face boxes and five landmarks, 640x640 |
| `face_recognition_sface.onnx` | Apache-2.0 (OpenCV Zoo SFace) | 128-D face embedding, 112x112 |

SFace is a MobileFaceNet trained with the SFace loss. It is not an InsightFace ArcFace weight. InsightFace publishes models whose pretrained weights are often limited to non-commercial research. Do not treat one of those files as Apache-2.0 if you set `SIGHTHOUND_FACE_EMBED_MODEL` to it.

No plate detector is downloaded. With only the OCR file, the recognizer reads the lower 45 percent of the vehicle crop. A detector file selected with `SIGHTHOUND_PLATE_DETECTOR_MODEL` should take an RGB image and emit one row `x1, y1, x2, y2, score` in that network's input pixels (output name `plate`, layout `box`). Score below 0.3 is ignored.

No delivery-carrier weights are downloaded. Train `models/carrier.onnx` with `scripts/train_carrier_classifier.py`. The ONNX file is a linear layer on 12 color features (`layout: carrier-linear`, labels `none`, `USPS`, `FedEx`, `UPS`, `Amazon`). That is a starting point for your own photos. It does not read logos, and it has not been checked on real trucks. `SIGHTHOUND_CARRIER_HEURISTIC=0` disables the color guess used when no carrier file is present.

Empty environment variables disable a default file. For example `SIGHTHOUND_FACE_EMBED_MODEL=` skips SFace even if the file is in `models/`.

**Privacy.** Face embeddings and the names they belong to are stored in the local object database only. Deleting a person in the gallery dialog removes the vector. This code does not upload the gallery.

**Accuracy.** CI runs tiny synthetic graphs, not these downloaded weights. YuNet at 640 and SFace at 112 will miss small or profile faces. The default cosine threshold is 0.4 (`attributeSettings.face_threshold`, or `SIGHTHOUND_FACE_THRESHOLD`). Plate text is case-insensitive and spaces are ignored when a rule compares it. A plate rule can use `*` and `?`.

## Windows

* Python 3.11 or 3.12.
* `pip install av numpy onnxruntime pillow`
* CPU inference is the default and is what CI runs.
* DirectML: uninstall `onnxruntime` and install `onnxruntime-directml`. The detector selects `DmlExecutionProvider` when it is present.
* CUDA: install `onnxruntime-gpu` and a matching CUDA driver. `CUDAExecutionProvider` is selected on Windows and Linux when it is present.
* USB cameras: `pip install opencv-python-headless`. DirectShow is used on Windows. RTSP, HTTP, and files use PyAV and do not need OpenCV.
* PyAV wheels include FFmpeg. A separate FFmpeg install is not required for capture.
