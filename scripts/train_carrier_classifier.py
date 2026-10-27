#!/usr/bin/env python3
"""Train a small delivery-carrier ONNX model from labeled photos.

There is no permissive pretrained USPS/FedEx/UPS/Amazon model shipped with
this tree. This script fits a nearest-mean linear classifier on color
features and writes ``models/carrier.onnx``.

Lay the photos out as folders named exactly::

    dataset/
        none/
        USPS/
        FedEx/
        UPS/
        Amazon/

``none`` is other vehicles and empty scenes. A missing folder is allowed.
That class gets a large negative bias so it does not win. At least two
folders must contain images. JPEG and PNG files are read with Pillow.

The feature vector is the one in ``backEnd.native.carrier_features``:
mean RGB, standard deviation RGB, top-half mean, bottom-half mean, on a
64x64 resize. A real truck fleet will need more than a handful of photos.
This model does not see logos as text. Treat the result as unverified
until you check it on your own cameras.

Example::

    python scripts/train_carrier_classifier.py --data dataset --output models/carrier.onnx
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backEnd.native.carrier_features import CARRIER_LABELS, FEATURE_DIM, image_features

_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def _list_images(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    found = []
    for name in sorted(os.listdir(folder)):
        if name.lower().endswith(_IMAGE_SUFFIXES):
            found.append(os.path.join(folder, name))
    return found


def _load_feature(path: str):
    import numpy as np
    from PIL import Image

    image = Image.open(path).convert("RGB")
    image = image.resize((64, 64), Image.Resampling.BILINEAR)
    return image_features(np.asarray(image))


def fit_carrier_model(data_dir: str):
    """Return ``(weight [12, 5], bias [5], counts)``."""
    import numpy as np

    means = []
    counts = {}
    for label in CARRIER_LABELS:
        paths = _list_images(os.path.join(data_dir, label))
        counts[label] = len(paths)
        if not paths:
            means.append(None)
            continue
        rows = [_load_feature(path).reshape(-1) for path in paths]
        means.append(np.mean(np.stack(rows, axis=0), axis=0))
    used = sum(1 for item in means if item is not None)
    if used < 2:
        raise SystemExit(
            "Need photos in at least two of %s under %s" % (", ".join(CARRIER_LABELS), data_dir)
        )
    weight = np.zeros((FEATURE_DIM, len(CARRIER_LABELS)), dtype=np.float32)
    bias = np.zeros((len(CARRIER_LABELS),), dtype=np.float32)
    for index, mean in enumerate(means):
        if mean is None:
            bias[index] = -1000.0
            continue
        weight[:, index] = (2.0 * mean).astype(np.float32)
        bias[index] = -float(np.dot(mean, mean))
    return weight, bias, counts


def export_onnx(path: str, weight, bias) -> str:
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    weight = np.asarray(weight, dtype=np.float32)
    bias = np.asarray(bias, dtype=np.float32)
    nodes = [helper.make_node("Gemm", ["features", "W", "B"], ["logits"])]
    graph = helper.make_graph(
        nodes,
        "sighthound_carrier_linear",
        [helper.make_tensor_value_info("features", TensorProto.FLOAT, [1, FEATURE_DIM])],
        [helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, len(CARRIER_LABELS)])],
        [
            numpy_helper.from_array(weight, "W"),
            numpy_helper.from_array(bias, "B"),
        ],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 11)])
    model.ir_version = 8
    prop = model.metadata_props.add()
    prop.key = "sighthound_layout"
    prop.value = "carrier-linear"
    onnx.checker.check_model(model)
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    onnx.save(model, path)
    sidecar = {
        "layout": "carrier-linear",
        "labels": list(CARRIER_LABELS),
        "feature_dim": FEATURE_DIM,
        "license": "trained by the user on local images; no third-party weights",
        "note": (
            "Nearest-mean color classifier. Accuracy on real trucks is not "
            "implied by a successful export. Add more photos and check the "
            "cameras you care about."
        ),
    }
    with open(path + ".json", "w", encoding="utf-8") as handle:
        json.dump(sidecar, handle, indent=2)
        handle.write("\n")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="Folder of none/USPS/FedEx/UPS/Amazon")
    parser.add_argument("--output", default=os.path.join(_ROOT, "models", "carrier.onnx"))
    args = parser.parse_args()
    weight, bias, counts = fit_carrier_model(args.data)
    export_onnx(args.output, weight, bias)
    print("Wrote %s" % args.output)
    for label in CARRIER_LABELS:
        print("  %s: %d image(s)" % (label, counts[label]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
