"""Tests for media_studio.ramp: velocity ramping with optical flow interpolation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import media_studio.cli as cli_module
from media_studio.cli import main
from media_studio.ingest import IngestError
from media_studio.ramp import (
    RAMP_CURVES,
    RampError,
    RampResult,
    SpeedSegment,
    apply_speed_ramp,
    calculate_ramp_segments,
)


def test_ramp_curves_available():
    assert "fast_slow_fast" in RAMP_CURVES
    assert "slow_snap" in RAMP_CURVES
    assert "flash_freeze" in RAMP_CURVES


def test_calculate_ramp_segments_math():
    segments = calculate_ramp_segments(
        clip_duration=6.0,
        slow_window=(2.0, 4.0),
        curve="fast_slow_fast",
        slow_factor=0.25,
        fast_factor=2.0,
    )
    assert len(segments) == 3
    # Phase 1: 0.0 to 2.0 at 2.0x (takes 1.0s)
    assert segments[0].start_time == 0.0
    assert segments[0].end_time == 2.0
    assert segments[0].speed_factor == 2.0
    # Phase 2: 2.0 to 4.0 at 0.25x (takes 8.0s)
    assert segments[1].speed_factor == 0.25
    # Phase 3: 4.0 to 6.0 at 2.0x (takes 1.0s)
    assert segments[2].speed_factor == 2.0


def test_calculate_ramp_segments_slow_snap():
    segments = calculate_ramp_segments(
        clip_duration=6.0,
        slow_window=(2.0, 4.0),
        curve="slow_snap",
        slow_factor=0.25,
        fast_factor=2.0,
    )
    assert len(segments) == 3
    assert segments[0] == SpeedSegment(0.0, 2.0, 1.0)
    assert segments[1] == SpeedSegment(2.0, 4.0, 0.25)
    assert segments[2] == SpeedSegment(4.0, 6.0, 1.0)


def test_calculate_ramp_segments_flash_freeze():
    segments = calculate_ramp_segments(
        clip_duration=6.0,
        slow_window=(2.0, 4.0),
        curve="flash_freeze",
        slow_factor=0.25,
        fast_factor=2.0,
    )
    assert len(segments) == 3
    assert segments[0] == SpeedSegment(0.0, 2.0, 1.0)
    assert segments[1] == SpeedSegment(2.0, 4.0, 0.1)
    assert segments[2] == SpeedSegment(4.0, 6.0, 1.0)


def test_calculate_ramp_segments_invalid_window():
    with pytest.raises(RampError, match="slow_window start must be strictly less than end"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(3.0, 2.0))

    with pytest.raises(RampError, match="outside clip duration"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 6.0))

    with pytest.raises(RampError, match="negative"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(-0.5, 2.0))


def test_calculate_ramp_segments_invalid_factors():
    with pytest.raises(RampError, match="slow_factor must be positive"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 2.0), slow_factor=0.0)

    with pytest.raises(RampError, match="slow_factor must be positive"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 2.0), slow_factor=-0.5)

    with pytest.raises(RampError, match="fast_factor must be positive"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 2.0), fast_factor=0.0)

    with pytest.raises(RampError, match="unknown ramp curve"):
        calculate_ramp_segments(clip_duration=5.0, slow_window=(1.0, 2.0), curve="warp_speed")


def test_calculate_ramp_segments_window_touches_edges():
    segments = calculate_ramp_segments(
        clip_duration=5.0, slow_window=(0.0, 5.0), curve="fast_slow_fast"
    )
    assert len(segments) == 1
    assert segments[0] == SpeedSegment(0.0, 5.0, 0.25)


def test_apply_speed_ramp_validation(tmp_path: Path):
    dummy = tmp_path / "dummy.mp4"
    dummy.write_bytes(b"empty")

    with pytest.raises(RampError, match="slow_factor must be positive"):
        apply_speed_ramp(dummy, slow_window=(1.0, 2.0), slow_factor=0.0)

    with pytest.raises(RampError, match="unknown ramp curve"):
        apply_speed_ramp(dummy, slow_window=(1.0, 2.0), curve="warp_speed")


def test_apply_speed_ramp_missing_source(tmp_path: Path):
    with pytest.raises(IngestError, match="no such file"):
        apply_speed_ramp(tmp_path / "ghost.mp4", slow_window=(1.0, 2.0))


def _make_clip(path: Path, *, size: str = "320x240", rate: int = 30, duration: int = 2) -> Path:
    """Render a tiny real clip so the render path exercises ffmpeg, not a stub."""

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
            f"testsrc2=size={size}:rate={rate}:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def test_apply_speed_ramp_renders_1080p60(tmp_path: Path):
    source = _make_clip(tmp_path / "source.mp4")
    result = apply_speed_ramp(
        source,
        slow_window=(0.5, 1.0),
        curve="fast_slow_fast",
        slow_factor=0.5,
        fast_factor=2.0,
        fps=60,
    )

    assert result.destination.is_file()
    assert result.size_bytes > 0
    assert result.curve == "fast_slow_fast"
    assert result.fps == 60
    assert result.optical_flow is True
    assert result.fallback_used is False
    # 0.5s at 2x (0.25s) + 0.5s at 0.5x (1.0s) + 1.0s at 2x (0.5s) = 1.75s
    assert result.duration == pytest.approx(1.75, abs=0.01)

    from media_studio.ingest import probe_media

    info = probe_media(result.destination)
    assert info.width == 1920
    assert info.height == 1080
    assert info.fps == pytest.approx(60.0, abs=0.01)


def test_apply_speed_ramp_optical_flow_off_skips_mci(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_clip(tmp_path / "source.mp4")
    seen: list[str] = []
    real_run = subprocess.run

    def fake_run(argv, **kwargs):
        seen.append(" ".join(map(str, argv)))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = apply_speed_ramp(
        source,
        slow_window=(0.5, 1.0),
        optical_flow=False,
        fps=60,
    )

    assert result.destination.is_file()
    assert result.optical_flow is False
    assert result.fallback_used is False
    # With optical flow off, mci must never be attempted (no minterpolate at all).
    assert all("mi_mode=mci" not in command for command in seen)


def test_apply_speed_ramp_falls_back_to_blend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = _make_clip(tmp_path / "source.mp4")

    real_run = subprocess.run
    calls: dict[str, int] = {"optical_flow": 0}

    def fake_run(argv, **kwargs):
        command = " ".join(map(str, argv))
        if "mi_mode=mci" in command:
            calls["optical_flow"] += 1
            if calls["optical_flow"] == 1:
                failed = MagicMock(spec=subprocess.CompletedProcess)
                failed.returncode = 1
                failed.stderr = "ffmpeg: Out of memory while allocating frame pool"
                return failed
        return real_run(argv, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = apply_speed_ramp(source, slow_window=(0.5, 1.0), fps=60)

    assert calls["optical_flow"] == 1
    assert result.fallback_used is True
    assert result.optical_flow is True
    assert result.destination.is_file()
    assert result.size_bytes > 0


def test_speed_ramp_cli_registered() -> None:
    parser = cli_module.build_parser()
    subparsers_action = next(
        a
        for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)  # type: ignore[attr-defined]
    )
    video_parser = subparsers_action.choices["video"]
    video_sub_action = next(
        a
        for a in video_parser._actions
        if isinstance(a, argparse._SubParsersAction)  # type: ignore[attr-defined]
    )
    assert "speed-ramp" in video_sub_action.choices


def test_speed_ramp_cli_help_runs(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        main(["video", "speed-ramp", "--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert "usage: ms video speed-ramp" in out
    assert "--slow-window" in out


def test_speed_ramp_cli_handles_ramp_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(
        cli_module,
        "apply_speed_ramp",
        lambda *a, **k: (_ for _ in ()).throw(RampError("unknown ramp curve 'warp_speed'")),
    )

    exit_code = main(
        [
            "video",
            "speed-ramp",
            str(tmp_path / "x.mp4"),
            "--slow-window",
            "1",
            "2",
            "--curve",
            "fast_slow_fast",
        ]
    )
    assert exit_code == 1
    assert "unknown ramp curve" in capsys.readouterr().err


def test_speed_ramp_cli_renders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    source = _make_clip(tmp_path / "source.mp4")
    seen: dict[str, object] = {}

    def fake_ramp(source, destination=None, **kwargs):
        seen["kwargs"] = kwargs
        destination = destination or tmp_path / "out.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-t",
                "0.5",
                str(destination),
            ],
            check=True,
        )
        return RampResult(
            source=source,
            destination=destination,
            curve="fast_slow_fast",
            duration=0.5,
            fps=60,
            optical_flow=True,
            fallback_used=False,
            size_bytes=destination.stat().st_size,
        )

    monkeypatch.setattr(cli_module, "apply_speed_ramp", fake_ramp)

    exit_code = main(
        [
            "video",
            "speed-ramp",
            str(source),
            "--slow-window",
            "1",
            "2",
            "--curve",
            "slow_snap",
            "--slow-factor",
            "0.5",
            "--fast-factor",
            "2.0",
            "--fps",
            "60",
            "--out",
            str(tmp_path / "ramped.mp4"),
        ]
    )
    assert exit_code == 0
    kwargs = seen["kwargs"]  # type: ignore[assignment]
    assert kwargs["slow_window"] == (1.0, 2.0)
    assert kwargs["curve"] == "slow_snap"
    assert kwargs["slow_factor"] == 0.5
    assert kwargs["fast_factor"] == 2.0
    assert kwargs["fps"] == 60
    assert kwargs["optical_flow"] is True
    out = capsys.readouterr().out
    assert "speed ramp" in out
    assert "blend fallback" not in out
