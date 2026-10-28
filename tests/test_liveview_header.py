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


def test_live_header_parses_when_capture_rate_has_no_leading_space():
    """1000 fps is '1000.00', which used to glue the two fields together."""
    header = format_live_header(3, 320, 240, 15.0, 1000.0)
    assert len(header) == 32
    parsed = parse_live_header(header)
    assert parsed is not None
    _frame_id, width, height, request_fps, capture_fps = parsed
    assert (width, height) == (320, 240)
    assert request_fps == 15.0
    assert capture_fps == 1000.0
