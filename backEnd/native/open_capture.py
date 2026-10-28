"""PyAV capture, clip IO, live-view, JPEG, and HLS.

This is the open replacement for the closed ``videoLib2`` extension. Call
sites keep using :class:`StreamReader`, :class:`ClipReader`, and the clip
helpers exported from :mod:`backEnd.native.capture`.

USB cameras use OpenCV when it is installed (DirectShow on Windows). RTSP,
HTTP, and files use PyAV, whose wheels ship FFmpeg. Packet capture is not
part of this backend.
"""

from __future__ import annotations

import ctypes
import fractions
import glob
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
        # create_string_buffer copies and appends a NUL. Readers use width*height*3.
        self._storage = ctypes.create_string_buffer(self.rgb)
        self.buffer = ctypes.cast(self._storage, ctypes.c_void_p)

    def getLargeFrame(self) -> "VideoFrame":
        return self

    def asPil(self):
        from PIL import Image

        return Image.frombytes("RGB", (self.width, self.height), self.rgb)


def _scale_rgb(rgb: bytes, width: int, height: int, out_w: int, out_h: int) -> tuple[bytes, int, int]:
    if out_w <= 0 and out_h <= 0:
        return rgb, width, height
    target_w = out_w if out_w > 0 else width
    target_h = out_h if out_h > 0 else height
    if target_w == width and target_h == height:
        return rgb, width, height
    scale = min(target_w / float(width), target_h / float(height))
    new_w = max(1, int(width * scale))
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


def _frame_ms(frame: Any, fallback_ms: int) -> int:
    stamp = getattr(frame, "time", None)
    if stamp is None:
        return int(fallback_ms)
    return int(round(float(stamp) * 1000.0))


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
        video.pts = max(0, ms - self._first_ms)
        video.time_base = self._stream.time_base
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
        av = _av()
        self._container = av.open(self._full_path, mode="w")
        stream = self._container.add_stream("libx264", rate=15)
        stream.width = self._width
        stream.height = self._height
        stream.pix_fmt = "yuv420p"
        stream.time_base = fractions.Fraction(1, 1000)
        self._stream = stream

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

    def _open_av(self, uri: str) -> bool:
        av = _av()
        options = {}
        lowered = uri.lower()
        if lowered.startswith(("rtsp://", "rtsps://", "http://", "https://")):
            options["rtsp_transport"] = "tcp"
        self._container = av.open(uri, options=options or None)
        stream = _video_stream(self._container)
        self._width = int(stream.codec_context.width or 0)
        self._height = int(stream.codec_context.height or 0)
        self._iterator = self._container.decode(stream)
        self._running = True
        if self._record:
            self._writer = _ClipWriter(self)
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

    def getNewFrame(self, liveViewEnabled: bool = False) -> Optional[VideoFrame]:
        if not self._running:
            return None
        try:
            rgb, width, height, _media_ms = self._read_rgb()
        except Exception:
            self._running = False
            return None
        if rgb is None:
            self._running = False
            return None
        # Search and clips use arrival time. A camera PTS that starts at 0 or
        # jumps by seconds never overlaps "today" and opens a new tiny file
        # on every jump.
        ms = int(time.time() * 1000)
        if self._last_ms >= 0 and ms <= self._last_ms:
            ms = self._last_ms + 1
        self._last_ms = ms
        filename = ""
        if self._writer is not None:
            try:
                self._writer.write(rgb, width, height, ms)
                filename = self._writer.relative_path
            except Exception:
                self._writer = None
        frame = VideoFrame(rgb, width, height, ms, filename=filename, dummy=False)
        self._width = width
        self._height = height
        self._latest = frame
        self._frame_index += 1
        if self._mmap is not None and (liveViewEnabled or self._live_enabled):
            self._write_mmap(frame)
        for hls in list(self._hls.values()):
            try:
                hls.write(rgb, width, height, ms)
            except Exception:
                pass
        return frame

    def _read_rgb(self) -> tuple[Optional[bytes], int, int, int]:
        if self._capture is not None:
            ok, bgr = self._capture.read()
            if not ok or bgr is None:
                return None, 0, 0, 0
            rgb_array = bgr[:, :, ::-1]
            height, width = rgb_array.shape[:2]
            ms = int(time.time() * 1000)
            return rgb_array.tobytes(), int(width), int(height), ms
        if self._iterator is None:
            return None, 0, 0, 0
        try:
            decoded = next(self._iterator)
        except StopIteration:
            return None, 0, 0, 0
        array = decoded.to_ndarray(format="rgb24")
        height, width = array.shape[:2]
        ms = _frame_ms(decoded, self._fallback_ms)
        self._fallback_ms = ms + 1
        return array.tobytes(), int(width), int(height), ms

    def getProcSize(self) -> tuple[int, int]:
        return (int(self._width), int(self._height))

    def getInitialFrameBufferSize(self) -> int:
        if self._width > 0 and self._height > 0:
            return int(self._width) * int(self._height) * 3
        return int(self._init_frame_size)

    def setLiveStreamLimits(self, maxRes: Any = None, maxBitrate: Any = None) -> int:
        self._extras["maxRes"] = maxRes
        self._extras["maxBitrate"] = maxBitrate
        return 0

    def flush(self, *args: Any) -> int:
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


def getHardwareDevicesList() -> list[dict[str, str]]:
    return [{"name": name, "kind": "video"} for name in getLocalCameraNames()]


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


def _timestamp_list(path: str) -> list[int]:
    stamps: list[int] = []
    last = -1
    for ms, _rgb, _w, _h, _iw, _ih in _iter_video_frames(path):
        if stamps and ms <= last:
            ms = last + 1
        stamps.append(ms)
        last = ms
    return stamps


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
        for ms, rgb, fw, fh in frames:
            array = _rgb_ndarray(rgb, fw, fh)[:height, :width]
            video = av.VideoFrame.from_ndarray(array, format="rgb24")
            video.pts = max(0, int(ms - origin))
            video.time_base = fractions.Fraction(1, 1000)
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
    _ = dataDir, extras, logFn
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
    """Random-access reader for MP4 clips written by :class:`StreamReader`."""

    def __init__(self, logFn: Any = None) -> None:
        self._log_fn = logFn
        self._path = ""
        self._frames: list[tuple[int, bytes, int, int]] = []
        self._input_size = (0, 0)
        self._out_w = 0
        self._out_h = 0
        self._index = -1
        self._muted = False
        self._has_audio = False

    def open(self, path: str, outW: int = 0, outH: int = 0, fileStart: int = 0, extras: Any = None) -> bool:
        _ = fileStart, extras
        self.close()
        self._path = path
        self._out_w = int(outW or 0)
        self._out_h = int(outH or 0)
        try:
            loaded = []
            input_size = (0, 0)
            origin = None
            last = -1
            for ms, rgb, width, height, in_w, in_h in _iter_video_frames(path):
                if origin is None:
                    origin = ms
                    input_size = (in_w or width, in_h or height)
                relative = ms - origin
                if loaded and relative <= last:
                    relative = last + 1
                loaded.append((relative, rgb, width, height))
                last = relative
            self._frames = loaded
            self._input_size = input_size
            self._index = -1
            return bool(loaded)
        except Exception:
            self._frames = []
            return False

    def close(self) -> None:
        self._frames = []
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
        if not self._frames:
            return None
        target = int(offsetMs)
        chosen = 0
        for index, (ms, _rgb, _w, _h) in enumerate(self._frames):
            chosen = index
            if ms >= target:
                break
        self._index = chosen
        return self._frame_at(chosen)

    def getNextFrame(self) -> Optional[ClipFrame]:
        if not self._frames:
            return None
        nxt = self._index + 1
        if nxt >= len(self._frames):
            return None
        self._index = nxt
        return self._frame_at(nxt)

    def getPrevFrame(self) -> Optional[ClipFrame]:
        if not self._frames or self._index <= 0:
            return None
        self._index -= 1
        return self._frame_at(self._index)

    def getNextFrameOffset(self) -> int:
        nxt = self._index + 1
        if nxt < 0 or nxt >= len(self._frames):
            return -1
        return int(self._frames[nxt][0])

    def _frame_at(self, index: int) -> ClipFrame:
        ms, rgb, width, height = self._frames[index]
        scaled, out_w, out_h = _scale_rgb(rgb, width, height, self._out_w, self._out_h)
        return ClipFrame(scaled, out_w, out_h, ms, self._input_size)


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
