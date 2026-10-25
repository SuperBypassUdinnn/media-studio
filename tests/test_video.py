"""End-to-end tests for the video path: MLT, melt, loudness, ducking, and QC.

These run the real toolchain on small generated media, so they prove the pipeline rather than
mocking it. Resolution is deliberately tiny to keep the suite fast.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from media_studio import media, video
from media_studio.ledger import stage_asset
from media_studio.manifest import Project, load_project
from media_studio.mlt import AudioPlan, Clip, Timeline
from media_studio.project import scaffold_project

RESOLUTION = "320x180"
FPS = 24
TARGET_LUFS = -14.0


def generate(destination: Path, arguments: list[str]) -> Path:
    completed = subprocess.run(
        [media.FFMPEG, "-hide_banner", "-nostats", "-y", *arguments, str(destination)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr[-800:]
    return destination


@pytest.fixture
def media_sources(tmp_path: Path) -> dict[str, Path]:
    """Three moving clips, a voice track with silence around it, and a music bed."""

    sources = tmp_path / "sources"
    sources.mkdir()
    for name, pattern in (
        ("shot-1.mp4", "testsrc2=size=320x180:rate=24:duration=2"),
        ("shot-2.mp4", "testsrc=size=320x180:rate=24:duration=2"),
        ("shot-3.mp4", "smptebars=size=320x180:rate=24:duration=2"),
    ):
        generate(
            sources / name,
            ["-f", "lavfi", "-i", pattern, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an"],
        )
    generate(
        sources / "voice.wav",
        [
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono:d=2",
            "-filter_complex",
            "[0:a][1:a][2:a]concat=n=3:v=0:a=1",
            "-c:a",
            "pcm_s16le",
        ],
    )
    generate(
        sources / "music.wav",
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=6:sample_rate=48000",
            "-c:a",
            "pcm_s16le",
        ],
    )
    return {
        "shot-1": sources / "shot-1.mp4",
        "shot-2": sources / "shot-2.mp4",
        "shot-3": sources / "shot-3.mp4",
        "voice": sources / "voice.wav",
        "music": sources / "music.wav",
    }


@pytest.fixture
def project_root(tmp_path: Path, media_sources: dict[str, Path]) -> Path:
    root = scaffold_project("video-demo", root=tmp_path).path
    project = load_project(root)
    classes = {
        "shot-1": "video",
        "shot-2": "video",
        "shot-3": "video",
        "voice": "audio",
        "music": "music",
    }
    staged: dict[str, str] = {}
    for asset_id, source in media_sources.items():
        result = stage_asset(
            project,
            class_name=classes[asset_id],
            license_text="CC0",
            local_file=source,
            provider="fixture",
            retrieved_at="2026-10-24T00:00:00+00:00",
        )
        staged[asset_id] = result.relative

    manifest = f"""slug: video-demo
title: Video Demo
deliverables:
  - id: master
    kind: video
    resolution: {RESOLUTION}
    fps: {FPS}
    duration: 6.0
    loudness_lufs: {TARGET_LUFS}
    container: mp4
assets:
"""
    for asset_id, relative in staged.items():
        manifest += f"  {asset_id}: {relative}\n"
    manifest += """video:
  shots:
    - { asset: shot-1 }
    - { asset: shot-2, in: 0.5 }
    - { asset: shot-3 }
  voice: { asset: voice }
  music: { asset: music, gain: 1.0, fade_in: 0.3, fade_out: 0.5, duck: true }
"""
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return root


def test_render_lands_on_the_loudness_target_and_passes_qc(project_root: Path) -> None:
    project = load_project(project_root)

    result = video.render(project, preview=True, keep_intermediate=True)

    assert result.deliverable.is_file()
    assert result.mlt_path.is_file()
    assert result.contact_sheet.is_file()
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert report["checks"]["has_video"] is True
    assert report["checks"]["has_audio"] is True
    assert report["checks"]["loudness_within_half_lu"] is True, report["loudness"]
    assert report["checks"]["true_peak_below_ceiling"] is True, report["loudness"]
    assert report["ok"] is True, report["checks"]
    assert abs(report["loudness"]["distance_lu"]) <= 0.5
    assert report["duration_seconds"] == pytest.approx(6.0, abs=0.5)


def test_render_records_the_ducking_windows_it_derived(project_root: Path) -> None:
    project = load_project(project_root)

    result = video.render(project, preview=True)
    ducking = result.report["ducking"]

    assert isinstance(ducking, dict)
    assert ducking["source"] == "silencedetect on the voice track"
    intervals = ducking["plan_intervals"]
    assert len(intervals) == 1
    begin, end = intervals[0]
    assert begin == pytest.approx(1.8, abs=0.4)
    assert end == pytest.approx(4.4, abs=0.4)


def test_ducking_measurably_lowers_the_music(project_root: Path, tmp_path: Path) -> None:
    """Render the same mix with and without ducking and compare the voice window."""

    project = load_project(project_root)
    timeline, evidence = video.build_timeline(project, project.deliverables[0])
    intervals = tuple(tuple(pair) for pair in evidence["plan_intervals"])
    ducked = AudioPlan(
        resource=timeline.music.resource,
        gain=1.0,
        ducking=intervals,
        duck_level=0.3,
        duck_fade=0.2,
    )
    flat = AudioPlan(resource=timeline.music.resource, gain=1.0)

    voice_window = (2.2, 1.6)
    levels: dict[str, float] = {}
    for label, music in (("ducked", ducked), ("flat", flat)):
        mix = Timeline(
            width=timeline.width,
            height=timeline.height,
            fps=timeline.fps,
            clips=timeline.clips,
            music=music,
            voice=None,
        )
        mlt_path = tmp_path / f"{label}.mlt"
        mlt_path.write_text(mix.to_xml(project.root), encoding="utf-8")
        rendered = media.render_timeline(mlt_path, tmp_path / f"{label}.mkv")
        levels[label] = media.mean_volume_window(rendered, *voice_window)

    assert levels["ducked"] < levels["flat"] - 3.0, levels


def test_render_refuses_an_asset_without_a_sidecar(project_root: Path) -> None:
    project = load_project(project_root)
    relative = project.assets["music"]
    (project_root / f"{relative}.license").unlink()

    with pytest.raises(video.VideoError, match=r"no .license sidecar"):
        video.build_timeline(project, project.deliverables[0])


def test_render_refuses_a_project_without_a_video_plan(project_root: Path) -> None:
    project = load_project(project_root)
    body = (project_root / "project.yaml").read_text(encoding="utf-8")
    head = body.split("video:\n")[0]
    (project_root / "project.yaml").write_text(head, encoding="utf-8")

    with pytest.raises(video.VideoError, match=r"no video\.shots"):
        video.build_timeline(load_project(project_root), project.deliverables[0])


def test_qc_report_flags_a_wrong_loudness(project_root: Path, tmp_path: Path) -> None:
    project = load_project(project_root)
    source = media.render_timeline(
        _quiet_timeline(project, tmp_path),
        tmp_path / "quiet.mkv",
    )

    report = video.qc_report(source, expected_duration=2.0, target_lufs=-14.0, preview=True)

    assert report["checks"]["loudness_within_half_lu"] is False
    assert report["ok"] is False


def _quiet_timeline(project: Project, tmp_path: Path) -> Path:
    """A deliberately quiet clip, to prove the QC gate can fail."""

    clip = project.root / project.assets["shot-1"]
    timeline = Timeline(
        width=320,
        height=180,
        fps=24.0,
        clips=(Clip(resource=clip, duration=2.0),),
        music=AudioPlan(resource=project.root / project.assets["music"], gain=0.01),
    )
    path = tmp_path / "quiet.mlt"
    path.write_text(timeline.to_xml(project.root), encoding="utf-8")
    return path
