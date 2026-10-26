"""End-to-end tests for the video path: MLT, melt, loudness, ducking, and QC.

These run the real toolchain on small generated media, so they prove the pipeline rather than
mocking it. Resolution is deliberately tiny to keep the suite fast.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

import pytest

from media_studio import media, mlt, video
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
        ("shot-3.mp4", "testsrc2=size=320x180:rate=24:duration=2"),
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
  fade_in: 0.4
  fade_out: 0.6
  crossfade: 0.3
  titles:
    - { text: "Demo title", start: 1.2, duration: 1.5, size: 32, position: "+10+10" }
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
    assert report["ok"] is True, json.dumps(report["checks"])
    assert report["black_segments"] == [], report["black_segments"]
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


def test_a_declared_title_reaches_the_render(project_root: Path) -> None:
    project = load_project(project_root)

    result = video.render(project, preview=True)

    assert result.report["timeline"]["titles"] == [
        {"text": "Demo title", "start": 1.2, "duration": 1.5}
    ]
    mlt = result.mlt_path.read_text(encoding="utf-8")
    assert "qtext" in mlt
    assert "qtblend" in mlt
    assert "Demo title" in mlt


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


def test_titles_render_over_the_picture(tmp_path: Path) -> None:
    """The text must actually appear, so this measures a region where the title sits.

    Getting here took a bisect: declaring `sample_aspect_num="1"` on the profile made qtblend
    drop the title composite entirely in this MLT build.
    """

    from media_studio.mlt import Title

    black = generate(
        tmp_path / "black.mp4",
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
    timeline = Timeline(
        width=320,
        height=180,
        fps=24.0,
        clips=(Clip(resource=black, duration=2.0),),
        titles=(Title(text="DEMO", start=0.5, duration=1.0, size=60),),
    )
    xml = timeline.to_xml(tmp_path)
    assert "qtext" in xml and "qtblend" in xml
    assert '<property name="compositing">1</property>' in xml

    rendered = media.render_timeline(_write(tmp_path / "titles.mlt", xml), tmp_path / "titles.mkv")

    # Peak brightness, not a mean: white text on black is a few hundred bright pixels.
    window = "240:90:0:0"
    plain = media.brightest_luminance_at(rendered, 0.2, crop=window)
    titled = media.brightest_luminance_at(rendered, 1.0, crop=window)
    assert plain <= 20.0, plain  # nothing bright without the title
    assert titled >= 150.0, titled  # antialiased white text, far above the black background


def _write(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


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


# Kdenlive handoff --------------------------------------------------------------------------


def test_the_handoff_carries_the_envelope_kdenlive_looks_for(project_root: Path) -> None:
    """Kdenlive refuses a document whose version it cannot read, so the envelope is the contract."""

    result = video.handoff(load_project(project_root))

    assert result.path.is_file()
    assert result.path.suffix == ".kdenlive"
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))
    assert root.get("producer") == "main_bin"

    def properties(element: ElementTree.Element) -> dict[str, str]:
        return {item.get("name", ""): item.text or "" for item in element.findall("property")}

    bin_playlist = root.find("playlist[@id='main_bin']")
    assert bin_playlist is not None
    assert (
        properties(bin_playlist)["kdenlive:docproperties.version"] == mlt.KDENLIVE_DOCUMENT_VERSION
    )

    # The sequence is the tractor Kdenlive types as a sequence, not a name of our choosing.
    sequence = next(
        tractor
        for tractor in root.findall("tractor")
        if properties(tractor).get("kdenlive:producer_type") == mlt.KDENLIVE_SEQUENCE_TYPE
    )
    sequence_properties = properties(sequence)
    count = str(len(sequence.findall("track")))
    assert sequence_properties["kdenlive:sequenceproperties.tracksCount"] == count
    assert sequence_properties["kdenlive:sequenceproperties.tracks"] == count
    assert sequence_properties["kdenlive:trackheight"] == mlt.KDENLIVE_TRACK_HEIGHT
    assert sequence_properties["kdenlive:sequenceproperties.zoneout"] != "0"

    project = root.find("tractor[@id='kdenlive_project']")
    assert project is not None
    assert properties(project)["kdenlive:projectTractor"] == "1"
    assert [track.get("producer") for track in project.findall("track")] == [sequence.get("id")]

    # The bin has to list the clips, or Kdenlive shows an empty project bin.
    bin_entries = root.find("playlist[@id='main_bin']").findall("entry")
    assert {entry.get("producer") for entry in bin_entries} == {
        producer.get("id") for producer in root.findall("producer")
    }
    assert result.problems == [], result.problems


def test_the_profile_name_maps_only_to_presets_mlt_has() -> None:
    """The name is what Kdenlive reads its frame rate from, so it is never invented."""

    assert mlt.kdenlive_profile_name(1280, 720, 30.0) == "atsc_720p_30"
    assert mlt.kdenlive_profile_name(1920, 1080, 25.0) == "atsc_1080p_25"
    assert mlt.kdenlive_profile_name(320, 180, 24.0) == ""
    assert mlt.kdenlive_profile_name(1080, 1920, 30.0) == ""


def test_the_handoff_carries_the_document_identity(project_root: Path) -> None:
    """The fixture renders tiny, so it has no preset name; identity is always written."""

    result = video.handoff(load_project(project_root))

    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))
    bin_playlist = root.find("playlist[@id='main_bin']")
    document = {item.get("name", ""): item.text or "" for item in bin_playlist.findall("property")}

    assert "kdenlive:docproperties.profile" not in document
    assert document["kdenlive:docproperties.uuid"].startswith("{")
    assert (
        document["kdenlive:docproperties.activetimeline"] == document["kdenlive:docproperties.uuid"]
    )

    profile = root.find("profile")
    assert profile is not None
    # The render input omits sample aspect; the handoff declares it, because Kdenlive reads it.
    assert profile.get("sample_aspect_num") == "1"
    assert profile.get("sample_aspect_den") == "1"


def test_the_handoff_leaves_the_render_input_without_sample_aspect(project_root: Path) -> None:
    """The 1:1 sample aspect is what makes this MLT build drop qtblend titles, so it stays local."""

    from media_studio.mlt import Clip, Timeline

    timeline = Timeline(
        width=1280,
        height=720,
        fps=30.0,
        clips=(Clip(resource=project_root / "assets" / "video" / "shot-1.mp4", duration=1.0),),
    )

    assert "sample_aspect" not in timeline.to_xml(project_root)
    assert "sample_aspect_num" in timeline.to_kdenlive_xml(project_root)


def test_the_handoff_targets_the_installed_kdenlive(project_root: Path) -> None:
    result = video.handoff(load_project(project_root))

    assert result.kdenlive_version == video.installed_kdenlive_version()
    assert "kdenlive:docproperties.kdenliveversion" in result.path.read_text(encoding="utf-8")


def test_the_handoff_is_reproducible(project_root: Path) -> None:
    """The document carries no timestamp, so the same project yields the same bytes."""

    first = video.handoff(load_project(project_root)).path.read_text(encoding="utf-8")
    second = video.handoff(load_project(project_root)).path.read_text(encoding="utf-8")

    assert first == second


def test_the_handoff_names_a_font_kdenlive_would_substitute(project_root: Path) -> None:
    """The fixture asks for `Sans`, which fontconfig resolves to another family on this machine."""

    result = video.handoff(load_project(project_root))

    assert result.notes, "expected the title's family to be reported"
    assert "will be used instead" in result.notes[0]
    # A cosmetic substitution is named but does not fail the handoff.
    assert result.problems == []


def test_the_handoff_references_only_files_inside_the_project(project_root: Path) -> None:
    """Scratch under /tmp/opencode is purged, so the generated filler has to live in the project."""

    project = load_project(project_root)

    result = video.handoff(project)

    assert result.missing_references == ()
    resources = re.findall(
        r'<property name="resource">([^<]+)</property>', result.path.read_text(encoding="utf-8")
    )
    assert resources
    for resource in resources:
        path = Path(resource)
        assert path.is_file(), resource
        assert path.is_relative_to(project.root), resource
    # The fades are cross-fades against a generated black clip, which has to be project-owned.
    filler = [Path(item) for item in resources if "black-" in item]
    assert filler, resources
    for path in filler:
        assert path.is_relative_to(project.root / "out" / "frames"), path


def test_the_handoff_reports_a_reference_that_is_not_on_disk(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """build_timeline validates the shot sources, so this guards the document's own references."""

    project = load_project(project_root)
    real = video.build_timeline

    def without_a_source(*args: object, **kwargs: object) -> tuple[Timeline, dict]:
        timeline, evidence = real(*args, **kwargs)
        return (
            replace(
                timeline, clips=(replace(timeline.clips[0], resource=project.root / "gone.mp4"),)
            ),
            evidence,
        )

    monkeypatch.setattr(video, "build_timeline", without_a_source)

    result = video.handoff(project)

    assert result.missing_references == (project.root / "gone.mp4",)
    assert any("not there" in problem for problem in result.problems)


def test_the_handoff_document_still_renders(project_root: Path) -> None:
    """The envelope must not change the edit, so `melt` has to render it like the render input."""

    project = load_project(project_root)
    result = video.handoff(project)
    render_input = project.root / "out" / "master.mlt"
    render_input.write_text(result.timeline.to_xml(project.root), encoding="utf-8")

    from_handoff = media.render_timeline(
        result.path, project.root / "out" / "frames" / "handoff.mkv"
    )
    from_input = media.render_timeline(render_input, project.root / "out" / "frames" / "input.mkv")

    assert media.probe(from_handoff).duration == pytest.approx(
        media.probe(from_input).duration, abs=0.1
    )
    # The envelope must not reshape the audio either, so the two renders are compared by level.
    assert media.mean_volume_window(from_handoff, 2.0, 2.0) == pytest.approx(
        media.mean_volume_window(from_input, 2.0, 2.0), abs=0.5
    )


def test_the_cli_writes_and_reports_the_handoff(
    project_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from media_studio.cli import main

    exit_code = main(["video", "handoff", str(project_root)])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    assert "handoff out/kdenlive/master.kdenlive" in captured.out
    assert "verdict: pass" in captured.out
    assert (project_root / "out" / "kdenlive" / "master.kdenlive").is_file()
    # The render input is the render path's business and must not be created here.
    assert not (project_root / "out" / "master.mlt").exists()
