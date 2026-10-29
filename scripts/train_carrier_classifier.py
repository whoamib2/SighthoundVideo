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

from backEnd.native.carrier_features import CARRIER_LABELS
from backEnd.native.carrier_training import export_onnx, fit_carrier_model

__all__ = ["export_onnx", "fit_carrier_model", "main"]

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
