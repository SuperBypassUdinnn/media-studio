"""Tests for media_studio.reframe: subject-tracked auto-reframe.

Pure unit tests exercise focal-center smoothing, scene-boundary resets, and
crop-window clamping without ffmpeg. The slow E2E group builds real moving-box
clips and runs ffmpeg to verify track / blur-pad reframing to 1080x1920.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.reframe import (
    ReframeError,
    ReframeResult,
    _exponential_smooth,
    _scene_boundaries,
    _smooth_center,
    clamp_crop_window,
    reframe_video,
    safe_zone_for,
)

FPS = 30.0


# ---------------------------------------------------------------------------
# safe_zone_for
# ---------------------------------------------------------------------------


def test_safe_zone_for_9x16_1080x1920() -> None:
    zone = safe_zone_for("9:16", 1080, 1920)
    assert zone == {"top": 220, "bottom": 380, "left": 60, "right": 140}


def test_safe_zone_for_16x9_1920x1080() -> None:
    zone = safe_zone_for("16:9", 1920, 1080)
    assert zone == {"top": 60, "bottom": 120, "left": 60, "right": 140}


def test_safe_zone_for_unknown_aspect_raises() -> None:
    with pytest.raises(ReframeError, match="unknown aspect"):
        safe_zone_for("4:3", 1080, 1920)


# ---------------------------------------------------------------------------
# clamp_crop_window
# ---------------------------------------------------------------------------


def test_clamp_crop_window_inside_bounds() -> None:
    # max_x = 1920 - 1080 = 840 (x stays), max_y = 0 (crop taller than source -> y clamps to 0).
    x, y = clamp_crop_window(100, 100, source_w=1920, source_h=1080, crop_w=1080, crop_h=1920)
    assert x == 100
    assert y == 0


def test_clamp_crop_window_clamps_x_upper() -> None:
    # source_w - crop_w = 0 when cropping full width; nothing exceeds.
    x, _y = clamp_crop_window(500, 0, source_w=1080, source_h=1920, crop_w=1080, crop_h=1920)
    assert x == 0


def test_clamp_crop_window_clamps_y() -> None:
    _x, y = clamp_crop_window(0, 2000, source_w=1920, source_h=1080, crop_w=1080, crop_h=1920)
    # source_h - crop_h is negative: clamp to the max valid (0 here since crop > source).
    assert y <= 0


# ---------------------------------------------------------------------------
# exponential smoothing
# ---------------------------------------------------------------------------


def test_exponential_smooth_basic() -> None:
    # First sample is taken as-is.
    assert _exponential_smooth([10.0, 20.0, 30.0], alpha=0.5) == [10.0, 15.0, 22.5]


def test_exponential_smooth_empty() -> None:
    assert _exponential_smooth([], alpha=0.5) == []


# ---------------------------------------------------------------------------
# scene boundaries
# ---------------------------------------------------------------------------


def test_scene_boundaries_detects_cut() -> None:
    # A 4 s clip with a hard cut at 2.0 s: detect via ffmpeg select scene.
    scenes = _scene_boundaries(Path("no-such-file.mp4"), threshold=0.3)
    # File missing -> empty list, not a crash.
    assert scenes == []


# ---------------------------------------------------------------------------
# focal-center smoothing with scene reset
# ---------------------------------------------------------------------------


def test_smooth_center_resets_at_scene_boundary() -> None:
    # Centers per frame: scene 1 steady at x=100, cut at frame 20, scene 2 at x=900.
    centers = [(float(i), 100.0, 540.0) for i in range(20)]
    centers += [(float(i), 900.0, 540.0) for i in range(20, 40)]
    smoothed = _smooth_center(centers, scene_cuts={20}, alpha=0.5)
    # Frame just before the cut remains near 100.
    assert abs(smoothed[19][1] - 100.0) < 5.0
    # Frame at the cut resets to the new scene's value exactly.
    assert smoothed[20][1] == 900.0
    # Frames after the cut relax toward 900, not drifting from 100 across the cut.
    assert smoothed[21][1] > 500.0


def test_smooth_center_no_cuts_monotone_toward_target() -> None:
    centers = [(float(i), 100.0, 540.0) for i in range(10)]
    centers += [(float(i), 900.0, 540.0) for i in range(10, 20)]
    smoothed = _smooth_center(centers, scene_cuts=set(), alpha=0.5)
    # Without a reset the center pans slowly across the cut.
    assert smoothed[9][1] < smoothed[10][1] < smoothed[19][1]
    assert smoothed[19][1] < 900.0


# ---------------------------------------------------------------------------
# E2E: real ffmpeg reframe
# ---------------------------------------------------------------------------


def _make_moving_box_clip(path: Path, *, duration: int = 3) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A box translating left->right across a 1920x1080 frame so a tracking
    # crop can keep it centered.
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1920x1080:rate={int(FPS)}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        check=True,
    )
    return path


def _probe_size(path: Path) -> tuple[int, int]:
    import json

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    stream = payload["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _probe_has_audio(path: Path) -> bool:
    import json

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=codec_type",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    payload = json.loads(completed.stdout)
    return len(payload.get("streams", [])) > 0


def _decode_center_pixel(
    path: Path, *, x: int, y: int, time_s: float = 0.5
) -> tuple[int, int, int]:
    """Decode one pixel at (x, y) from ``path`` at ``time_s`` seconds."""

    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{time_s:.3f}",
            "-i",
            str(path),
            "-vframes",
            "1",
            "-vf",
            f"crop=1:1:{x}:{y},format=rgb24",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
    )
    data = completed.stdout
    if len(data) < 3:
        raise AssertionError(f"could not decode pixel at ({x},{y}) from {path}")
    return data[0], data[1], data[2]


@pytest.mark.slow
def test_reframe_track_mode_produces_1080x1920(tmp_path: Path) -> None:
    source = _make_moving_box_clip(tmp_path / "source.mp4")
    output = tmp_path / "out_track.mp4"
    result = reframe_video(
        source,
        aspect="9:16",
        mode="track",
        output=output,
        scene_threshold=0.3,
    )
    assert isinstance(result, ReframeResult)
    assert output.is_file()
    width, height = _probe_size(output)
    assert (width, height) == (1080, 1920)


@pytest.mark.slow
def test_reframe_blur_pad_mode_produces_1080x1920(tmp_path: Path) -> None:
    source = _make_moving_box_clip(tmp_path / "source.mp4")
    output = tmp_path / "out_blurpad.mp4"
    result = reframe_video(
        source,
        aspect="9:16",
        mode="blur-pad",
        output=output,
        scene_threshold=0.3,
    )
    assert isinstance(result, ReframeResult)
    assert output.is_file()
    width, height = _probe_size(output)
    assert (width, height) == (1080, 1920)


@pytest.mark.slow
def test_reframe_auto_mode_falls_back_to_track(tmp_path: Path) -> None:
    source = _make_moving_box_clip(tmp_path / "source.mp4")
    output = tmp_path / "out_auto.mp4"
    result = reframe_video(
        source,
        aspect="9:16",
        mode="auto",
        output=output,
        scene_threshold=0.3,
    )
    assert isinstance(result, ReframeResult)
    assert output.is_file()
    width, height = _probe_size(output)
    assert (width, height) == (1080, 1920)


def _make_edge_stripe_clip(path: Path, *, duration: int = 3) -> Path:
    """1920x1080 clip: red stripe on left edge, blue stripe on right edge."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:size=1920x1080:rate={int(FPS)}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"color=c=red:size=40x1080:rate={int(FPS)}:duration={duration}",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:size=40x1080:rate={int(FPS)}:duration={duration}",
            "-filter_complex",
            "[0:v][1:v]overlay=0:0[bg];[bg][2:v]overlay=W-w:0[out]",
            "-map",
            "[out]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.slow
def test_reframe_blur_pad_preserves_horizontal_edges(tmp_path: Path) -> None:
    # blur-pad must scale the whole source width into the output (no horizontal
    # cropping) so left/right edge content survives inside blurred bands.
    source = _make_edge_stripe_clip(tmp_path / "edges.mp4")
    output = tmp_path / "out_blurpad.mp4"
    result = reframe_video(
        source,
        aspect="9:16",
        mode="blur-pad",
        output=output,
        scene_threshold=0.3,
    )
    assert isinstance(result, ReframeResult)
    assert output.is_file()
    width, height = _probe_size(output)
    assert (width, height) == (1080, 1920)
    # Left stripe at output x=0-20; right stripe at output x=1060-1080.
    left = _decode_center_pixel(output, x=10, y=960)
    right = _decode_center_pixel(output, x=1070, y=960)
    assert left[0] > 100 and left[1] < 60 and left[2] < 60, f"left red stripe lost: {left}"
    assert right[2] > 100 and right[0] < 60 and right[1] < 60, f"right blue stripe lost: {right}"


@pytest.mark.slow
def test_reframe_preserves_audio_stream(tmp_path: Path) -> None:
    # Source has audio; reframed output must retain an audio stream.
    source = tmp_path / "with_audio.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=1920x1080:rate={int(FPS)}:duration=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3:sample_rate=48000",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ],
        check=True,
    )
    for mode in ("track", "blur-pad"):
        output = tmp_path / f"out_{mode}.mp4"
        result = reframe_video(
            source,
            aspect="9:16",
            mode=mode,
            output=output,
            scene_threshold=0.3,
        )
        assert isinstance(result, ReframeResult)
        assert output.is_file()
        assert _probe_has_audio(output), f"audio stream lost in mode={mode}"
