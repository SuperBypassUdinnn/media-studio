"""Tests for media_studio.beatfx: beat-mapped micro-effects.

Unit tests cover schedule parsing, the private filter builders, auto-schedule
policy, and destination/copy/validation behaviour. The E2E group renders real
1920x1080 testsrc2 clips through ffmpeg.
"""

from __future__ import annotations

import itertools
import json
import subprocess
from pathlib import Path

import pytest

from media_studio.beatfx import (
    BeatFxError,
    ScheduledEffect,
    _flash_filter,
    _hue_filter,
    _punch_filter,
    _rgb_filter,
    _shake_filter,
    apply_effects,
    auto_schedule,
    parse_schedule,
)
from media_studio.ingest import probe_media

FPS = 60.0
RESOLUTION = "1920x1080"


def _make_clip(path: Path, *, size: str = "1920x1080", duration: int = 1) -> Path:
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


class TestParseSchedule:
    def test_round_trips_sorted_by_time(self) -> None:
        payload = [
            {"time": 2.0, "kind": "shake", "params": {}},
            {"time": 0.5, "kind": "punch", "params": {}},
            {"time": 1.0, "kind": "flash", "params": {}},
        ]
        effects = parse_schedule(payload)
        assert [e.time for e in effects] == [0.5, 1.0, 2.0]
        assert [e.kind for e in effects] == ["punch", "flash", "shake"]

    def test_empty_list_is_valid(self) -> None:
        assert parse_schedule([]) == []

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(BeatFxError, match="rgb"):
            parse_schedule([{"time": 1.0, "kind": "nope", "params": {}}])

    def test_negative_time_raises(self) -> None:
        with pytest.raises(BeatFxError, match="non-negative"):
            parse_schedule([{"time": -0.5, "kind": "shake", "params": {}}])


class TestFilters:
    def test_shake_uses_animated_crop(self) -> None:
        f = _shake_filter(0.25, 0.5, FPS, 1920, 1080)
        assert "sin(" in f
        assert "between(t," in f

    def test_punch_uses_zoompan(self) -> None:
        f = _punch_filter(0.25, 0.55, FPS, 1920, 1080)
        assert "zoompan" in f
        assert "if(between(" in f

    def test_flash_uses_drawbox_white(self) -> None:
        f = _flash_filter(0.0, 2 / FPS, FPS, 1920, 1080)
        assert "drawbox" in f
        assert "color=white" in f

    def test_rgb_uses_split_and_overlays(self) -> None:
        f = _rgb_filter(0.0, 0.2, FPS, 1920, 1080)
        assert "split=3" in f
        assert "overlay" in f

    def test_hue_uses_hue_expr(self) -> None:
        f = _hue_filter(0.0, 0.4, FPS, 1920, 1080)
        assert "hue=h=" in f


class TestAutoSchedule:
    def _beats_payload(self) -> dict:
        beats = [i * 0.5 for i in range(8)]
        energy = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]
        return {"beats": beats, "energy": energy, "window": 0.5}

    def test_exactly_eight_effects(self) -> None:
        effects = auto_schedule(self._beats_payload(), offset=0.0, duration=4.0)
        assert len(effects) == 8

    def test_min_gap_between_consecutive_times(self) -> None:
        effects = auto_schedule(self._beats_payload(), offset=0.0, duration=4.0)
        times = [e.time for e in effects]
        for earlier, later in itertools.pairwise(times):
            assert later - earlier >= 0.2

    def test_first_kind_is_punch(self) -> None:
        effects = auto_schedule(self._beats_payload(), offset=0.0, duration=4.0)
        assert effects[0].kind == "punch"
        assert effects[0].params == {}

    def test_offset_shifts_into_local_range(self) -> None:
        effects = auto_schedule(self._beats_payload(), offset=2.0, duration=4.0)
        times = [e.time for e in effects]
        assert all(0.0 <= t < 2.0 for t in times)

    def test_offset_outside_range_raises(self) -> None:
        with pytest.raises(BeatFxError, match="no beats fall inside"):
            auto_schedule(self._beats_payload(), offset=10.0, duration=4.0)


class TestApplyEffects:
    def test_real_flash_shake_punch_pipeline(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        effects = [
            ScheduledEffect(time=0.2, kind="flash", params={}),
            ScheduledEffect(time=0.5, kind="shake", params={}),
            ScheduledEffect(time=0.93, kind="punch", params={"zoom": 1.08, "duration": 0.15}),
        ]
        result = apply_effects(
            source,
            effects=effects,
            fps=FPS,
            resolution=RESOLUTION,
            codec="h264",
        )
        assert result.destination.is_file()
        assert result.size_bytes > 0
        codec, _ = self._ffprobe_video(result.destination)
        assert codec == "h264"
        assert abs(probe_media(result.destination).duration - 1.0) < 0.2

    def test_overlapping_windows_succeed(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        effects = [
            ScheduledEffect(time=0.3, kind="hue", params={}),
            ScheduledEffect(time=0.4, kind="flash", params={}),
        ]
        result = apply_effects(
            source, effects=effects, fps=FPS, resolution=RESOLUTION, codec="h264"
        )
        assert result.destination.is_file()
        assert result.size_bytes > 0

    @staticmethod
    def _ffprobe_video(path: Path) -> tuple[str, str]:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        streams = json.loads(out).get("streams", [])
        video = next(s for s in streams if s.get("codec_type") == "video")
        return str(video.get("codec_name")), str(video.get("pix_fmt") or "")


class TestValidation:
    def test_resolution_below_floor_raises(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4", size="1280x720")
        with pytest.raises(BeatFxError, match="below the 1920x1080 minimum floor"):
            apply_effects(source, effects=[], fps=FPS, resolution="1280x720", codec="h264")

    def test_malformed_resolution_raises(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        with pytest.raises(BeatFxError, match="must be WxH"):
            apply_effects(source, effects=[], fps=FPS, resolution="bogus", codec="h264")

    def test_unknown_codec_raises(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "source.mp4")
        with pytest.raises(BeatFxError, match="h264"):
            apply_effects(source, effects=[], fps=FPS, resolution=RESOLUTION, codec="av1")

    def test_missing_source_raises(self) -> None:
        with pytest.raises(BeatFxError, match="no such file"):
            apply_effects(Path("/tmp/opencode/does-not-exist.mp4"), effects=[])
