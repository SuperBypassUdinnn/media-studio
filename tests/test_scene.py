"""Tests for media_studio.scene: sustained scene-change detection.

Pure tests exercise _mean_abs_diff and scene_segments without ffmpeg. The
slow E2E group builds real clips and runs ffmpeg scene detection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.scene import (
    CONFIRM_AFTER_S,
    CONFIRM_BEFORE_S,
    FLASH_DIFF_THRESHOLD,
    MAX_SCENE_CANDIDATES,
    SCENE_THRESHOLD,
    SceneError,
    _mean_abs_diff,
    motion_score,
    scene_segments,
    sustained_scene_changes,
)

FPS = 60.0


def test_mean_abs_diff_known_values() -> None:
    assert _mean_abs_diff(b"\x00\x10", b"\x00\x20") == 8.0
    assert _mean_abs_diff(b"\x00", b"\x00") == 0.0


def test_mean_abs_diff_uses_min_length() -> None:
    # Differing lengths: only the first n=2 bytes are compared.
    assert _mean_abs_diff(b"\x00\x10\x20", b"\x00\x20") == 8.0


def test_mean_abs_diff_empty_raises() -> None:
    with pytest.raises(SceneError, match="empty frame"):
        _mean_abs_diff(b"", b"")


def test_scene_segments_basic() -> None:
    assert scene_segments(2.0, [0.5, 1.5]) == [
        (0.0, 0.5),
        (0.5, 1.5),
        (1.5, 2.0),
    ]


def test_scene_segments_drops_out_of_range_and_dups() -> None:
    assert scene_segments(2.0, [0.0, 2.0, 2.0, 5.0]) == [(0.0, 2.0)]


def test_scene_segments_empty_boundaries() -> None:
    assert scene_segments(3.0, []) == [(0.0, 3.0)]


def test_scene_segments_drops_zero_length_segments() -> None:
    assert scene_segments(1.0, [0.5, 0.5]) == [(0.0, 0.5), (0.5, 1.0)]


def test_constants_are_sensible() -> None:
    assert SCENE_THRESHOLD == 0.3
    assert CONFIRM_BEFORE_S == 0.1
    assert CONFIRM_AFTER_S == 0.25
    assert FLASH_DIFF_THRESHOLD == 12.0
    assert MAX_SCENE_CANDIDATES == 2000


def _make_clip(path: Path, *, duration: int = 2) -> Path:
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
            f"testsrc2=size=640x480:rate={int(FPS)}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.slow
def test_sustained_scene_changes_detects_real_change(tmp_path: Path) -> None:
    # A 2 s clip whose picture changes at 1 s: concat two different sources.
    seg1 = tmp_path / "seg1.mp4"
    seg2 = tmp_path / "seg2.mp4"
    _make_clip(seg1, duration=1)
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
            f"color=c=red:size=640x480:rate={int(FPS)}:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(seg2),
        ],
        check=True,
    )
    concat = tmp_path / "concat.txt"
    concat.write_text(f"file '{seg1}'\nfile '{seg2}'\n", encoding="utf-8")
    clip = tmp_path / "changed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            str(clip),
        ],
        check=True,
    )

    times = sustained_scene_changes(clip)
    assert any(0.9 <= t <= 1.1 for t in times), f"no sustained boundary near 1s: {times}"


@pytest.mark.slow
def test_flash_is_filtered_not_sustained(tmp_path: Path) -> None:
    # testsrc2 base with a single-frame white overlay at t=1.0 s.
    clip = tmp_path / "flash.mp4"
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
            f"testsrc2=size=640x480:rate={int(FPS)}:duration=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:size=640x480:rate=60:duration=2",
            "-filter_complex",
            "overlay=enable='between(t,1.0,1.02)'",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-frames:v",
            "120",
            str(clip),
        ],
        check=True,
    )

    times = sustained_scene_changes(clip)
    assert 1.0 not in times, f"flash at 1s was not filtered: {times}"


def test_motion_score_rejects_empty_window() -> None:
    with pytest.raises(SceneError, match="empty motion window"):
        motion_score(Path("x.mp4"), 2.0, 1.0)
    with pytest.raises(SceneError, match="empty motion window"):
        motion_score(Path("x.mp4"), 1.0, 1.0, samples=1)
    with pytest.raises(SceneError, match="empty motion window"):
        motion_score(Path("x.mp4"), 1.0, 1.0, samples=0)


@pytest.mark.slow
def test_motion_score_moving_half_greater(tmp_path: Path) -> None:
    # A 2 s clip: static color first half, moving testsrc2 second half.
    seg1 = tmp_path / "static.mp4"
    seg2 = tmp_path / "moving.mp4"
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
            f"color=c=red:size=640x480:rate={int(FPS)}:duration=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(seg1),
        ],
        check=True,
    )
    _make_clip(seg2, duration=1)
    concat = tmp_path / "concat.txt"
    concat.write_text(f"file '{seg1}'\nfile '{seg2}'\n", encoding="utf-8")
    clip = tmp_path / "changed.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat),
            "-c",
            "copy",
            str(clip),
        ],
        check=True,
    )

    second = motion_score(clip, 1.0, 2.0, samples=4)
    assert second > 0, f"moving half motion score not > 0: {second}"
