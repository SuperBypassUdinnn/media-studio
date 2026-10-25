"""Tests for the ffmpeg helpers, exercised against real generated media."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio import media


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("media")


def generate(destination: Path, arguments: list[str]) -> Path:
    completed = subprocess.run(
        [media.FFMPEG, "-hide_banner", "-nostats", "-y", *arguments, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    assert destination.is_file()
    return destination


@pytest.fixture(scope="module")
def video_file(workdir: Path) -> Path:
    # testsrc2 animates, so nothing looks frozen and the freeze check has a clean baseline.
    return generate(
        workdir / "moving.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


@pytest.fixture(scope="module")
def black_file(workdir: Path) -> Path:
    return generate(
        workdir / "black.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=320x180:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


@pytest.fixture(scope="module")
def quiet_then_loud(workdir: Path) -> Path:
    """Two seconds of near silence followed by two seconds of tone."""

    return generate(
        workdir / "quiet-then-loud.wav",
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-filter_complex",
            "[0:a][1:a]concat=n=2:v=0:a=1",
            "-c:a",
            "pcm_s16le",
        ],
    )


def test_probe_reports_geometry_duration_and_codecs(video_file: Path) -> None:
    info = media.probe(video_file)

    assert info.width == 320
    assert info.height == 180
    assert info.fps == pytest.approx(24.0, abs=0.01)
    assert info.duration == pytest.approx(2.0, abs=0.1)
    assert info.video_codec == "h264"
    assert info.audio_codec is None


def test_probe_of_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(media.MediaError):
        media.probe(tmp_path / "absent.mp4")


def test_measure_loudness_returns_a_plausible_reading(quiet_then_loud: Path) -> None:
    loudness = media.measure_loudness(quiet_then_loud, target=-14.0)

    # The tone is the loudest part of a half-silent file, so only the shape is asserted.
    assert -40.0 < loudness.integrated_lufs < -10.0
    assert loudness.true_peak_dbtp <= 0.0
    assert "measured_I=" in loudness.as_loudnorm_arguments()


def test_speech_segments_find_the_tone_and_not_the_silence(quiet_then_loud: Path) -> None:
    segments = media.speech_segments(quiet_then_loud, noise_db=-40.0, min_silence=0.5)

    assert segments, "expected at least one sounding span"
    first_start, first_end = segments[0]
    assert first_start == pytest.approx(2.0, abs=0.3)
    assert first_end == pytest.approx(4.0, abs=0.3)


def test_mean_luminance_separates_black_from_picture(video_file: Path, black_file: Path) -> None:
    # Limited-range video black sits at Y=16, so black is "under 20", not "zero".
    black = media.mean_luminance_at(black_file, 1.0)
    picture = media.mean_luminance_at(video_file, 1.0)

    assert black < 20.0
    assert picture > 30.0
    assert picture > black + 10.0


def test_black_and_frozen_detection_agree_with_the_content(
    video_file: Path, black_file: Path
) -> None:
    assert media.black_segments(black_file, minimum=0.5)
    assert media.black_segments(video_file, minimum=0.5) == []
    assert media.frozen_segments(video_file, minimum=1.0) == []


def test_contact_sheet_is_written(tmp_path: Path, video_file: Path) -> None:
    sheet = media.contact_sheet(video_file, tmp_path / "sheet.png", columns=2, rows=1, width=160)

    assert sheet.is_file()
    assert sheet.stat().st_size > 0
