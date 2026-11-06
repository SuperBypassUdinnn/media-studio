"""Tests for media_studio.beatramp: beat-synced velocity ramp.

Unit tests cover ``schedule_ramp_events`` trigger identification, greedy
selection, tie-breaks, and map overrides. The E2E group renders a real
1920x1080 testsrc2 clip through ffmpeg.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.beatramp import (
    BEATRAMP_KINDS,
    MIN_GAP_S,
    TRIGGER_MAP,
    BeatRampError,
    RampEvent,
    apply_ramp_events,
    schedule_ramp_events,
)
from media_studio.ingest import probe_media

FPS = 60.0
RESOLUTION = "1920x1080"


def _make_clip(path: Path, *, size: str = "1920x1080", duration: int = 2) -> Path:
    """Render a tiny real clip so E2E tests exercise real ffmpeg."""

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
            f"testsrc2=size={size}:rate=60:duration={duration}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


def _video_codec(path: Path) -> str:
    info = probe_media(path)
    return info.video_codec


def _video_duration(path: Path) -> float:
    return probe_media(path).duration


BEATS = {
    "beats": [0.0, 0.3, 2.0],
    "downbeats": [0.0],
    "window": 0.25,
    "energy": [1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    "sections": [
        {"start": 0.0, "end": 1.0, "level": "high"},
        {"start": 1.0, "end": 2.5, "level": "low"},
        {"start": 2.5, "end": 4.0, "level": "high"},
    ],
}


def test_kinds_and_trigger_map() -> None:
    assert frozenset({"fast_slow_fast", "slow_snap", "flash_freeze"}) == BEATRAMP_KINDS
    assert TRIGGER_MAP == {
        "downbeat": "fast_slow_fast",
        "snare": "slow_snap",
        "build_peak": "flash_freeze",
    }
    assert MIN_GAP_S == 0.5


def test_schedule_emits_downbeat_and_build_peak_only() -> None:
    events = schedule_ramp_events(BEATS, offset=0.0, duration=4.0)
    assert [(e.time, e.kind, e.trigger) for e in events] == [
        (0.0, "fast_slow_fast", "downbeat"),
        (2.0, "flash_freeze", "build_peak"),
    ]


def test_collision_tie_break_downbeat_wins() -> None:
    beats = dict(BEATS)
    beats["energy"] = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    events = schedule_ramp_events(beats, offset=0.0, duration=4.0)
    assert [(e.time, e.kind, e.trigger) for e in events] == [
        (0.0, "fast_slow_fast", "downbeat"),
        (2.0, "flash_freeze", "build_peak"),
    ]


def test_map_override_changes_kind() -> None:
    events = schedule_ramp_events(
        BEATS,
        offset=0.0,
        duration=4.0,
        map={"downbeat": "slow_snap"},
    )
    kinds = {e.kind for e in events}
    assert "slow_snap" in kinds
    downbeat = next(e for e in events if e.trigger == "downbeat")
    assert downbeat.kind == "slow_snap"


def test_offset_shifts_beat_times_to_shot_local() -> None:
    beats = {
        "beats": [0.5, 2.3, 2.8, 4.0],
        "downbeats": [2.3],
        "window": 0.5,
        "energy": [0.1, 0.9],
        "sections": [],
    }
    events = schedule_ramp_events(beats, offset=2.0, duration=2.0)
    assert [(e.time, e.kind, e.trigger) for e in events] == [
        (0.3, "fast_slow_fast", "downbeat"),
    ]


@pytest.mark.slow
def test_apply_ramp_events_e2e(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _make_clip(source, duration=2)
    events = [RampEvent(time=0.5, kind="fast_slow_fast", trigger="downbeat")]
    destination = tmp_path / "out.mp4"

    result = apply_ramp_events(
        source,
        events,
        destination,
        fps=FPS,
        resolution=RESOLUTION,
        codec="h264",
    )

    assert destination.is_file()
    assert _video_codec(destination) == "h264"
    assert abs(_video_duration(destination) - 2.0) <= 0.3
    assert result.events == tuple(events)
    assert result.resolution == RESOLUTION
    assert result.fps == FPS


@pytest.mark.slow
def test_apply_ramp_events_changes_speed_for_fast_slow_fast(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _make_clip(source, duration=2)
    events = [RampEvent(time=0.5, kind="fast_slow_fast", trigger="downbeat")]
    destination = tmp_path / "out.mp4"

    apply_ramp_events(source, events, destination, fps=FPS, resolution=RESOLUTION)

    assert abs(_video_duration(destination) - 2.0) > 0.1


@pytest.mark.slow
def test_apply_ramp_events_changes_speed_for_slow_snap(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _make_clip(source, duration=2)
    events = [RampEvent(time=0.5, kind="slow_snap", trigger="snare")]
    destination = tmp_path / "out.mp4"

    apply_ramp_events(source, events, destination, fps=FPS, resolution=RESOLUTION)

    assert abs(_video_duration(destination) - 2.0) > 0.1


def test_apply_ramp_events_rejects_below_floor_resolution(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _make_clip(source, duration=2)
    events = [RampEvent(time=0.5, kind="fast_slow_fast", trigger="downbeat")]
    destination = tmp_path / "out.mp4"

    with pytest.raises(BeatRampError, match="below the 1920x1080 minimum floor"):
        apply_ramp_events(
            source,
            events,
            destination,
            fps=FPS,
            resolution="1280x720",
            codec="h264",
        )


def test_zero_events_single_copy_encode_pass(tmp_path: Path) -> None:
    source = tmp_path / "source.mp4"
    _make_clip(source, duration=2)
    destination = tmp_path / "out.mp4"

    result = apply_ramp_events(source, [], destination, fps=FPS, resolution=RESOLUTION)

    assert destination.is_file()
    assert _video_codec(destination) == "h264"
    assert result.events == ()
    assert result.resolution == RESOLUTION
