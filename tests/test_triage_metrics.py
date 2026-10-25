"""Tests for the triage metrics: exposure, edges, motion, hashing, shake, and cuts.

Every metric is measured on generated media, so the numbers are checked against content the test
itself created rather than against a recorded fixture.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from media_studio import media


@pytest.fixture(scope="module")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("triage")


def generate(destination: Path, arguments: list[str]) -> Path:
    completed = subprocess.run(
        [media.FFMPEG, "-hide_banner", "-nostats", "-y", *arguments, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-500:]
    return destination


@pytest.fixture(scope="module")
def moving(workdir: Path) -> Path:
    return generate(
        workdir / "moving.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


@pytest.fixture(scope="module")
def held(workdir: Path) -> Path:
    return generate(
        workdir / "held.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=gray:size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


@pytest.fixture(scope="module")
def dark(workdir: Path) -> Path:
    """Dark but not crushed: #0a0a0a lands at luma 25 in limited range."""

    return generate(
        workdir / "dark.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=#0a0a0a:size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


@pytest.fixture(scope="module")
def black(workdir: Path) -> Path:
    return generate(
        workdir / "black.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=black:size=160x90:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )


def test_exposure_separates_gray_from_dark_and_crushed(
    moving: Path, held: Path, dark: Path, black: Path
) -> None:
    held_stats = media.frame_stats(held)
    dark_stats = media.frame_stats(dark)
    black_stats = media.frame_stats(black)

    assert 100 < held_stats.mean < 130  # mid gray is 126 in limited range
    assert dark_stats.mean < 40  # dark, and reported as dark
    assert dark_stats.low_clip == 0.0  # but not crushed
    assert black_stats.mean < 20  # video black is luma 16
    assert black_stats.low_clip == 1.0  # every sampled frame sits in the shadows
    assert media.frame_stats(moving).mean > dark_stats.mean


def test_edge_strength_separates_detail_from_a_flat_frame(moving: Path, held: Path) -> None:
    assert media.edge_strength(moving) > 5.0
    assert media.edge_strength(held) < 1.0


def test_motion_separates_a_moving_clip_from_a_held_one(moving: Path, held: Path) -> None:
    assert media.motion_level(moving) > 1.0
    assert media.motion_level(held) < 0.1


def test_perceptual_hash_matches_a_duplicate_and_differs_otherwise(
    workdir: Path, held: Path, moving: Path
) -> None:
    copy = workdir / "held-copy.mp4"
    copy.write_bytes(held.read_bytes())

    assert media.perceptual_hash(held) == media.perceptual_hash(copy)
    assert media.perceptual_hash(held) != media.perceptual_hash(moving)


def test_scene_cuts_find_a_hard_cut(workdir: Path) -> None:
    cut = generate(
        workdir / "cut.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=red:size=160x90:rate=24:duration=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:size=160x90:rate=24:duration=1",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )

    cuts = media.scene_cuts(cut)

    assert cuts, "expected the red-to-blue change to register as a cut"
    assert any(abs(moment - 1.0) < 0.25 for moment in cuts)


def test_shake_is_reported_for_a_stable_clip(held: Path, tmp_path: Path) -> None:
    assert media.shake_level(held, scratch=tmp_path) < 1.0
