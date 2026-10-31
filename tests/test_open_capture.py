"""PyAV capture, clips, JPEG, live-view mmap, and HLS."""

import os

import pytest

from appCommon.LiveViewBuffer import (
    LIVE_HEADER_SIZE,
    parse_live_footer,
    parse_live_header,
)
from tests.open_backend_support import ClipLog, write_moving_rect

av = pytest.importorskip("av")

from backEnd.native.capture import (  # noqa: E402
    ClipReader,
    StreamReader,
    clip_utils,
    getDuration,
    getMsList,
    getRealClipSize,
    kPacketCaptureErrCodes,
    remuxClip,
)


def test_file_round_trip_jpeg_mmap_and_clips(tmp_path):
    video = tmp_path / "sample.mp4"
    write_moving_rect(video, frames=8)
    clips = ClipLog()
    reader = StreamReader(
        "FrontDoor",
        clips,
        None,
        str(tmp_path),
        str(tmp_path),
        str(tmp_path),
        None,
        True,
    )
    assert reader.open(str(video), {"maxClipMs": 60000})
    live = tmp_path / "FrontDoor.live"
    assert reader.open_mmap(str(live))
    playlist = tmp_path / "live.m3u8"
    assert reader.enableLiveStream(1, str(playlist), 0, 0) == 0
    seen = []
    while True:
        frame = reader.getNewFrame(True)
        if frame is None:
            break
        seen.append(frame)
    assert len(seen) >= 4
    assert seen[0].width == 320
    assert seen[0].height == 240
    assert [frame.ms for frame in seen] == sorted(frame.ms for frame in seen)
    assert seen[-1].ms > seen[0].ms
    jpeg = reader.getNewestFrameAsJpeg(160, 120)
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
    header = parse_live_header(open(live, "rb").read(LIVE_HEADER_SIZE))
    assert header is not None
    _frame_id, width, height, _request, _capture = header
    assert width > 0 and height > 0
    blob = open(live, "rb").read()
    footer = blob[LIVE_HEADER_SIZE + width * height * 3 : LIVE_HEADER_SIZE + width * height * 3 + 8]
    assert parse_live_footer(footer) == (width, height)
    reader.disableLiveStream(1)
    reader.close()
    assert os.path.isfile(playlist)
    playlist_text = playlist.read_text(encoding="utf-8")
    assert "#EXTM3U" in playlist_text
    assert clips.clips
    recorded = os.path.join(str(tmp_path), clips.clips[0][0])
    assert os.path.isfile(recorded)
    stamps = getMsList(recorded)
    assert len(stamps) >= 4
    assert stamps[0] == 0
    assert getDuration(recorded) > 0
    assert getRealClipSize(recorded)[0] > 0
    clip = ClipReader(None)
    assert clip.open(recorded, 0, 120, 0, None)
    frame = clip.seek(0)
    assert frame is not None and frame.asPil().size[1] <= 120
    assert clip.getNextFrame() is not None
    assert reader.enablePacketCapture("x", "rtsp://example", None) == -2
    assert -2 in kPacketCaptureErrCodes


def test_remux_keeps_a_time_range(tmp_path):
    video = tmp_path / "sample.mp4"
    write_moving_rect(video, frames=8)
    trimmed = tmp_path / "trim.mp4"
    start = clip_utils.remuxSubClip(str(video), str(trimmed), 0, 400, str(tmp_path), None)
    assert start >= 0
    assert getDuration(str(trimmed)) <= 500
    joined = tmp_path / "joined.mp4"
    assert remuxClip([(str(trimmed), 1000)], str(joined), 1000, 5000, str(tmp_path), {}, None) == 0
    assert os.path.getsize(joined) > 0


def test_export_single_frame_as_jpeg_between_frames(tmp_path):
    """Export Frame / Mark as Missed ask for start == end; must not fail."""
    from PIL import Image

    video = tmp_path / "sample.mp4"
    write_moving_rect(video, frames=8)
    still = tmp_path / "frame.jpg"
    # 1013 ms is between frames: the old mp4 path selected nothing (-1).
    assert remuxClip([(str(video), 1000)], str(still), 1013, 1013, str(tmp_path),
                     {"format": "jpg", "drawBoxes": False}, None) == 0
    assert Image.open(still).size == (320, 240)
    assert remuxClip([], str(tmp_path / "none.jpg"), 5, 5, None, {"format": "jpg"}, None) == -1
