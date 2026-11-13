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


def test_apply_transition_default_duration_ms_is_250() -> None:
    """Standalone apply_transition keeps the 250 ms default (promo overrides to ~180)."""

    import inspect

    sig = inspect.signature(apply_transition)
    assert sig.parameters["duration_ms"].default == 250


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


def test_apply_transition_command_uses_high_quality_intermediate_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """apply_transition must use crf 14, preset veryfast, lanczos scale, limited range."""
    from media_studio import transitions

    captured: dict = {}

    def fake_ffmpeg(command):
        captured["command"] = list(command)
        # Delegate to real ffmpeg so the output file exists for the probe in apply_transition.
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-y", *command], capture_output=True, text=True
        )

    monkeypatch.setattr(transitions, "_ffmpeg", fake_ffmpeg)

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0)
    _make_clip(clip_b, 1.0)

    apply_transition(
        clip_a,
        clip_b,
        kind="whip_pan_left",
        output=tmp_path / "out.mp4",
        duration_ms=250,
        width=1080,
        height=1920,
        fps=30,
    )

    cmd = captured["command"]
    assert "-preset" in cmd and cmd[cmd.index("-preset") + 1] == "veryfast", cmd
    assert "-crf" in cmd and cmd[cmd.index("-crf") + 1] == "14", cmd
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p", cmd
    # The normalisation scale must carry flags=lanczos.
    norm_idx = None
    for i, part in enumerate(cmd):
        if part == "-filter_complex":
            norm_idx = i + 1
            break
    assert norm_idx is not None
    vf = cmd[norm_idx]
    assert "scale=w=1080:h=1920" in vf, vf
    assert "flags=lanczos" in vf, f"no lanczos in normalisation filter: {vf}"
    assert "format=yuv420p" in vf, f"no limited range format in normalisation filter: {vf}"


def _make_clip(destination: Path, duration: float = 1.0, *, with_audio: bool = False) -> Path:
    """Render one synthetic animated clip with real ffmpeg."""

    command: list[str] = [
        media.FFMPEG,
        "-hide_banner",
        "-nostats",
        "-y",
    ]
    if with_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size=160x120:rate=30:duration={duration}",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:duration={duration}:sample_rate=48000",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "pcm_s16le",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-shortest",
            ]
        )
    else:
        command.extend(
            [
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
            ]
        )
    command.append(str(destination))
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
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
def test_apply_transition_aligns_audio_to_output_duration(tmp_path: Path) -> None:
    """clip_a has audio, clip_b is silent: output audio/video match out_duration."""

    clip_a = tmp_path / "a.mp4"
    clip_b = tmp_path / "b.mp4"
    _make_clip(clip_a, 1.0, with_audio=True)
    _make_clip(clip_b, 1.0, with_audio=False)

    output = tmp_path / "out.mp4"
    result = apply_transition(
        clip_a,
        clip_b,
        kind="whip_pan_left",
        output=output,
        duration_ms=250,
        width=160,
        height=120,
        fps=30,
    )

    assert isinstance(result, TransitionResult)
    assert output.is_file() and output.stat().st_size > 0
    info = media.probe(output)
    expected = 1.0 + 1.0 - result.duration_ms / 1000.0
    assert info.duration == pytest.approx(expected, abs=0.05)
    assert info.audio_codec is not None
    assert info.width == 160
    assert info.height == 120
    assert info.fps == pytest.approx(30.0, abs=0.5)


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
