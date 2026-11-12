"""Tests for FFmpeg transitions (media_studio.transitions).

Real-ffmpeg cases are marked ``slow`` and exercise FFmpeg 9.0.2 end to end.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio import media
from media_studio.transitions import (
    TRANSITION_KINDS,
    TransitionError,
    TransitionResult,
    apply_transition,
)

KINDS: tuple[str, ...] = (
    "whip_pan_left",
    "whip_pan_right",
    "zoom_punch_in",
    "zoom_punch_out",
    "rgb_split_hit",
    "flash_white",
    "light_leak_warm",
    "slide_up",
)


def test_transition_kinds_match_spec() -> None:
    """All eight transition kinds are locked by the design spec."""

    assert TRANSITION_KINDS == KINDS


def test_apply_transition_rejects_unknown_kind(tmp_path: Path) -> None:
    """An unknown kind raises TransitionError before ffmpeg is touched."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    with pytest.raises(TransitionError, match="unknown transition kind"):
        apply_transition(clip_a, clip_b, kind="not_a_kind", output=tmp_path / "out.mp4")


@pytest.mark.parametrize("bad_duration", [0, -1, -250, 10000])
def test_apply_transition_rejects_bad_duration(tmp_path: Path, bad_duration: int) -> None:
    """Non-positive durations are rejected."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    with pytest.raises(TransitionError, match="duration_ms"):
        apply_transition(
            clip_a,
            clip_b,
            kind="whip_pan_left",
            output=tmp_path / "out.mp4",
            duration_ms=bad_duration,
        )


def _make_clip(destination: Path, duration: float = 1.0) -> Path:
    """Render one synthetic animated clip with real ffmpeg."""

    completed = subprocess.run(
        [
            media.FFMPEG,
            "-hide_banner",
            "-nostats",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=size=160x120:rate=30:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-an",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    assert destination.is_file()
    return destination


@pytest.mark.slow
def test_apply_transition_whip_pan_left_executes(tmp_path: Path) -> None:
    """Real ffmpeg xfade run: output duration, resolution, fps, zero errors."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    output = tmp_path / "out.mp4"
    result = apply_transition(
        clip_a,
        clip_b,
        kind="whip_pan_left",
        output=output,
        duration_ms=250,
        width=1080,
        height=1920,
        fps=30,
    )

    assert isinstance(result, TransitionResult)
    assert output.is_file() and output.stat().st_size > 0
    info = media.probe(output)
    assert info.width == 1080
    assert info.height == 1920
    assert info.fps == pytest.approx(30.0, abs=0.5)
    assert info.duration == pytest.approx(1.75, abs=0.2)


@pytest.mark.slow
def test_apply_transition_rgb_split_hit_executes(tmp_path: Path) -> None:
    """Custom geq/rgbashift transition runs cleanly on ffmpeg 9.0.2."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    output = tmp_path / "out.mp4"
    result = apply_transition(
        clip_a,
        clip_b,
        kind="rgb_split_hit",
        output=output,
        duration_ms=250,
        width=1080,
        height=1920,
        fps=30,
    )

    assert isinstance(result, TransitionResult)
    assert result.kind == "rgb_split_hit"
    assert output.is_file()
    info = media.probe(output)
    assert info.width == 1080
    assert info.height == 1920
    assert info.fps == pytest.approx(30.0, abs=0.5)
    assert info.duration == pytest.approx(1.75, abs=0.2)


@pytest.mark.slow
@pytest.mark.parametrize("kind", KINDS)
def test_all_transition_kinds_run_cleanly(kind: str, tmp_path: Path) -> None:
    """Every locked transition kind executes end to end without ffmpeg errors."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    output = tmp_path / f"out-{kind}.mp4"
    result = apply_transition(
        clip_a,
        clip_b,
        kind=kind,
        output=output,
        duration_ms=250,
        width=1080,
        height=1920,
        fps=30,
    )

    assert result.kind == kind
    assert output.is_file()
    info = media.probe(output)
    assert info.width == 1080
    assert info.height == 1920
    assert info.fps == pytest.approx(30.0, abs=0.5)
    assert info.duration > 0.0
