"""PyAV capture, clip IO, live-view, JPEG, and HLS.

This is the open replacement for the closed ``videoLib2`` extension. Call
sites keep using :class:`StreamReader`, :class:`ClipReader`, and the clip
helpers exported from :mod:`backEnd.native.capture`.

USB cameras use OpenCV when it is installed (DirectShow on Windows). RTSP,
HTTP, and files use PyAV, whose wheels ship FFmpeg. Packet capture is not
part of this backend.
"""

from __future__ import annotations

import bisect
import collections
import ctypes
import fractions
import glob
import logging
import mmap
import os
import sys
import time
from io import BytesIO
from typing import Any, Optional

from appCommon.LiveViewBuffer import (
    LIVE_FOOTER_SIZE,
    LIVE_HEADER_SIZE,
    blank_bytes,
    format_live_footer,
    format_live_header,
)

_LIVE_MAX_WIDTH = 1280
_LIVE_MAX_HEIGHT = 720
_DEFAULT_CLIP_MS = 60_000
_CLIP_FPS = 15
_MAX_RECORD_WIDTH = 1280


def _av():
    import av

    return av


def av_available() -> bool:
    try:
        _av()
    except Exception:
        return False
    return True


def _even(value: int) -> int:
    value = int(value)
    if value < 2:
        return 2
    return value if value % 2 == 0 else value - 1


def _rgb_ndarray(rgb: bytes, width: int, height: int):
    import numpy as np

    return np.frombuffer(rgb, dtype=np.uint8).reshape((height, width, 3))


class VideoFrame:
    """One RGB frame. ``buffer`` stays a ctypes pointer for older readers."""

    def __init__(
        self,
        rgb: bytes,
        width: int,
        height: int,
        ms: int,
        filename: str = "",
        dummy: bool = False,
    ) -> None:
        self.rgb = bytes(rgb)
        self.width = int(width)
        self.height = int(height)
        self.ms = int(ms)
        self.filename = filename or ""
        self.dummy = bool(dummy)
        self.wasResized = False
        self._large: Optional["VideoFrame"] = None
        # create_string_buffer copies and appends a NUL. Readers use width*height*3.
        self._storage = ctypes.create_string_buffer(self.rgb)
        self.buffer = ctypes.cast(self._storage, ctypes.c_void_p)

    def getLargeFrame(self) -> "VideoFrame":
        return self._large if self._large is not None else self

    def asPil(self):
        from PIL import Image

        return Image.frombytes("RGB", (self.width, self.height), self.rgb)


def decoder_info_path(mmapPath: str, userDir: str, camera: str) -> str:
    """Where the HW/SW + bitrate status goes: next to the live mmap, or
    <userDir>/live/<camera>.decoder.json when no live view is open."""
    if mmapPath:
        return os.path.splitext(mmapPath)[0] + ".decoder.json"
    if userDir and camera:
        return os.path.join(userDir, "live", camera + ".decoder.json")
    return ""


def _scale_rgb(rgb: bytes, width: int, height: int, out_w: int, out_h: int) -> tuple[bytes, int, int]:
    if out_w <= 0 and out_h <= 0:
        return rgb, width, height
    target_w = out_w if out_w > 0 else width
    target_h = out_h if out_h > 0 else height
    if target_w == width and target_h == height:
        return rgb, width, height
    scale = min(target_w / float(width), target_h / float(height))
    # Multiple-of-4 width keeps RGB rows 4-byte aligned for every consumer
    # (OpenGL, wx bitmaps, encoders).
    new_w = max(4, int(width * scale) // 4 * 4)
    new_h = max(1, int(height * scale))
    if new_w == width and new_h == height:
        return rgb, width, height
    from PIL import Image

    image = Image.frombytes("RGB", (width, height), rgb)
    image = image.resize((new_w, new_h), Image.Resampling.BILINEAR)
    return image.tobytes(), new_w, new_h


def _jpeg_bytes(rgb: bytes, width: int, height: int, out_w: int, out_h: int) -> bytes:
    scaled, sw, sh = _scale_rgb(rgb, width, height, out_w, out_h)
    from PIL import Image

    image = Image.frombytes("RGB", (sw, sh), scaled)
    buffer = BytesIO()
    image.save(buffer, format="JPEG", quality=80)
    return buffer.getvalue()


def _record_dims(width: int, height: int) -> tuple[int, int]:
    """Cap recorded width so a 4K camera does not write an empty or stalled file."""
    width = max(2, int(width))
    height = max(2, int(height))
    if width > _MAX_RECORD_WIDTH:
        height = max(2, int(round(height * _MAX_RECORD_WIDTH / float(width))))
        width = _MAX_RECORD_WIDTH
    return _even(width), _even(height)


def analytics_frame(frame: "VideoFrame") -> "VideoFrame":
    """The frame the tracker sees: the camera frame scaled to the recorded
    clip size, with the full-size frame kept for the detector.

    A 4096x1536 camera frame made the pure-Python motion tracker take about a
    second per frame, so tracks broke up and objects were never sent to the
    detector. Boxes stay in the same coordinates as the recorded clips.
    """
    out_w, out_h = _record_dims(frame.width, frame.height)
    if (out_w, out_h) == (frame.width, frame.height):
        return frame
    from PIL import Image

    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    image = image.resize((out_w, out_h), Image.Resampling.BILINEAR)
    small = VideoFrame(image.tobytes(), out_w, out_h, frame.ms, filename=frame.filename, dummy=frame.dummy)
    small.wasResized = True
    small._large = frame
    return small


def _frame_ms(frame: Any, fallback_ms: int) -> int:
    stamp = getattr(frame, "time", None)
    if stamp is None:
        return int(fallback_ms)
    return int(round(float(stamp) * 1000.0))


_HW_PREFERENCE = ("d3d11va", "dxva2", "cuda", "qsv") if sys.platform == "win32" else (
    "videotoolbox", "vaapi", "cuda", "qsv")


def hw_device_for(pref: Any) -> Optional[str]:
    """FFmpeg hwaccel device for the Options preference ("auto", "none", or a name)."""
    pref = str(pref or "auto").strip().lower()
    if pref in ("none", "off", "false", "0"):
        return None
    try:
        from av.codec.hwaccel import hwdevices_available

        available = list(hwdevices_available())
    except Exception:
        return None
    if pref != "auto" and pref in available:
        return pref
    for name in _HW_PREFERENCE:
        if name in available:
            return name
    return None


def decoder_label(codec_context: Any, hw_device: Optional[str]) -> str:
    if hw_device and getattr(codec_context, "is_hwaccel", False):
        return "Hardware (%s)" % hw_device.upper()
    return "Software"


def _tolerant_decode(container: Any, stream: Any, max_bad: int = 100, on_packet: Any = None):
    """Decode frames, skipping corrupt packets instead of dropping the stream.

    One damaged packet (common on Wi-Fi doorbells / Blue Iris restreams)
    used to raise out of the decode loop and restart the camera process.
    """
    import av

    bad = 0
    for packet in container.demux(stream):
        if on_packet is not None and packet.size:
            on_packet(packet)
        try:
            frames = packet.decode()
        except (av.error.InvalidDataError, av.error.PatchWelcomeError) as exc:
            bad += 1
            if bad > max_bad:
                raise RuntimeError("too many corrupt packets in a row (%s)" % exc)
            continue
        if frames:
            bad = 0
        for frame in frames:
            yield frame


_MAX_QUEUED_FRAMES = 10


class _ThreadedFrames:
    """Runs a frame iterator (demux + decode + passthrough recording) on its
    own thread so the network socket is always drained at the camera's rate.

    If analytics fall behind, the oldest undelivered frames are dropped
    instead of letting the TCP receive window fill up: Blue Iris (and many
    cameras) reset an RTSP client whose socket backs up (WSAECONNRESET 10054
    about every 50 s). Recording is unaffected since packets are remuxed on
    this thread before decoding.
    """

    _END = object()

    def __init__(self, source: Any, maxlen: int = _MAX_QUEUED_FRAMES, log: Any = None) -> None:
        import collections
        import threading

        self._source = source
        self._queue: "collections.deque" = collections.deque()
        self._maxlen = max(1, int(maxlen))
        self._cond = threading.Condition()
        self._error: Optional[BaseException] = None
        self._done = False
        self._stop = False
        self.dropped = 0
        self._dropped_logged = 0
        self._last_log = time.monotonic()
        self._log = log
        self._thread = threading.Thread(target=self._run, name="stream-reader", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for frame in self._source:
                with self._cond:
                    if self._stop:
                        break
                    if len(self._queue) >= self._maxlen:
                        self._queue.popleft()
                        self.dropped += 1
                    self._queue.append(frame)
                    self._cond.notify()
        except BaseException as exc:
            self._error = exc
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def __iter__(self) -> "_ThreadedFrames":
        return self

    def __next__(self) -> Any:
        with self._cond:
            while not self._queue and not self._done:
                self._cond.wait(1.0)
            self._maybe_log()
            if self._queue:
                return self._queue.popleft()
            if self._error is not None:
                raise self._error
            raise StopIteration

    def _maybe_log(self) -> None:
        now = time.monotonic()
        if self._log is None or now - self._last_log < 60 or self.dropped == self._dropped_logged:
            return
        try:
            self._log.info("Analytics behind the camera: skipped %d frames in the last %.0f s "
                           "(recording unaffected)" % (self.dropped - self._dropped_logged, now - self._last_log))
        except Exception:
            pass
        self._dropped_logged = self.dropped
        self._last_log = now

    def stop(self) -> None:
        with self._cond:
            self._stop = True
            self._queue.clear()
            self._cond.notify_all()


def _maybe_threaded(uri: str, frames: Any, log: Any) -> Any:
    """Live network streams get a reader thread; files are read on demand
    (reading ahead would drop frames that are simply not consumed yet)."""
    if str(uri or "").lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return _ThreadedFrames(frames, log=log)
    return frames


def _video_stream(container: Any):
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"
    return stream


class _ClipWriter:
    """Rolling MP4 segments registered with ClipManager.addClip."""

    def __init__(self, reader: "StreamReader") -> None:
        self._reader = reader
        self._container = None
        self._stream = None
        self._full_path = ""
        self._relative = ""
        self._first_ms: Optional[int] = None
        self._last_ms: Optional[int] = None
        self._prev_relative = ""
        self._last_tick = -1
        self._width = 0
        self._height = 0

    @property
    def relative_path(self) -> str:
        return self._relative

    def write(self, rgb: bytes, width: int, height: int, ms: int) -> None:
        limit = int(self._reader._extras.get("maxClipMs", _DEFAULT_CLIP_MS) or _DEFAULT_CLIP_MS)
        if self._container is not None and self._first_ms is not None and ms - self._first_ms >= limit:
            self.close_segment()
        if self._container is None:
            self._open(width, height, ms)
        assert self._stream is not None
        array = _rgb_ndarray(rgb, width, height)
        if array.shape[1] != self._width or array.shape[0] != self._height:
            scaled, _, _ = _scale_rgb(rgb, width, height, self._width, self._height)
            array = _rgb_ndarray(scaled, self._width, self._height)
        av = _av()
        video = av.VideoFrame.from_ndarray(array, format="rgb24")
        if self._first_ms is None:
            self._first_ms = ms
        # libx264 encodes in 1/15 s ticks whatever the stream time base is.
        # Two frames that arrive within one tick got the same pts, the muxer
        # rejected the packet (EINVAL) and the file was left without an index.
        tick = int(round(max(0, ms - self._first_ms) * _CLIP_FPS / 1000.0))
        if tick <= self._last_tick:
            tick = self._last_tick + 1
        self._last_tick = tick
        video.pts = tick
        video.time_base = fractions.Fraction(1, _CLIP_FPS)
        for packet in self._stream.encode(video):
            self._container.mux(packet)
        self._last_ms = ms

    def _open(self, width: int, height: int, ms: int) -> None:
        root = self._reader._archive_path or self._reader._tmp_path or "."
        camera = self._reader._camera_location or "camera"
        folder = os.path.join(root, camera)
        os.makedirs(folder, exist_ok=True)
        name = "%d.mp4" % int(ms)
        self._full_path = os.path.join(folder, name)
        self._relative = (camera + "/" + name).replace("\\", "/")
        self._width = _record_dims(width, height)[0]
        self._height = _record_dims(width, height)[1]
        self._first_ms = ms
        self._last_ms = ms
        self._last_tick = -1
        av = _av()
        # Fragmented MP4 keeps every finished fragment playable. A plain MP4
        # writes its index (moov) only on close, so a capture process that
        # restarts or dies leaves a file no player can open.
        self._container = av.open(
            self._full_path,
            mode="w",
            options={"movflags": "frag_keyframe+empty_moov+default_base_moof"},
        )
        stream = self._container.add_stream("libx264", rate=15)
        stream.width = self._width
        stream.height = self._height
        stream.pix_fmt = "yuv420p"
        stream.time_base = fractions.Fraction(1, 1000)
        self._stream = stream

    def flush_if_long_enough(self, min_ms: int = 2000) -> int:
        if self._container is None or self._first_ms is None or self._last_ms is None:
            return 0
        if self._last_ms - self._first_ms < min_ms:
            return 0
        self.close_segment()
        return 1

    def close_segment(self) -> None:
        if self._container is None:
            return
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
            self._container.close()
        except Exception:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._stream = None
        self._register()

    def _register(self) -> None:
        clip_mgr = self._reader._clip_mgr
        if clip_mgr is None or self._first_ms is None or self._last_ms is None:
            self._prev_relative = self._relative
            return
        lock = self._reader._clip_mgr_lock
        if lock is not None:
            lock.acquire()
        try:
            clip_mgr.addClip(
                self._relative,
                self._reader._camera_location,
                int(self._first_ms),
                int(self._last_ms),
                self._prev_relative,
                "",
                0,
                self._width,
                self._height,
            )
        except Exception:
            move_failed = self._reader._move_failed
            if move_failed is not None:
                move_failed(self._relative)
        finally:
            if lock is not None:
                lock.release()
        self._prev_relative = self._relative


class _HlsWriter:
    """Best-effort single-variant HLS (MPEG-TS segments plus an m3u8)."""

    def __init__(self, playlist_path: str, start_index: int) -> None:
        self.playlist_path = playlist_path
        self._index = max(0, int(start_index))
        self._segments: list[tuple[str, float]] = []
        self._container = None
        self._stream = None
        self._segment_path = ""
        self._segment_started: Optional[float] = None
        self._width = 0
        self._height = 0
        folder = os.path.dirname(playlist_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

    def write(self, rgb: bytes, width: int, height: int, ms: int) -> None:
        now = time.time()
        if self._container is not None and self._segment_started is not None and now - self._segment_started >= 1.0:
            self._close_segment(now - self._segment_started)
        if self._container is None:
            self._open_segment(width, height, now)
        array = _rgb_ndarray(rgb, width, height)
        array = array[: self._height, : self._width]
        av = _av()
        video = av.VideoFrame.from_ndarray(array, format="rgb24")
        if self._segment_started is None:
            self._segment_started = now
        video.pts = int(max(0.0, now - self._segment_started) * 90000)
        for packet in self._stream.encode(video):
            self._container.mux(packet)

    def _open_segment(self, width: int, height: int, now: float) -> None:
        self._width = _even(min(width, _LIVE_MAX_WIDTH))
        self._height = _even(min(height, _LIVE_MAX_HEIGHT))
        folder = os.path.dirname(self.playlist_path) or "."
        stem = os.path.splitext(os.path.basename(self.playlist_path))[0]
        # CameraCapture's playlist parser expects "-<digit><index>.ts".
        filename = "%s-1%04d.ts" % (stem, self._index)
        self._index += 1
        self._segment_path = os.path.join(folder, filename)
        self._segment_started = now
        av = _av()
        self._container = av.open(self._segment_path, mode="w", format="mpegts")
        try:
            stream = self._container.add_stream("libx264", rate=15)
        except Exception:
            stream = self._container.add_stream("mpeg4", rate=15)
        stream.width = self._width
        stream.height = self._height
        stream.pix_fmt = "yuv420p"
        self._stream = stream

    def _close_segment(self, duration: float) -> None:
        if self._container is None:
            return
        try:
            for packet in self._stream.encode():
                self._container.mux(packet)
            self._container.close()
        except Exception:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._stream = None
        self._segments.append((os.path.basename(self._segment_path), max(0.1, float(duration))))
        self._segments = self._segments[-8:]
        self._write_playlist()
        self._segment_started = None

    def _write_playlist(self) -> None:
        target = 2
        if self._segments:
            target = max(1, int(max(item[1] for item in self._segments)) + 1)
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            "#EXT-X-TARGETDURATION:%d" % target,
            "#EXT-X-MEDIA-SEQUENCE:%d" % max(0, self._index - len(self._segments)),
        ]
        for name, duration in self._segments:
            lines.append("#EXTINF:%.3f," % duration)
            lines.append(name)
        text = "\n".join(lines) + "\n"
        with open(self.playlist_path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def close(self) -> None:
        if self._segment_started is not None:
            self._close_segment(max(0.1, time.time() - self._segment_started))
        else:
            self._close_segment(0.1)



# --------------------------------------------------------------------------
# Stream selection, network options, passthrough recording
# --------------------------------------------------------------------------

_IDLE_ANALYTICS_FPS = 3.0
_IDLE_AFTER_S = 15.0
_MAX_PREROLL_PACKETS = 900
_SUB_MAX_ASPECT_DIFF = 0.35


def redact_uri(text: str) -> str:
    """Remove user:password@ from any URL in a log message."""
    import re

    return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s'\"]+@", r"\1***@", str(text))


def network_options(uri: str) -> dict:
    """FFmpeg demuxer options for network cameras (FFmpeg 7+ names).

    RTSP over TCP (no UDP packet loss -> fewer smeared frames), a socket
    I/O timeout of 10 s ("timeout", in microseconds; "stimeout" no longer
    exists), and a 2 MB receive buffer. FFmpeg's RTSP demuxer already sends
    GET_PARAMETER/OPTIONS keep-alives at half the server session timeout
    while we keep reading.
    """
    lowered = str(uri or "").lower()
    if lowered.startswith(("rtsp://", "rtsps://")):
        return {"rtsp_transport": "tcp", "timeout": "10000000", "buffer_size": "2097152",
                "max_delay": "500000"}
    if lowered.startswith(("http://", "https://")):
        return {"timeout": "10000000"}
    return {}


def sub_stream_uri(uri: str, extras: Optional[dict] = None) -> Optional[str]:
    """Lower-resolution stream URI for analytics / grid thumbnails, or None.

    Order: an explicit ``subStreamUri`` extra (ONVIF wizard stores the
    lowest-resolution profile), then known vendor templates. Blue Iris
    restreams have no reliable sub path, so they use ``blueIrisSubStream``
    only when set. Callers fall back to the main stream when the sub
    stream cannot be opened or does not match.
    """
    import re

    extras = extras or {}
    if str(extras.get("useSubStream", True)).lower() in ("false", "0", "no", "off"):
        return None
    explicit = extras.get("subStreamUri") or extras.get("blueIrisSubStream")
    if explicit:
        explicit = str(explicit)
        return None if explicit == uri else explicit
    if not uri or not str(uri).lower().startswith(("rtsp://", "rtsps://", "rtmp://", "http://", "https://")):
        return None
    if extras.get("blueIrisServer") or extras.get("blueIrisShortName"):
        return None
    rules = (
        (r"(h26[45]Preview_\d+_)main", r"\g<1>sub"),           # Reolink RTSP
        (r"(Preview_\d+_)main", r"\g<1>sub"),
        (r"(channel\d+_)main(\.bcs)", r"\g<1>sub\g<2>"),        # Reolink RTMP/FLV
        (r"([?&]subtype=)0\b", r"\g<1>1"),                     # Amcrest / Dahua
        (r"(/Streaming/Channels/\d*?)01\b", r"\g<1>02"),        # Hikvision
        (r"(/stream)1\b", r"\g<1>2"),                          # many generic IPCs
    )
    for pattern, repl in rules:
        new, n = re.subn(pattern, repl, uri, count=1, flags=re.IGNORECASE)
        if n and new != uri:
            return new
    return None


def sub_stream_usable(main_wh: tuple[int, int], sub_wh: tuple[int, int]) -> bool:
    mw, mh = main_wh
    sw, sh = sub_wh
    if min(mw, mh, sw, sh) <= 0 or sw * sh >= mw * mh:
        return False
    return abs((sw / float(sh)) / (mw / float(mh)) - 1.0) <= _SUB_MAX_ASPECT_DIFF


def pick_onvif_sub_uri(choices: list, main_uri: str) -> Optional[str]:
    """From wizard ONVIF choices [(label "name: enc, WxH (...)", uri)], the
    lowest-resolution other profile, used as subStreamUri."""
    import re

    best = None
    for label, uri in choices:
        if not uri or uri == main_uri or not str(uri).lower().startswith("rtsp"):
            continue
        m = re.search(r"(\d+)x(\d+)", str(label))
        if not m:
            continue
        area = int(m.group(1)) * int(m.group(2))
        if best is None or area < best[0]:
            best = (area, uri)
    return best[1] if best else None


class _PtsClock:
    """Wall-clock ms for a stream's frames/packets: anchored at the first
    arrival, then following the camera's pts so spacing is smooth. Large
    pts jumps (>10 s off the wall clock) re-anchor; output is monotonic."""

    def __init__(self) -> None:
        self._anchor: Optional[tuple[float, float]] = None
        self._last = -1

    def ms(self, seconds: Optional[float]) -> int:
        now = time.time() * 1000.0
        if seconds is None:
            value = now
        else:
            seconds = float(seconds)
            if self._anchor is None:
                self._anchor = (now, seconds)
            value = self._anchor[0] + (seconds - self._anchor[1]) * 1000.0
            if abs(value - now) > 10_000:
                self._anchor = (now, seconds)
                value = now
        out = int(value)
        if out <= self._last:
            out = self._last + 1
        self._last = out
        return out


def _packet_seconds(packet: Any) -> Optional[float]:
    stamp = packet.pts if packet.pts is not None else packet.dts
    tb = packet.time_base
    if stamp is None or tb is None:
        return None
    return float(stamp * tb)


class _BitrateMeter:
    """Average bitrate of the recorded (main) stream over a wall-clock window."""

    def __init__(self, window_s: float = 60.0) -> None:
        self.window_s = window_s
        self._start: Optional[float] = None
        self._bytes = 0

    def add(self, size: int, now: float) -> Optional[int]:
        """Count a packet; return kbps when a window completes, else None."""
        if self._start is None:
            self._start = now
        self._bytes += int(size)
        elapsed = now - self._start
        if elapsed < self.window_s:
            return None
        kbps = int(round(self._bytes * 8 / 1000.0 / elapsed))
        self._start, self._bytes = now, 0
        return kbps


def gb_per_day(kbps: float) -> float:
    """Storage per day for a continuous stream at kbps (decimal GB)."""
    return float(kbps) * 1000.0 / 8.0 * 86400.0 / 1e9


class _RemuxWriter:
    """Records the camera's own compressed packets into fragmented MP4
    segments (no decode, no re-encode). Segments start on a keyframe; the
    packets since the last keyframe are kept as pre-roll so a new segment
    (after a flush, a size roll, or a write error) starts without a gap."""

    def __init__(self, reader: "StreamReader", in_stream: Any) -> None:
        import threading

        self._reader = reader
        self._in_stream = in_stream
        self._clock = _PtsClock()
        self._lock = threading.RLock()
        self._container = None
        self._out = None
        self._full_path = ""
        self._relative = ""
        self._first_ms: Optional[int] = None
        self._last_ms: Optional[int] = None
        self._base: Optional[int] = None
        self._last_dts: Optional[int] = None
        self._prev_relative = ""
        self._roll_requested = False
        self._gop: list[tuple[Any, int]] = []
        self._segments: list[tuple[int, str]] = []
        self.packets_written = 0
        self._meter = _BitrateMeter()

    @property
    def relative_path(self) -> str:
        return self._relative

    def filename_for(self, ms: int) -> str:
        with self._lock:
            name = ""
            for first, rel in self._segments:
                if first <= ms:
                    name = rel
            return name or self._relative

    def write_packet(self, packet: Any) -> None:
        if packet.size == 0 or packet.stream is not self._in_stream and packet.stream.index != self._in_stream.index:
            return
        ms = self._clock.ms(_packet_seconds(packet))
        kbps = self._meter.add(packet.size, time.monotonic())
        if kbps is not None:
            info = getattr(self._reader, "_decoder_info", None)
            if isinstance(info, dict):
                info["measured_kbps"] = kbps
                info["measured_gb_per_day"] = round(gb_per_day(kbps), 1)
                self._reader._write_decoder_info()
        with self._lock:
            key = bool(packet.is_keyframe)
            if key:
                self._gop = []
            if self._gop or key:
                self._gop.append((packet, ms))
                if len(self._gop) > _MAX_PREROLL_PACKETS:
                    self._gop = []
            limit = int(self._reader._extras.get("maxClipMs", _DEFAULT_CLIP_MS) or _DEFAULT_CLIP_MS)
            if key and self._container is not None and (
                    self._roll_requested or (self._first_ms is not None and ms - self._first_ms >= limit)):
                self.close_segment()  # roll exactly on the keyframe: no gap, no overlap
            if self._container is None:
                if not key:
                    return  # wait for a keyframe; a segment must start decodable
                self._open(ms)
            try:
                self._mux(packet, ms)
            except Exception as exc:
                (getattr(self._reader, "logger", None) or logging.getLogger(__name__)).warning(
                    "Clip write failed, restarting segment from the last keyframe: %r" % (exc,))
                self._recover(packet)

    def _mux(self, packet: Any, ms: int) -> None:
        dts = packet.dts if packet.dts is not None else packet.pts
        pts = packet.pts if packet.pts is not None else dts
        if dts is None:
            tb = self._in_stream.time_base
            dts = pts = int((ms - (self._first_ms or ms)) / 1000.0 / float(tb)) if tb else ms
            base = 0
        else:
            if self._base is None:
                self._base = int(dts)
            base = self._base
        dts = int(dts) - base
        pts = int(pts) - base
        if self._last_dts is not None and dts <= self._last_dts:
            shift = self._last_dts + 1 - dts
            dts += shift
            pts += shift
        pts = max(pts, dts)
        self._last_dts = dts
        # Mux a copy: the original packet is still decoded by the caller.
        out = _av().Packet(bytes(packet))
        out.is_keyframe = bool(packet.is_keyframe)
        out.time_base = self._in_stream.time_base
        out.dts = dts
        out.pts = pts
        if packet.duration:
            out.duration = packet.duration
        out.stream = self._out
        self._container.mux(out)
        self._last_ms = ms
        self.packets_written += 1

    def _open(self, ms: int) -> None:
        root = self._reader._archive_path or self._reader._tmp_path or "."
        camera = self._reader._camera_location or "camera"
        folder = os.path.join(root, camera)
        os.makedirs(folder, exist_ok=True)
        name = "%d.mp4" % int(ms)
        self._full_path = os.path.join(folder, name)
        self._relative = (camera + "/" + name).replace("\\", "/")
        av = _av()
        # Fragmented MP4: every finished fragment stays playable if the
        # capture process dies before the segment is closed.
        self._container = av.open(self._full_path, mode="w",
                                  options={"movflags": "frag_keyframe+empty_moov+default_base_moof"})
        self._out = self._container.add_stream_from_template(self._in_stream)
        self._first_ms = ms
        self._last_ms = ms
        self._base = None
        self._last_dts = None
        self._roll_requested = False
        self._segments.append((ms, self._relative))
        del self._segments[:-8]

    def request_roll(self) -> int:
        with self._lock:
            if self._container is None or self._first_ms is None or self._last_ms is None:
                return 0
            if self._last_ms - self._first_ms < 2000:
                return 0
            self._roll_requested = True
            return 1

    flush_if_long_enough = request_roll

    def close_segment(self) -> None:
        with self._lock:
            if self._container is None:
                return
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
            self._out = None
            if self._first_ms is not None and self._last_ms is not None and self._last_ms > self._first_ms:
                _ClipWriter._register(self)
            else:
                try:
                    os.remove(self._full_path)
                except OSError:
                    pass

    def _recover(self, failed: Any) -> None:
        """Pre-roll: after a write error, reopen a segment from the buffered
        packets of the current GOP (keyframe first), minus the bad packet."""
        gop = [(p, m) for (p, m) in self._gop if p is not failed]
        self.close_segment()
        self._gop = gop
        if not gop or not gop[0][0].is_keyframe:
            self._gop = []
            return
        try:
            self._open(gop[0][1])
            for packet, ms in gop:
                self._mux(packet, ms)
        except Exception:
            self.close_segment()
            self._gop = []

    @property
    def _width(self) -> int:
        return int(self._reader.getProcSize()[0])

    @property
    def _height(self) -> int:
        return int(self._reader.getProcSize()[1])


class _MainRecorder:
    """Background reader of the main stream when analytics use the sub
    stream: remuxes every packet to disk and decodes only while an enlarged
    live view wants main-stream quality."""

    def __init__(self, reader: "StreamReader", container: Any, stream: Any, writer: Optional[_RemuxWriter]) -> None:
        import threading

        self._reader = reader
        self._container = container
        self._stream = stream
        self._writer = writer
        self._stop = threading.Event()
        self.error: Optional[BaseException] = None
        self.alive = True
        self._thread = threading.Thread(target=self._run, name="main-stream-recorder", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        decoding = False
        bad = 0
        try:
            for packet in self._container.demux(self._stream):
                if self._stop.is_set():
                    break
                if packet.size == 0:
                    continue
                if self._writer is not None:
                    self._writer.write_packet(packet)
                want = self._reader._main_live_wanted()
                if want and not decoding and packet.is_keyframe:
                    decoding = True
                if not want:
                    decoding = False
                if decoding:
                    try:
                        frames = packet.decode()
                        bad = 0
                    except Exception:
                        bad += 1
                        decoding = bad < 50
                        continue
                    for frame in frames:
                        self._reader._live_from_frame(frame)
        except BaseException as exc:  # reported to the capture loop
            self.error = exc
        finally:
            self.alive = False

    def stop(self) -> None:
        self._stop.set()
        try:
            self._container.close()
        except Exception:
            pass
        self._thread.join(timeout=5)


class StreamReader:
    """File, RTSP/HTTP, and USB reader with clip recording and live view."""

    def __init__(
        self,
        cameraLocation: str = "",
        clipMgr: Any = None,
        clipMgrLock: Any = None,
        tmpPath: str = "",
        archivePath: str = "",
        userDir: str = "",
        logFn: Any = None,
        record: bool = True,
        moveFailed: Any = None,
        initFrameSize: int = 0,
        statsInterval: int = 0,
    ) -> None:
        self.locationName = cameraLocation or ""
        self._camera_location = cameraLocation or ""
        self._clip_mgr = clipMgr
        self._clip_mgr_lock = clipMgrLock
        self._tmp_path = tmpPath or ""
        self._archive_path = archivePath or ""
        self._user_dir = userDir or ""
        self._log_fn = logFn
        self._record = bool(record)
        self._move_failed = moveFailed
        self._init_frame_size = int(initFrameSize or 0)
        self._stats_interval = int(statsInterval or 0)
        self._extras: dict[str, Any] = {}
        self._uri = ""
        self._container = None
        self._iterator = None
        self._capture = None
        self._running = False
        self._width = 0
        self._height = 0
        self._fallback_ms = 0
        self._last_ms = -1
        self._latest: Optional[VideoFrame] = None
        self._writer: Optional[_ClipWriter] = None
        self._hls: dict[int, _HlsWriter] = {}
        self._mmap = None
        self._mmap_file = None
        self._mmap_path = ""
        self._mmap_size = 0
        self._live_enabled = True
        self._live_width = 0
        self._live_height = 0
        self._request_fps = 15.0
        self._frame_index = 0
        self._prev_live_ms: Optional[int] = None
        self._capture_fps = 0.0
        self._audio_volume = 1.0

    @property
    def isRunning(self) -> bool:
        return bool(self._running)

    def open(self, cameraUri: str, extras: Optional[dict] = None) -> bool:
        self._reset_source()
        self._extras = dict(extras or {})
        self._uri = "" if cameraUri is None else str(cameraUri)
        self._fallback_ms = 0
        self._last_ms = -1
        self._frame_index = 0
        if not self._uri:
            return False
        usb_index = _usb_index(self._uri)
        try:
            if usb_index is not None:
                return self._open_usb(usb_index)
            return self._open_av(self._uri)
        except Exception:
            self._running = False
            return False

    def _hwaccel(self):
        hw = hw_device_for(self._extras.get("hardwareDevice", "auto"))
        if not hw:
            return None, None
        try:
            from av.codec.hwaccel import HWAccel

            return HWAccel(device_type=hw, allow_software_fallback=True), hw
        except Exception:
            return None, None

    def _open_container(self, uri: str, hwaccel: Any = None):
        av = _av()
        options = network_options(uri) or None
        # The (open, read) timeouts stop a stalled read from blocking the
        # capture loop forever.
        if hwaccel is not None:
            try:
                return av.open(uri, options=options, timeout=(15.0, 10.0), hwaccel=hwaccel)
            except TypeError:
                pass
        return av.open(uri, options=options, timeout=(15.0, 10.0))

    def _open_av(self, uri: str) -> bool:
        log = getattr(self, "logger", None) or logging.getLogger(__name__)
        hwaccel, hw = self._hwaccel()
        main = self._open_container(uri, hwaccel)
        main_stream = _video_stream(main)
        self._container = main
        self._main_size = (int(main_stream.codec_context.width or 0), int(main_stream.codec_context.height or 0))
        self._clock = _PtsClock()
        self._live_clock = _PtsClock()
        self._last_analytics_t = 0.0
        self._last_activity_t = time.time()
        writer = _RemuxWriter(self, main_stream) if self._record else None
        self._writer = writer
        self._recorder = None
        self._sub_container = None
        sub_uri = sub_stream_uri(uri, self._extras)
        sub = None
        if sub_uri:
            try:
                sub = self._open_container(sub_uri, hwaccel)
                sub_stream = _video_stream(sub)
                sub_size = (int(sub_stream.codec_context.width or 0), int(sub_stream.codec_context.height or 0))
                if not sub_stream_usable(self._main_size, sub_size):
                    log.info("Sub stream %dx%d not usable for main %dx%d; analytics use the main stream"
                             % (sub_size + self._main_size))
                    sub.close()
                    sub = None
            except Exception as exc:
                log.info("Sub stream unavailable (%s); analytics use the main stream" % (type(exc).__name__,))
                sub = None
        if sub is not None:
            self._sub_container = sub
            self._stream = sub_stream
            self._recorder = _MainRecorder(self, main, main_stream, writer)
            self._iterator = _maybe_threaded(uri, _tolerant_decode(sub, sub_stream), log)
            self._stream_mode = "sub"
        else:
            self._stream = main_stream
            self._iterator = _maybe_threaded(
                uri,
                _tolerant_decode(main, main_stream, on_packet=writer.write_packet if writer is not None else None),
                log)
            self._stream_mode = "main"
        self._hw_device = hw
        self._decoder_info = {}
        self._width = int(self._stream.codec_context.width or 0)
        self._height = int(self._stream.codec_context.height or 0)
        self._running = True
        return True

    def _open_usb(self, index: int) -> bool:
        import cv2

        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        capture = cv2.VideoCapture(index, backend)
        if not capture.isOpened():
            capture.release()
            return False
        self._capture = capture
        self._width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        self._height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        self._running = True
        if self._record:
            self._writer = _ClipWriter(self)
        return True

    def _reset_source(self) -> None:
        """Close the current container without dropping the live-view map."""
        self._running = False
        recorder = getattr(self, "_recorder", None)
        if recorder is not None:
            recorder.stop()
            self._recorder = None
        sub = getattr(self, "_sub_container", None)
        if sub is not None:
            try:
                sub.close()
            except Exception:
                pass
            self._sub_container = None
        if self._writer is not None:
            try:
                self._writer.close_segment()
            except Exception:
                pass
            self._writer = None
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
        if isinstance(self._iterator, _ThreadedFrames):
            self._iterator.stop()
        self._iterator = None
        if self._capture is not None:
            try:
                self._capture.release()
            except Exception:
                pass
            self._capture = None

    def close(self, termFunc: Any = None) -> None:
        for writer in list(self._hls.values()):
            try:
                writer.close()
            except Exception:
                pass
        self._hls.clear()
        self._reset_source()
        self.close_mmap()
        if termFunc is not None:
            termFunc()

    def noteActivity(self) -> None:
        """Analytics saw objects/motion: keep full analytics frame rate."""
        self._last_activity_t = time.time()

    def _analytics_idle(self) -> bool:
        return time.time() - getattr(self, "_last_activity_t", 0.0) > _IDLE_AFTER_S

    def _main_live_wanted(self) -> bool:
        """Enlarged live view asks for more pixels than the sub stream has."""
        if getattr(self, "_stream_mode", "main") != "sub" or self._mmap is None or not self._live_enabled:
            return False
        sw, sh = self._width, self._height
        if sw <= 0 or sh <= 0 or self._live_width <= 0 or self._live_height <= 0:
            return False
        # Upscale the sub stream would need to fill the requested view.
        return min(self._live_width / float(sw), self._live_height / float(sh)) > 1.1

    def _live_from_frame(self, decoded: Any) -> None:
        """Live view straight from a decoded frame, scaled in libswscale."""
        if self._mmap is None:
            return
        w, h = int(decoded.width), int(decoded.height)
        out_w = self._live_width if self._live_width > 0 else min(w, _LIVE_MAX_WIDTH)
        out_h = self._live_height if self._live_height > 0 else min(h, _LIVE_MAX_HEIGHT)
        scale = min(out_w / float(w), out_h / float(h), 1.0)
        lw = max(4, int(w * scale) // 4 * 4)
        lh = max(2, int(h * scale) // 2 * 2)
        array = decoded.to_ndarray(width=lw, height=lh, format="rgb24")
        clock = getattr(self, "_live_clock", None) or _PtsClock()
        ms = clock.ms(getattr(decoded, "time", None))
        self._write_mmap(VideoFrame(array.tobytes(), lw, lh, ms, dummy=False))

    def getNewFrame(self, liveViewEnabled: bool = False) -> Optional[VideoFrame]:
        if not self._running:
            return None
        log = getattr(self, "logger", None) or logging.getLogger(__name__)
        recorder = getattr(self, "_recorder", None)
        if recorder is not None and not recorder.alive:
            log.warning("Main stream (recording) stopped: %s" % redact_uri(repr(recorder.error)))
            self._running = False
            return None
        if self._capture is not None:
            return self._get_usb_frame(liveViewEnabled)
        if self._iterator is None:
            self._running = False
            return None
        try:
            decoded = next(self._iterator)
        except StopIteration:
            self._running = False
            return None
        except Exception as exc:
            log.warning("Stream read failed: %s" % redact_uri(repr(exc)))
            self._running = False
            return None
        if not getattr(self, "_decoder_info", None):
            self._note_decoder(decoded)
        ms = self._clock.ms(getattr(decoded, "time", None))
        self._last_ms = ms
        writer = self._writer
        filename = writer.filename_for(ms) if isinstance(writer, _RemuxWriter) else ""
        self._width, self._height = int(decoded.width), int(decoded.height)
        self._frame_index += 1
        live = self._mmap is not None and (liveViewEnabled or self._live_enabled)
        if live and not self._main_live_wanted():
            try:
                self._live_from_frame(decoded)
            except Exception:
                pass
        # Idle: no objects for a while -> hand analytics ~3 fps; the other
        # frames are "dummy" (timestamp only), skipping RGB conversion and
        # tracking entirely.
        now = time.time()
        if self._analytics_idle() and now - self._last_analytics_t < 1.0 / _IDLE_ANALYTICS_FPS:
            return VideoFrame(b"", self._width, self._height, ms, filename=filename, dummy=True)
        self._last_analytics_t = now
        out_w, out_h = _record_dims(self._width, self._height)
        if (out_w, out_h) != (self._width, self._height):
            array = decoded.to_ndarray(width=out_w, height=out_h, format="rgb24")
        else:
            array = decoded.to_ndarray(format="rgb24")
        height, width = array.shape[:2]
        frame = VideoFrame(array.tobytes(), int(width), int(height), ms, filename=filename, dummy=False)
        self._latest = frame
        for hls in list(self._hls.values()):
            try:
                hls.write(frame.rgb, frame.width, frame.height, ms)
            except Exception:
                pass
        return frame

    def _get_usb_frame(self, liveViewEnabled: bool) -> Optional[VideoFrame]:
        rgb, width, height, _media_ms = self._read_rgb()
        if rgb is None:
            self._running = False
            return None
        ms = int(time.time() * 1000)
        if self._last_ms >= 0 and ms <= self._last_ms:
            ms = self._last_ms + 1
        self._last_ms = ms
        filename = ""
        if self._writer is not None:
            try:
                self._writer.write(rgb, width, height, ms)
                filename = self._writer.relative_path
            except Exception as exc:
                logging.getLogger(__name__).warning("Clip write failed, starting a new segment: %r", exc)
                try:
                    self._writer.close_segment()
                except Exception:
                    pass
        frame = VideoFrame(rgb, width, height, ms, filename=filename, dummy=False)
        self._width = width
        self._height = height
        self._latest = frame
        self._frame_index += 1
        if self._mmap is not None and (liveViewEnabled or self._live_enabled):
            self._write_mmap(frame)
        return analytics_frame(frame)

    def _read_rgb(self) -> tuple[Optional[bytes], int, int, int]:
        ok, bgr = self._capture.read()
        if not ok or bgr is None:
            return None, 0, 0, 0
        rgb_array = bgr[:, :, ::-1]
        height, width = rgb_array.shape[:2]
        return rgb_array.tobytes(), int(width), int(height), int(time.time() * 1000)

    def _note_decoder(self, decoded: Any) -> None:
        stream = getattr(self, "_stream", None)
        ctx = getattr(stream, "codec_context", None)
        rate = getattr(stream, "average_rate", None) or getattr(stream, "guessed_rate", None)
        info = {
            "decoder": decoder_label(ctx, getattr(self, "_hw_device", None)),
            "codec": str(getattr(ctx, "name", "") or ""),
            "width": int(getattr(decoded, "width", 0) or 0),
            "height": int(getattr(decoded, "height", 0) or 0),
            "pix_fmt": str(getattr(getattr(decoded, "format", None), "name", "") or ""),
            "fps": round(float(rate), 2) if rate else 0.0,
            "transport": "tcp" if self._uri.lower().startswith("rtsp") else "",
            "analytics_stream": getattr(self, "_stream_mode", "main"),
            "main_resolution": "%dx%d" % getattr(self, "_main_size", (0, 0)),
            "recording": "passthrough" if isinstance(self._writer, _RemuxWriter) else (
                "re-encode" if self._writer is not None else "off"),
            "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        self._decoder_info = info
        (getattr(self, "logger", None) or logging.getLogger(__name__)).info(
            "Stream started: decoder=%(decoder)s codec=%(codec)s %(width)dx%(height)d "
            "pix_fmt=%(pix_fmt)s fps=%(fps)s analytics=%(analytics_stream)s main=%(main_resolution)s "
            "recording=%(recording)s" % info)
        self._write_decoder_info()

    def _write_decoder_info(self) -> None:
        info = getattr(self, "_decoder_info", None)
        if not info:
            return
        path = decoder_info_path(self._mmap_path, self._user_dir, self._camera_location)
        if not path:
            return
        try:
            import json

            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as handle:
                json.dump(info, handle)
            os.replace(path + ".tmp", path)
        except Exception:
            pass

    def getDecoderInfo(self) -> dict:
        return dict(getattr(self, "_decoder_info", None) or {})

    def getProcSize(self) -> tuple[int, int]:
        # Boxes are in analytics-frame coordinates, which match the recorded
        # clip size (see analytics_frame and _ClipWriter).
        if self._width <= 0 or self._height <= 0:
            return (int(self._width), int(self._height))
        return _record_dims(self._width, self._height)

    def getInitialFrameBufferSize(self) -> int:
        if self._width > 0 and self._height > 0:
            return int(self._width) * int(self._height) * 3
        return int(self._init_frame_size)

    def setLiveStreamLimits(self, maxRes: Any = None, maxBitrate: Any = None) -> int:
        self._extras["maxRes"] = maxRes
        self._extras["maxBitrate"] = maxBitrate
        return 0

    def flush(self, *args: Any) -> int:
        """Finish the clip being written so search can play up to now.

        The original capture library formalized its temporary file on
        msgIdFlushVideo. Closing the segment registers it in clipdb; the next
        frame opens a new segment linked to it, so coverage stays continuous.
        """
        writer = self._writer
        if writer is None:
            return 0
        try:
            return writer.flush_if_long_enough()
        except Exception:
            logging.getLogger(__name__).exception("Clip flush failed")
            return 0

    def setMmapParams(self, enabled: Any, width: Any, height: Any, fps: Any) -> None:
        self._live_enabled = bool(enabled)
        try:
            self._live_width = int(width or 0)
            self._live_height = int(height or 0)
        except (TypeError, ValueError):
            self._live_width = 0
            self._live_height = 0
        try:
            self._request_fps = float(fps or 0) or self._request_fps
        except (TypeError, ValueError):
            pass

    def setAudioVolume(self, volume: Any) -> None:
        try:
            self._audio_volume = float(volume)
        except (TypeError, ValueError):
            self._audio_volume = 1.0

    def open_mmap(self, filename: str) -> bool:
        self.close_mmap()
        try:
            folder = os.path.dirname(filename)
            if folder:
                os.makedirs(folder, exist_ok=True)
            size = LIVE_HEADER_SIZE + (_LIVE_MAX_WIDTH * _LIVE_MAX_HEIGHT * 3) + LIVE_FOOTER_SIZE
            handle = open(filename, "w+b")
            handle.write(blank_bytes(size))
            handle.flush()
            self._mmap = mmap.mmap(handle.fileno(), size)
            self._mmap_file = handle
            self._mmap_path = filename
            self._mmap_size = size
            self._write_decoder_info()
            return True
        except Exception:
            self.close_mmap()
            return False

    def close_mmap(self) -> None:
        if self._mmap is not None:
            try:
                self._mmap.seek(0)
                self._mmap.write(blank_bytes(min(LIVE_HEADER_SIZE, self._mmap_size)))
                self._mmap.close()
            except Exception:
                pass
        self._mmap = None
        if self._mmap_file is not None:
            try:
                self._mmap_file.close()
            except Exception:
                pass
        self._mmap_file = None

    def _write_mmap(self, frame: VideoFrame) -> None:
        if self._mmap is None:
            return
        import threading

        lock = self.__dict__.setdefault("_mmap_lock", threading.Lock())
        with lock:
            self._write_mmap_locked(frame)

    def _write_mmap_locked(self, frame: VideoFrame) -> None:
        if self._mmap is None:
            return
        out_w = self._live_width if self._live_width > 0 else min(frame.width, _LIVE_MAX_WIDTH)
        out_h = self._live_height if self._live_height > 0 else min(frame.height, _LIVE_MAX_HEIGHT)
        rgb, width, height = _scale_rgb(frame.rgb, frame.width, frame.height, out_w, out_h)
        payload_size = LIVE_HEADER_SIZE + len(rgb) + LIVE_FOOTER_SIZE
        if payload_size > self._mmap_size:
            return
        if self._prev_live_ms is not None and frame.ms > self._prev_live_ms:
            delta = (frame.ms - self._prev_live_ms) / 1000.0
            if delta > 0:
                self._capture_fps = 1.0 / delta
        self._prev_live_ms = frame.ms
        header = format_live_header(
            self._frame_index % 1000000000,
            width,
            height,
            self._request_fps,
            self._capture_fps,
        )
        footer = format_live_footer(width, height)
        self._mmap.seek(0)
        self._mmap.write(header + rgb + footer)

    def enableLiveStream(self, profileId: Any, fileName: str, tsOption: Any = 0, maxFileIndex: int = 0) -> int:
        try:
            self._hls[int(profileId)] = _HlsWriter(fileName, int(maxFileIndex or 0))
            return 0
        except Exception:
            return -1

    def disableLiveStream(self, profileId: Any) -> None:
        writer = self._hls.pop(int(profileId), None)
        if writer is not None:
            writer.close()

    def getNewestFrameAsJpeg(self, width: int = 0, height: int = 0) -> Optional[bytes]:
        frame = self._latest
        if frame is None:
            return None
        try:
            return _jpeg_bytes(frame.rgb, frame.width, frame.height, int(width or 0), int(height or 0))
        except Exception:
            return None

    def enablePacketCapture(self, *args: Any, **kwargs: Any) -> int:
        # No pcap driver in the open backend. -2 matches the historical
        # "driver isn't installed" code consumed by PacketCaptureStream.
        return -2


def _usb_index(uri: str) -> Optional[int]:
    text = uri.strip()
    lowered = text.lower()
    for prefix in ("usb:", "camera:", "device:"):
        if lowered.startswith(prefix):
            try:
                return int(text.split(":", 1)[1] or "0")
            except ValueError:
                return 0
    return None


class FFmpegLog:
    """Logging hook. PyAV does not expose FFmpeg's log callback here."""

    def __init__(self, name: str = "", prefix: str = "") -> None:
        self.name = name
        self.prefix = prefix

    def open(self, logFn: Any = None, level: int = 0) -> int:
        return 0

    def flush(self) -> int:
        return 0

    def close(self) -> None:
        return None


def getLocalCameraNames() -> list[str]:
    if sys.platform == "win32":
        return _probe_opencv_cameras()
    if sys.platform.startswith("linux"):
        return sorted(glob.glob("/dev/video*"))
    if sys.platform == "darwin":
        return sorted(glob.glob("/dev/video*"))
    return []


def getHardwareDevicesList() -> list[str]:
    """Hardware video decoders usable on this PC (Options > hardware acceleration)."""
    try:
        from av.codec.hwaccel import hwdevices_available

        available = set(hwdevices_available())
    except Exception:
        return []
    return [name for name in _HW_PREFERENCE if name in available]


def _probe_opencv_cameras() -> list[str]:
    try:
        import cv2
    except Exception:
        return []
    names = []
    backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
    for index in range(4):
        capture = cv2.VideoCapture(index, backend)
        try:
            if capture.isOpened():
                names.append("usb:%d" % index)
        finally:
            capture.release()
    return names


kPacketCaptureErrCodes = {
    -1: "insufficient privileges",
    -2: "packet capture is not available in the open capture backend",
    -3: "capture already enabled",
}


def _iter_video_frames(path: str):
    av = _av()
    container = av.open(path)
    try:
        stream = _video_stream(container)
        width = int(stream.codec_context.width or 0)
        height = int(stream.codec_context.height or 0)
        for decoded in container.decode(stream):
            ms = _frame_ms(decoded, 0)
            array = decoded.to_ndarray(format="rgb24")
            yield ms, array.tobytes(), int(array.shape[1] or width), int(array.shape[0] or height), width, height
    finally:
        container.close()


def _packet_stamps(container: Any, stream: Any) -> list[int]:
    """Presentation timestamps in ms from the demuxer, without decoding."""
    time_base = float(stream.time_base or 0) or (1.0 / 1000.0)
    raw = []
    for packet in container.demux(stream):
        if packet.pts is not None and packet.size:
            raw.append(int(packet.pts))
    raw.sort()
    stamps: list[int] = []
    last = -1
    for pts in raw:
        ms = int(round(pts * time_base * 1000.0))
        if stamps and ms <= last:
            ms = last + 1
        stamps.append(ms)
        last = ms
    return stamps


def _timestamp_list(path: str) -> list[int]:
    # Demux only. Decoding every frame to RGB took seconds per clip and the
    # search, thumbnail and cleaner paths call this for every result.
    av = _av()
    container = av.open(path)
    try:
        return _packet_stamps(container, _video_stream(container))
    finally:
        container.close()


def getMsList(path: str, logFn: Any = None) -> list[int]:
    """Frame timestamps relative to the first frame, in milliseconds."""
    _ = logFn
    try:
        stamps = _timestamp_list(path)
    except Exception:
        return []
    if not stamps:
        return []
    origin = stamps[0]
    return [stamp - origin for stamp in stamps]


def getDuration(path: str, logFn: Any = None) -> int:
    _ = logFn
    stamps = getMsList(path, logFn)
    if not stamps:
        return 0
    if len(stamps) == 1:
        return 0
    return int(stamps[-1] - stamps[0])


def getRealClipSize(path: str, logFn: Any = None) -> tuple[int, int]:
    _ = logFn
    try:
        av = _av()
        container = av.open(path)
        try:
            stream = container.streams.video[0]
            return (int(stream.codec_context.width or 0), int(stream.codec_context.height or 0))
        finally:
            container.close()
    except Exception:
        return (0, 0)


def _write_mp4(path: str, frames: list[tuple[int, bytes, int, int]]) -> bool:
    if not frames:
        return False
    av = _av()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    _ms0, _rgb0, width, height = frames[0]
    width = _even(width)
    height = _even(height)
    container = av.open(path, mode="w")
    try:
        stream = container.add_stream("libx264", rate=15)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        origin = frames[0][0]
        last_tick = -1
        for ms, rgb, fw, fh in frames:
            array = _rgb_ndarray(rgb, fw, fh)[:height, :width]
            video = av.VideoFrame.from_ndarray(array, format="rgb24")
            # Same 1/15 s encoder ticks as _ClipWriter; keep them strictly increasing.
            last_tick = max(last_tick + 1, int(round(max(0, ms - origin) * _CLIP_FPS / 1000.0)))
            video.pts = last_tick
            video.time_base = fractions.Fraction(1, _CLIP_FPS)
            for packet in stream.encode(video):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()
    return True


def remuxSubClip(
    origFilePath: str,
    newPath: str,
    startOffset: int,
    endOffset: int,
    configDir: str = "",
    logFn: Any = None,
) -> int:
    """Trim a clip. Returns the first written frame offset, or -1."""
    _ = configDir, logFn
    try:
        stamps_and_frames = []
        for ms, rgb, width, height, _iw, _ih in _iter_video_frames(origFilePath):
            stamps_and_frames.append((ms, rgb, width, height))
        if not stamps_and_frames:
            return -1
        origin = stamps_and_frames[0][0]
        selected = []
        for ms, rgb, width, height in stamps_and_frames:
            offset = ms - origin
            if offset < int(startOffset):
                continue
            if offset > int(endOffset):
                break
            selected.append((ms, rgb, width, height))
        if not selected:
            return -1
        if not _write_mp4(newPath, selected):
            return -1
        return int(selected[0][0] - origin)
    except Exception:
        return -1


def remuxClip(
    fileList: list,
    savePath: str,
    startMs: int,
    endMs: int,
    dataDir: Any = None,
    extras: Any = None,
    logFn: Any = None,
    progressFn: Any = None,
) -> int:
    """Join clip files between absolute timestamps. 0 on success, -1 on failure."""
    _ = dataDir, logFn
    fmt = str((extras or {}).get("format") or "").lower() if isinstance(extras, dict) else ""
    if fmt in ("jpg", "jpeg", "png") or str(savePath).lower().endswith((".jpg", ".jpeg", ".png")):
        return _save_still_frame(fileList, savePath, int(startMs))
    try:
        selected: list[tuple[int, bytes, int, int]] = []
        entries = []
        for item in fileList or []:
            if isinstance(item, (list, tuple)):
                entries.append((item[0], int(item[1]) if len(item) > 1 and item[1] is not None else None))
            else:
                entries.append((item, None))
        total = max(1, len(entries))
        for index, (path, file_start) in enumerate(entries):
            origin = None
            for ms, rgb, width, height, _iw, _ih in _iter_video_frames(path):
                if origin is None:
                    origin = ms
                absolute = ms if file_start is None else int(file_start) + (ms - origin)
                if absolute < int(startMs) or absolute > int(endMs):
                    continue
                selected.append((absolute, rgb, width, height))
            if progressFn is not None:
                progressFn(int(100 * (index + 1) / total))
        if not selected:
            return -1
        selected.sort(key=lambda item: item[0])
        if not _write_mp4(savePath, selected):
            return -1
        return 0
    except Exception:
        return -1


def _save_still_frame(fileList: list, savePath: str, targetMs: int) -> int:
    """Write the decoded frame nearest ``targetMs`` as a still image.

    Export Frame and Mark as Missed ask for a zero-length range
    (``startMs == endMs``). A frame almost never lands on that exact
    millisecond, so the mp4 path selected nothing and failed. Pick the
    closest frame instead. 0 on success, -1 on failure.
    """
    try:
        from PIL import Image

        best = None
        for item in fileList or []:
            if isinstance(item, (list, tuple)):
                path = item[0]
                file_start = int(item[1]) if len(item) > 1 and item[1] is not None else None
            else:
                path, file_start = item, None
            origin = None
            for ms, rgb, width, height, _iw, _ih in _iter_video_frames(path):
                if origin is None:
                    origin = ms
                absolute = ms if file_start is None else int(file_start) + (ms - origin)
                gap = abs(absolute - int(targetMs))
                if best is None or gap < best[0]:
                    best = (gap, rgb, width, height)
                if absolute > int(targetMs) and best is not None and gap > best[0]:
                    break
            if best is not None and best[0] == 0:
                break
        if best is None:
            return -1
        image = Image.frombytes("RGB", (int(best[2]), int(best[3])), bytes(best[1]))
        lower = str(savePath).lower()
        image.save(savePath, "PNG" if lower.endswith(".png") else "JPEG", quality=92)
        return 0
    except Exception:
        return -1


class _ClipUtils:
    remuxClip = staticmethod(remuxClip)
    remuxSubClip = staticmethod(remuxSubClip)
    getRealClipSize = staticmethod(getRealClipSize)


clip_utils = _ClipUtils()


class ClipFrame:
    def __init__(self, rgb: bytes, width: int, height: int, ms: int, input_size: tuple[int, int]) -> None:
        self.rgb = bytes(rgb)
        self.width = int(width)
        self.height = int(height)
        self.ms = int(ms)
        self._input_size = input_size
        self.dummy = False
        self.wasResized = (self.width, self.height) != input_size
        self.filename = ""
        self._storage = ctypes.create_string_buffer(self.rgb)
        self.buffer = ctypes.cast(self._storage, ctypes.c_void_p)

    def asPil(self):
        from PIL import Image

        return Image.frombytes("RGB", (self.width, self.height), self.rgb)

    def getLargeFrame(self):
        return self


class ClipReader:
    """Random-access reader for MP4 clips written by :class:`StreamReader`.

    Frames are decoded on demand. Decoding a whole 60 s clip up front held
    about 1.6 GB of RGB for a 1280x480 camera and left the search player
    black while it loaded.
    """

    _CACHE_FRAMES = 48

    def __init__(self, logFn: Any = None) -> None:
        self._log_fn = logFn
        self._path = ""
        self._stamps: list[int] = []
        self._pts: list[int] = []
        self._cache: "collections.OrderedDict[int, tuple[bytes, int, int]]" = collections.OrderedDict()
        self._container = None
        self._stream = None
        self._decoder = None
        self._decoded_index = -1
        self._input_size = (0, 0)
        self._out_w = 0
        self._out_h = 0
        self._index = -1
        self._muted = False
        self._has_audio = False
        self._file_start = 0
        self._overlay = None

    def open(self, path: str, outW: int = 0, outH: int = 0, fileStart: int = 0, extras: Any = None) -> bool:
        self.close()
        self._path = path
        self._file_start = int(fileStart or 0)
        self._overlay = None
        if isinstance(extras, dict) and (extras.get("boxList") or extras.get("zonesList")):
            from backEnd.native.box_overlay import BoxOverlay

            overlay = BoxOverlay(extras.get("boxList"), extras.get("boxLabels"), extras.get("zonesList"))
            self._overlay = overlay if overlay else None
        self._out_w = int(outW or 0)
        self._out_h = int(outH or 0)
        try:
            av = _av()
            container = av.open(path)
            try:
                stream = _video_stream(container)
                time_base = float(stream.time_base or 0) or (1.0 / 1000.0)
                raw = sorted(
                    int(packet.pts) for packet in container.demux(stream) if packet.pts is not None and packet.size
                )
            finally:
                container.close()
            if not raw:
                return False
            # Same rounding as getMsList so search offsets land on the same frame.
            origin = int(round(raw[0] * time_base * 1000.0))
            stamps: list[int] = []
            last = -1
            for pts in raw:
                relative = int(round(pts * time_base * 1000.0)) - origin
                if stamps and relative <= last:
                    relative = last + 1
                stamps.append(relative)
                last = relative
            self._pts = raw
            self._stamps = stamps
            self._container = av.open(path)
            self._stream = _video_stream(self._container)
            try:
                self._stream.thread_type = "AUTO"
            except Exception:
                pass
            ctx = self._stream.codec_context
            self._input_size = (int(ctx.width or 0), int(ctx.height or 0))
            self._index = -1
            return True
        except Exception as exc:
            logging.getLogger(__name__).warning("ClipReader could not open %s: %r", path, exc)
            self.close()
            return False

    def close(self) -> None:
        self._decoder = None
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
        self._container = None
        self._stream = None
        self._decoded_index = -1
        self._cache.clear()
        self._stamps = []
        self._pts = []
        self._index = -1

    def getInputSize(self) -> tuple[int, int]:
        return self._input_size

    def hasAudio(self) -> bool:
        return self._has_audio

    def setMute(self, mute: bool) -> None:
        self._muted = bool(mute)

    def setOutputSize(self, resolution: tuple[int, int]) -> None:
        if not resolution:
            return
        self._out_w = int(resolution[0] or 0)
        self._out_h = int(resolution[1] or 0)

    def seek(self, offsetMs: int) -> Optional[ClipFrame]:
        if not self._stamps:
            return None
        chosen = bisect.bisect_left(self._stamps, int(offsetMs))
        chosen = min(chosen, len(self._stamps) - 1)
        self._index = chosen
        return self._frame_at(chosen)

    def getNextFrame(self) -> Optional[ClipFrame]:
        if not self._stamps:
            return None
        nxt = self._index + 1
        if nxt >= len(self._stamps):
            return None
        self._index = nxt
        return self._frame_at(nxt)

    def getPrevFrame(self) -> Optional[ClipFrame]:
        if not self._stamps or self._index <= 0:
            return None
        self._index -= 1
        return self._frame_at(self._index)

    def getNextFrameOffset(self) -> int:
        nxt = self._index + 1
        if nxt < 0 or nxt >= len(self._stamps):
            return -1
        return int(self._stamps[nxt])

    def _remember(self, index: int, value: tuple[bytes, int, int]) -> None:
        self._cache[index] = value
        self._cache.move_to_end(index)
        while len(self._cache) > self._CACHE_FRAMES:
            self._cache.popitem(last=False)

    def _decode(self, index: int) -> Optional[tuple[bytes, int, int]]:
        if index in self._cache:
            self._cache.move_to_end(index)
            return self._cache[index]
        if self._container is None or self._stream is None:
            return None
        # Keep decoding forward when the target is close ahead; otherwise seek
        # to the keyframe before it.
        if self._decoder is None or index <= self._decoded_index or index - self._decoded_index > 90:
            self._container.seek(self._pts[index], stream=self._stream, backward=True, any_frame=False)
            self._decoder = self._container.decode(self._stream)
            self._decoded_index = -1
        found = None
        for decoded in self._decoder:
            if decoded.pts is None:
                current = self._decoded_index + 1
            else:
                current = bisect.bisect_left(self._pts, int(decoded.pts))
                current = min(current, len(self._pts) - 1)
            self._decoded_index = current
            if current < index - self._CACHE_FRAMES:
                continue
            array = decoded.to_ndarray(format="rgb24")
            value = (array.tobytes(), int(array.shape[1]), int(array.shape[0]))
            self._remember(current, value)
            if current >= index:
                found = self._cache.get(index, value)
                break
        else:
            self._decoder = None
        if found is None and self._cache:
            # Missing tail frame: show the nearest decoded frame.
            nearest = min(self._cache, key=lambda key: abs(key - index))
            found = self._cache[nearest]
        return found

    def _frame_at(self, index: int) -> Optional[ClipFrame]:
        try:
            value = self._decode(index)
        except Exception as exc:
            logging.getLogger(__name__).warning("ClipReader decode failed in %s: %r", self._path, exc)
            self._decoder = None
            value = None
        if value is None:
            return None
        rgb, width, height = value
        scaled, out_w, out_h = _scale_rgb(rgb, width, height, self._out_w, self._out_h)
        if self._overlay is not None:
            try:
                scaled = self._overlay.draw(scaled, out_w, out_h, self._file_start + self._stamps[index])
            except Exception as exc:
                logging.getLogger(__name__).warning("Box overlay failed: %r", exc)
        return ClipFrame(scaled, out_w, out_h, self._stamps[index], self._input_size)


def getTimestampFlags(extras: Any = None) -> int:
    _ = extras
    return 0


def GetVideolibModulesList() -> list[str]:
    return []


def GetVideoLibDebugConfigItems() -> list[str]:
    return []


def GetVideoLibLogLevels() -> list[tuple[str, bool]]:
    return []


def SetVideoLibDebugConfig(configStruct: Any = None) -> None:
    _ = configStruct


def SetVideoLibDebugConfigSimple(logFfmpeg: bool = False, modules: Any = None) -> None:
    _ = logFfmpeg, modules
