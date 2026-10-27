"""Shared helpers for the open capture, tracker, and detector tests."""

from __future__ import annotations

import fractions


class Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def _note(self, level: str, args) -> None:
        if args:
            self.messages.append("%s %s" % (level, args[0]))

    def debug(self, *args, **kwargs) -> None:
        self._note("debug", args)

    def info(self, *args, **kwargs) -> None:
        self._note("info", args)

    def warning(self, *args, **kwargs) -> None:
        self._note("warning", args)

    def warn(self, *args, **kwargs) -> None:
        self._note("warning", args)

    def error(self, *args, **kwargs) -> None:
        self._note("error", args)

    def log(self, *args, **kwargs) -> None:
        self._note("log", args)

    def getCLogFn(self):
        return None


class ClipLog:
    def __init__(self) -> None:
        self.clips: list[tuple] = []

    def addClip(self, filename, camLoc, firstMs, lastMs, prevFile, nextFile, cacheStatus, procWidth, procHeight, updateProcSize=True):
        self.clips.append((filename, camLoc, firstMs, lastMs, prevFile, nextFile, procWidth, procHeight))


def write_moving_rect(path, frames: int = 16, fps: int = 10, width: int = 320, height: int = 240) -> None:
    """Synthetic MP4: a white rectangle moving across a black frame."""
    import av
    import numpy as np

    container = av.open(str(path), mode="w")
    try:
        stream = container.add_stream("libx264", rate=fps)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        stream.options = {"crf": "18", "preset": "ultrafast"}
        for index in range(frames):
            array = np.zeros((height, width, 3), dtype=np.uint8)
            x = 12 + index * 12
            array[70:190, x : x + 48] = 255
            video = av.VideoFrame.from_ndarray(array, format="rgb24")
            video.pts = index
            video.time_base = fractions.Fraction(1, fps)
            for packet in stream.encode(video):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
