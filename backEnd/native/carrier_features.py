"""Color features shared by the carrier heuristic, the runtime, and training.

The vector is 12 floats in ``[0, 1]``: image mean RGB, standard deviation RGB,
top-half mean RGB, and bottom-half mean RGB. Training and inference must use
the same function. This is a small starting point for user photos, not a
measure of real delivery-truck accuracy.
"""

from __future__ import annotations

FEATURE_DIM = 12
# "Garbage" (garbage / refuse truck) is appended last so trained models that
# carry their own label list in metadata keep their logit order.
CARRIER_LABELS = ("none", "USPS", "FedEx", "UPS", "Amazon", "Garbage")


def image_features(rgb):
    """Return a ``[1, 12]`` float32 vector for an ``H x W x 3`` uint8 image."""
    import numpy as np

    image = np.asarray(rgb)
    if image.size == 0 or image.ndim != 3 or image.shape[2] < 3:
        return np.zeros((1, FEATURE_DIM), dtype=np.float32)
    pixels = image[:, :, :3].astype(np.float32) / 255.0
    mean = pixels.mean(axis=(0, 1))
    std = pixels.std(axis=(0, 1))
    height = pixels.shape[0]
    mid = max(1, height // 2)
    top = pixels[:mid].mean(axis=(0, 1))
    bottom = pixels[mid:].mean(axis=(0, 1)) if height > mid else top
    features = np.concatenate([mean, std, top, bottom]).astype(np.float32)
    return features.reshape(1, FEATURE_DIM)


def heuristic_carrier(rgb):
    """Guess a carrier from dominant color, or return ``None``.

    Gray, white, and black crops stay unlabeled. The thresholds are a
    documented starting point for trucks that are mostly one brand color.
    """
    import numpy as np

    image = np.asarray(rgb)
    if image.size == 0 or image.ndim != 3 or image.shape[2] < 3:
        return None
    pixels = image[:, :, :3].astype(np.float32)
    red, green, blue = [float(value) for value in pixels.mean(axis=(0, 1))]
    if max(red, green, blue) - min(red, green, blue) < 25.0:
        return None
    scores = {}
    if blue > 80.0 and blue > red + 30.0 and blue > green + 15.0:
        scores["USPS"] = (blue - max(red, green)) / 255.0
    if red > 80.0 and green > 40.0 and blue < 100.0 and red > blue + 25.0 and red < green + 90.0:
        scores["UPS"] = (red - blue) / 255.0
    if red > 70.0 and blue > 70.0 and green + 30.0 < min(red, blue):
        scores["FedEx"] = (min(red, blue) - green) / 255.0
    if red > 150.0 and 50.0 < green < 170.0 and blue < 100.0 and red > green + 40.0:
        scores["Amazon"] = (red - blue) / 255.0
    if _looks_like_garbage_truck(pixels, red, green, blue):
        scores["Garbage"] = (green - max(red, blue)) / 255.0 + 0.1
    if not scores:
        return None
    name = max(scores, key=scores.get)
    confidence = float(min(0.99, max(0.2, scores[name])))
    return name, confidence


def _looks_like_garbage_truck(pixels, red, green, blue):
    """Green, boxy refuse truck (Waste Management / Republic style).

    COCO has no garbage-truck class, so this runs on vehicle crops like the
    carrier rules: the crop must be green-dominant overall and boxy (height at
    least 45% of width, so a long low green car is less likely to match), and
    the top half must not be much darker than the bottom (a tall hopper body
    rather than a dark cab roofline). White or gray refuse trucks are left
    unlabeled; train ``scripts/train_carrier_classifier.py`` with a
    ``Garbage`` folder for those.
    """
    height, width = pixels.shape[0], pixels.shape[1]
    if width <= 0 or height < 0.45 * width:
        return False
    if not (green > 70.0 and green > red + 20.0 and green > blue + 10.0):
        return False
    mid = max(1, height // 2)
    top = float(pixels[:mid, :, 1].mean())
    bottom = float(pixels[mid:, :, 1].mean()) if height > mid else top
    return top + 40.0 >= bottom
