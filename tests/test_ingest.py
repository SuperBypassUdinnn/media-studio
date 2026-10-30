"""Tests for media_studio.ingest: Resolve-safe transcoding decisions and execution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio.ingest import (
    PROFILES,
    IngestError,
    _parse_rate,
    ingest,
    ingest_folder,
    is_resolve_safe,
    plan_ingest,
    probe_media,
)


def _make_clip(path: Path, *, codec: str = "libx264", size: str = "320x240") -> Path:
    """Render a tiny real clip so probing exercises ffprobe, not a stub."""

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
            f"testsrc2=size={size}:rate=30:duration=1",
            "-c:v",
            codec,
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )
    return path


class TestParseRate:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("30000/1001", 29.97),
            ("30/1", 30.0),
            ("25", 25.0),
            ("0/0", 0.0),
            ("", 0.0),
            ("garbage", 0.0),
        ],
    )
    def test_parses_common_forms(self, text: str, expected: float) -> None:
        assert _parse_rate(text) == expected


class TestIsResolveSafe:
    @pytest.mark.parametrize("codec", ["h264", "hevc", "av1", "vp9"])
    def test_rejects_licensed_consumer_codecs(self, codec: str) -> None:
        info = probe_media(_make_clip(Path("/tmp/opencode/probe.mp4"), codec="libx264"))
        object.__setattr__(info, "video_codec", codec)
        assert is_resolve_safe(info) is False

    @pytest.mark.parametrize("codec", ["dnxhd", "prores", "rawvideo"])
    def test_accepts_native_codecs(self, codec: str) -> None:
        info = probe_media(_make_clip(Path("/tmp/opencode/probe.mp4")))
        object.__setattr__(info, "video_codec", codec)
        assert is_resolve_safe(info) is True


class TestProbeMedia:
    def test_reads_stream_layout(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4", size="640x360")
        info = probe_media(clip)
        assert info.video_codec == "h264"
        assert (info.width, info.height) == (640, 360)
        assert info.fps == pytest.approx(30.0, abs=0.01)
        assert info.duration == pytest.approx(1.0, abs=0.1)
        assert info.has_audio is False

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="no such file"):
            probe_media(tmp_path / "ghost.mp4")

    def test_audio_only_raises(self, tmp_path: Path) -> None:
        wav = tmp_path / "sound.wav"
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
                "sine=frequency=440:duration=1",
                str(wav),
            ],
            check=True,
        )
        with pytest.raises(IngestError, match="no video stream"):
            probe_media(wav)


class TestPlanIngest:
    def test_unknown_profile_raises(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        with pytest.raises(IngestError, match="unknown ingest profile"):
            plan_ingest(clip, profile="ultra")

    def test_h264_source_is_reported(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        plan = plan_ingest(clip, profile="proxy")
        assert any("h264" in reason for reason in plan.reasons)
        assert plan.destination.name == "clip.proxy.mov"

    def test_small_source_is_not_scaled(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4", size="640x360")
        plan = plan_ingest(clip, profile="proxy")
        assert plan.resized is False
        assert "scale=" not in " ".join(plan.command())

    def test_large_source_is_scaled_into_profile_box(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4", size="3840x2160")
        plan = plan_ingest(clip, profile="proxy")
        assert plan.resized is True
        assert (plan.scale_width, plan.scale_height) == (1920, 1080)

    def test_scaled_dimensions_stay_even(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4", size="1920x1080")
        plan = plan_ingest(clip, profile="proxy")
        # Already inside the box, so nothing is scaled.
        assert plan.resized is False

    def test_explicit_destination_is_kept(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        target = tmp_path / "elsewhere" / "out.mov"
        plan = plan_ingest(clip, destination=target, profile="hqx")
        assert plan.destination == target

    def test_command_selects_profile_and_pcm_audio(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        argv = plan_ingest(clip, profile="hqx").command()
        assert "dnxhr_hqx" in argv
        assert "yuv422p10le" in argv
        assert "pcm_s24le" in argv
        assert argv[0] == "ffmpeg"

    def test_constant_rate_source_is_not_retimed(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        plan = plan_ingest(clip, profile="proxy")
        assert plan.normalized_fps is False
        assert "fps=" not in " ".join(plan.command())

    def test_variable_rate_source_is_pinned(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        info = probe_media(clip)
        # Pretend ffprobe reported a drifting average, as a phone capture does.
        object.__setattr__(info, "avg_fps", info.fps - 2.0)
        assert info.is_variable_rate is True

    def test_constant_rate_info_is_not_flagged_as_variable(self, tmp_path: Path) -> None:
        clip = _make_clip(tmp_path / "clip.mp4")
        info = probe_media(clip)
        assert info.is_variable_rate is False


class TestIngest:
    def test_skips_a_source_that_is_already_safe(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "clip.mp4")
        # First transcode to DNxHR, then ingest that result.
        first = ingest(source, tmp_path / "clip.proxy.mov", profile="proxy")
        assert first.skipped is False

        second = ingest(first.destination, tmp_path / "again.mov", profile="proxy")
        assert second.skipped is True
        assert second.destination == first.destination

    def test_force_transcodes_a_safe_source(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "clip.mp4")
        first = ingest(source, tmp_path / "clip.proxy.mov", profile="proxy")
        second = ingest(first.destination, tmp_path / "copy.mov", profile="proxy", force=True)
        assert second.skipped is False
        assert second.destination == tmp_path / "copy.mov"

    def test_writes_a_resolve_safe_file(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "clip.mp4")
        result = ingest(source, tmp_path / "out.mov", profile="proxy")
        assert result.destination.is_file()
        assert result.size_bytes > 0

        info = probe_media(result.destination)
        assert info.video_codec == "dnxhd"
        assert is_resolve_safe(info) is True

    def test_creates_missing_destination_directory(self, tmp_path: Path) -> None:
        source = _make_clip(tmp_path / "clip.mp4")
        target = tmp_path / "deep" / "nested" / "out.mov"
        ingest(source, target, profile="proxy")
        assert target.is_file()

    @pytest.mark.parametrize("rate", [24, 25, 60])
    def test_preserves_the_source_frame_rate(self, tmp_path: Path, rate: int) -> None:
        source = tmp_path / f"clip-{rate}.mp4"
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
                f"testsrc2=size=320x240:rate={rate}:duration=1",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(source),
            ],
            check=True,
        )
        result = ingest(source, tmp_path / f"out-{rate}.mov", profile="proxy")
        assert probe_media(result.destination).fps == pytest.approx(rate, abs=0.01)


class TestIngestFolder:
    def test_ignores_non_footage_files(self, tmp_path: Path) -> None:
        _make_clip(tmp_path / "src" / "a.mp4")
        _make_clip(tmp_path / "src" / "b.mp4")
        (tmp_path / "src" / "notes.txt").write_text("ignore me")
        (tmp_path / "src" / "still.png").write_bytes(b"")

        results = ingest_folder(tmp_path / "src", tmp_path / "out", profile="proxy")
        assert len(results) == 2
        assert {r.source.name for r in results} == {"a.mp4", "b.mp4"}
        assert all(r.destination.parent == tmp_path / "out" for r in results)

    def test_requires_a_directory(self, tmp_path: Path) -> None:
        with pytest.raises(IngestError, match="not a directory"):
            ingest_folder(tmp_path / "nope")


class TestProfiles:
    def test_three_profiles_exist(self) -> None:
        assert set(PROFILES) == {"proxy", "hq", "hqx"}

    def test_proxy_caps_at_1080p(self) -> None:
        assert PROFILES["proxy"].max_width == 1920
        assert PROFILES["proxy"].max_height == 1080

    def test_hqx_is_ten_bit(self) -> None:
        assert PROFILES["hqx"].pix_fmt == "yuv422p10le"
