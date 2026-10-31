#!/usr/bin/env python3
"""Download permissively licensed plate and face ONNX models.

Weights are written under ``models/`` and are gitignored. Nothing here is
committed. The runtime also honors:

* ``SIGHTHOUND_PLATE_OCR_MODEL``
* ``SIGHTHOUND_PLATE_DETECTOR_MODEL``
* ``SIGHTHOUND_FACE_DETECTOR_MODEL``
* ``SIGHTHOUND_FACE_EMBED_MODEL``
* ``SIGHTHOUND_CARRIER_MODEL``

Licenses, checked against the projects that publish the files:

* Plate OCR: ``cct_xs_v1_global.onnx`` from the MIT-licensed fast-plate-ocr
  project (ankandrew). The weight file is published from the MIT repository
  ``ankandrew/cnn-ocr-lp``. Alphabet and input size are in the sidecar.
* Face detection: OpenCV Zoo YuNet ``face_detection_yunet_2023mar.onnx``,
  Apache-2.0.
* Face embeddings: OpenCV Zoo SFace
  ``face_recognition_sface_2021dec.onnx``, Apache-2.0. This is a
  MobileFaceNet trained with the SFace loss. It is not an InsightFace
  ArcFace pack. Many InsightFace pretrained weights are non-commercial
  only. This script does not download those.

There is no widely published permissively licensed delivery-carrier model.
Train one on your own photos with ``scripts/train_carrier_classifier.py``.
Until that file exists, vehicle crops can use a documented color heuristic
(``SIGHTHOUND_CARRIER_HEURISTIC=0`` turns it off).

Plate localization: if you do not pass ``SIGHTHOUND_PLATE_DETECTOR_MODEL``,
OCR runs on the lower part of the vehicle crop. A detector ONNX is optional.
Its contract is in ``models/README.md``.

These files are not a claim about real-camera accuracy. YuNet is fixed at
640x640. SFace is aligned with the five YuNet landmarks when they exist.
Plate OCR was trained as a general Latin-alphabet model and will misread
dirty, distant, or non-Latin plates.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

_MODELS = {
    "plate-ocr": {
        "url": "https://github.com/ankandrew/cnn-ocr-lp/releases/download/arg-plates/cct_xs_v1_global.onnx",
        "filename": "plate_ocr_cct_xs.onnx",
        "sidecar": {
            "layout": "fast-plate-ocr",
            "alphabet": "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_",
            "pad_char": "_",
            "img_height": 64,
            "img_width": 128,
            "scale": 0.00392156862745098,
            "license": "MIT",
            "source": "https://github.com/ankandrew/fast-plate-ocr",
            "weights": "https://github.com/ankandrew/cnn-ocr-lp",
            "note": (
                "fast-plate-ocr cct-xs-v1 global Latin plates. "
                "Input is NHWC 64x128 RGB scaled by 1/255. "
                "Nine character slots, pad character '_'."
            ),
        },
    },
    "yunet": {
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "filename": "face_detector_yunet.onnx",
        "sidecar": {
            "layout": "yunet",
            "score_threshold": 0.6,
            "nms_threshold": 0.3,
            "license": "Apache-2.0",
            "source": "https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet",
            "note": (
                "OpenCV Zoo YuNet, March 2023, Apache-2.0. "
                "Input is 640x640 RGB in 0-255 NCHW. "
                "InsightFace SCRFD weights are not used; some of those packs are non-commercial."
            ),
        },
    },
    "sface": {
        "url": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "filename": "face_recognition_sface.onnx",
        "sidecar": {
            "layout": "sface",
            "img_height": 112,
            "img_width": 112,
            "scale": 1.0,
            "license": "Apache-2.0",
            "source": "https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface",
            "note": (
                "OpenCV Zoo SFace, Apache-2.0. 112x112 RGB, pixel values 0-255, 128-D embedding. "
                "Match with cosine similarity. The default threshold is 0.4. "
                "This is not an InsightFace ArcFace weight. Do not drop a non-commercial "
                "ArcFace file in this path and treat it as Apache-2.0."
            ),
        },
    },
}


def _destination(filename: str, output: str | None) -> str:
    if output:
        return output
    folder = os.path.join(_ROOT, "models")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename)


def fetch_one(name: str, output: str | None = None) -> str:
    spec = _MODELS[name]
    path = _destination(spec["filename"], output)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    print("Downloading %s" % spec["url"])
    urllib.request.urlretrieve(spec["url"], path)
    with open(path + ".json", "w", encoding="utf-8") as handle:
        json.dump(spec["sidecar"], handle, indent=2)
        handle.write("\n")
    print("Wrote %s" % path)
    print("License: %s" % spec["sidecar"]["license"])
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only",
        choices=sorted(_MODELS),
        action="append",
        help="Download one model. Repeat for several. Default is all three.",
    )
    args = parser.parse_args()
    names = args.only or list(_MODELS)
    for name in names:
        fetch_one(name)
    print(
        "No carrier weights were downloaded. "
        "Train scripts/train_carrier_classifier.py on your own labeled photos."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
