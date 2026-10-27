"""Encode and decode the live-view memory-map headers.

Camera processes publish a frame by writing a fixed-width ASCII header and
then tightly packed RGB bytes into a shared file. Python 3 ``mmap`` slices
are ``bytes``. Treating them as ``str`` (or calling ``str()`` on them) wraps
the header in ``b'...'`` and breaks the integer fields. These helpers keep
the header as text for parsing and the pixels as ``bytes``.

Test-stream header (``kTestLiveHeaderSize`` == 10), matching the reader in
the camera setup wizard::

    1 byte   frame counter % 10
    4 bytes  width
    4 bytes  height
    1 byte   newline

Live header (``kLiveHeaderSize`` == 32), matching ``BaseView._getLiveImage``::

    9 bytes  frame id
    4 bytes  width
    4 bytes  height
    7 bytes  requested fps (``%7.2f``)
    7 bytes  captured fps (``%7.2f``)
    1 byte   newline

A footer of ``%4d%4d`` (width, height) follows the RGB payload for the live
stream. An all-NUL header means the writer has closed the mapping.
"""

from __future__ import annotations

from typing import Optional

from appCommon.CommonStrings import kLiveHeaderSize, kTestLiveHeaderSize

LIVE_HEADER_SIZE = kLiveHeaderSize
TEST_LIVE_HEADER_SIZE = kTestLiveHeaderSize
LIVE_FOOTER_SIZE = 8


def as_bytes(buf: bytes | bytearray | memoryview | str) -> bytes:
    """Return the raw mapping bytes without a ``str()`` wrapper."""
    if isinstance(buf, bytes):
        return buf
    if isinstance(buf, bytearray):
        return bytes(buf)
    if isinstance(buf, memoryview):
        return buf.tobytes()
    if isinstance(buf, str):
        return buf.encode("latin-1")
    return bytes(buf)


def as_text(buf: bytes | bytearray | memoryview | str) -> str:
    """Decode a header as Latin-1 so every byte round-trips, including NUL."""
    if isinstance(buf, str):
        return buf
    return as_bytes(buf).decode("latin-1")


def is_blank(buf: bytes | bytearray | memoryview | str) -> bool:
    raw = as_bytes(buf)
    return raw == b"\x00" * len(raw)


def blank_bytes(size: int) -> bytes:
    return b"\x00" * size


def format_test_live_header(frame_counter: int, width: int, height: int) -> bytes:
    """Header written by the camera test stream."""
    text = "%d%4d%4d\n" % (int(frame_counter) % 10, int(width), int(height))
    if len(text) != TEST_LIVE_HEADER_SIZE:
        raise ValueError(
            f"test live header is {len(text)} bytes, expected {TEST_LIVE_HEADER_SIZE}"
        )
    return text.encode("ascii")


def parse_test_live_header(header: bytes | bytearray | memoryview | str) -> tuple[int, int, int]:
    """Return ``(frame_counter, width, height)``."""
    text = as_text(header)
    if len(text) < TEST_LIVE_HEADER_SIZE:
        raise ValueError("test live header is short")
    text = text[:TEST_LIVE_HEADER_SIZE]
    if is_blank(text):
        raise ValueError("test live header is blank")
    return int(text[0]), int(text[1:5]), int(text[5:9])


def format_live_header(
    frame_id: int,
    width: int,
    height: int,
    request_fps: float,
    capture_fps: float,
) -> bytes:
    """Header consumed by the monitor view. Width precedes height."""
    text = "%09d%4d%4d%7.2f%7.2f\n" % (
        int(frame_id),
        int(width),
        int(height),
        float(request_fps),
        float(capture_fps),
    )
    if len(text) != LIVE_HEADER_SIZE:
        raise ValueError(
            f"live header is {len(text)} bytes, expected {LIVE_HEADER_SIZE}"
        )
    return text.encode("ascii")


def format_live_footer(width: int, height: int) -> bytes:
    text = "%4d%4d" % (int(width), int(height))
    if len(text) != LIVE_FOOTER_SIZE:
        raise ValueError("live footer must be 8 bytes")
    return text.encode("ascii")


def parse_live_header(
    header: bytes | bytearray | memoryview | str,
) -> Optional[tuple[int, int, int, float, float]]:
    """Return ``(frame_id, width, height, request_fps, capture_fps)``.

    ``None`` means the writer has cleared the mapping.
    """
    text = as_text(header)
    if len(text) < LIVE_HEADER_SIZE:
        raise ValueError("live header is short")
    text = text[:LIVE_HEADER_SIZE]
    if is_blank(text):
        return None
    frame_id = int(text[0:9])
    width = int(text[9:13])
    height = int(text[13:17])
    request_fps_text, capture_fps_text = text[17:].split()
    return frame_id, width, height, float(request_fps_text), float(capture_fps_text)


def parse_live_footer(footer: bytes | bytearray | memoryview | str) -> tuple[int, int]:
    text = as_text(footer)
    if len(text) < LIVE_FOOTER_SIZE:
        raise ValueError("live footer is short")
    text = text[:LIVE_FOOTER_SIZE]
    return int(text[0:4]), int(text[4:8])
