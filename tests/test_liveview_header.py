"""Live-view mmap headers stay bytes, and the field layout matches the readers."""

from appCommon.LiveViewBuffer import (
    blank_bytes,
    format_live_footer,
    format_live_header,
    format_test_live_header,
    is_blank,
    parse_live_footer,
    parse_live_header,
    parse_test_live_header,
)


def test_test_header_roundtrip():
    header = format_test_live_header(17, 320, 240)
    assert isinstance(header, bytes)
    assert header == b"7 320 240\n"
    assert parse_test_live_header(header) == (7, 320, 240)
    # str() on a bytes slice is the Python 3 bug this helper exists to avoid.
    assert str(header) != header.decode("ascii")


def test_live_header_width_precedes_height():
    header = format_live_header(42, 640, 480, 15.0, 12.5)
    assert isinstance(header, bytes)
    assert len(header) == 32
    assert parse_live_header(header) == (42, 640, 480, 15.0, 12.5)
    footer = format_live_footer(640, 480)
    assert footer == b" 640 480"
    assert parse_live_footer(footer) == (640, 480)


def test_blank_header_means_writer_closed():
    blank = blank_bytes(32)
    assert is_blank(blank)
    assert parse_live_header(blank) is None
