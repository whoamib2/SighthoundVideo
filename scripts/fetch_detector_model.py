#!/usr/bin/env python3
"""Download a permissively licensed ONNX detector.

The default model is Megvii YOLOX-Nano (Apache-2.0), about 3.5 MB:

    https://github.com/Megvii-BaseDetection/YOLOX
    https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx

Weights are written under ``models/`` and are gitignored. The app looks for
``models/yolox_nano.onnx`` or the path in ``SIGHTHOUND_DETECTOR_MODEL``.

Ultralytics YOLOv8 and YOLOv10 are AGPL-3.0. This script does not download
them. The runtime can still execute a YOLOv8 ONNX file you supply yourself;
that does not change the license of those weights.

Windows installs ``onnxruntime-directml`` and not the CPU ``onnxruntime``
wheel. Those two distributions own the same package directory. CUDA builds
use ``onnxruntime-gpu`` and also replace that directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

YOLOX_NANO = {
    "name": "yolox-nano",
    "url": "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_nano.onnx",
    "filename": "yolox_nano.onnx",
    "input_size": 416,
    "layout": "yolox",
    "normalize": False,
    "pad": 114,
    "resize": "bilinear",
    "classes": "coco",
    "license": "Apache-2.0",
    "source": "https://github.com/Megvii-BaseDetection/YOLOX",
    "note": (
        "YOLOX-Nano COCO weights from the 0.1.1rc0 release. "
        "Input is 416x416 BGR (channel_order), unnormalized (0-255), gray pad 114. "
        "Person, vehicle, and animal COCO classes are mapped onto the rule engine."
    ),
}


def _destination(filename: str, output: str | None) -> str:
    if output:
        return output
    folder = os.path.join(_ROOT, "models")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, filename)


def fetch(spec: dict, output: str | None = None) -> str:
    path = _destination(spec["filename"], output)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    print("Downloading %s" % spec["url"])
    urllib.request.urlretrieve(spec["url"], path)
    sidecar = {
        "name": spec["name"],
        "layout": spec["layout"],
        "normalize": spec["normalize"],
        "pad": spec["pad"],
        "resize": spec["resize"],
        "classes": spec["classes"],
        "input_size": spec["input_size"],
        "license": spec["license"],
        "source": spec["source"],
        "note": spec["note"],
    }
    with open(path + ".json", "w", encoding="utf-8") as handle:
        json.dump(sidecar, handle, indent=2)
        handle.write("\n")
    print("Wrote %s" % path)
    print("License: %s (%s)" % (spec["license"], spec["source"]))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="yolox-nano",
        choices=("yolox-nano",),
        help="Permissive detector to download (default: yolox-nano, Apache-2.0).",
    )
    parser.add_argument("--output", default=None, help="Destination .onnx path.")
    args = parser.parse_args(argv)
    if args.model != "yolox-nano":
        print("Only yolox-nano is fetched. YOLOv8/v10 weights are AGPL-3.0.", file=sys.stderr)
        return 2
    fetch(YOLOX_NANO, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
