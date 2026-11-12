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
from types import SimpleNamespace
from unittest.mock import MagicMock
from xml.etree import ElementTree

import pytest

from media_studio import media, mlt, video
from media_studio.fcproj import CompilerError
from media_studio.filmcraft import FilmCraftRunner
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
    assert "kdenlivetitle" in mlt
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

    Getting here took a bisect: the title composite was invisible because `qtblend` was told to
    blend the title track in at low opacity instead of compositing it over the picture below.
    Switching to `kdenlivetitle` (the producer Kdenlive's own title clips use) plus
    `compositing=0` draws the title over the moving tracks instead.
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
    assert "kdenlivetitle" in xml and "qtblend" in xml
    assert '<property name="compositing">0</property>' in xml

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
    # The black track the sequence starts with is not one of the tracks Kdenlive counts, which is
    # also how the project this envelope mirrors records it.
    count = str(len(sequence.findall("track")) - 1)
    assert sequence_properties["kdenlive:sequenceproperties.tracksCount"] == count
    assert sequence_properties["kdenlive:sequenceproperties.tracks"] == count
    assert sequence_properties["kdenlive:trackheight"] == mlt.KDENLIVE_TRACK_HEIGHT
    assert sequence_properties["kdenlive:sequenceproperties.zoneout"] != "0"

    project = root.find("tractor[@id='kdenlive_project']")
    assert project is not None
    assert properties(project)["kdenlive:projectTractor"] == "1"
    assert [track.get("producer") for track in project.findall("track")] == [sequence.get("id")]

    # The bin has to list the clips, or Kdenlive shows an empty project bin; it also has to carry
    # the retain marker and an entry for the sequence itself, or Kdenlive builds no sequence model,
    # aborts the load and reports a corrupted file.
    bin_properties = properties(bin_playlist)
    assert bin_properties["xml_retain"] == "1"
    assert bin_properties["kdenlive:docproperties.opensequences"] == sequence.get("id")
    assert bin_properties["kdenlive:sequenceFolder"] == mlt.KDENLIVE_SEQUENCE_FOLDER
    assert (
        bin_properties[f"kdenlive:folder.-1.{mlt.KDENLIVE_SEQUENCE_FOLDER}"]
        == mlt.KDENLIVE_SEQUENCE_FOLDER_NAME
    )
    bin_entries = bin_playlist.findall("entry")
    # The black track producer is a track, not a bin clip.
    clip_ids = {
        producer.get("id")
        for producer in root.findall("producer")
        if producer.get("id") != "producer0"
    }
    assert {entry.get("producer") for entry in bin_entries} == clip_ids | {sequence.get("id")}
    sequence_entry = next(
        entry for entry in bin_entries if entry.get("producer") == sequence.get("id")
    )
    assert sequence_entry.get("in") == mlt.KDENLIVE_SEQUENCE_ENTRY_OUT
    assert sequence_entry.get("out") == mlt.KDENLIVE_SEQUENCE_ENTRY_OUT
    # Properties first, then entries: the order a document Kdenlive writes itself uses.
    tags = [child.tag for child in bin_playlist]
    assert set(tags[: -len(bin_entries)]) == {"property"}
    # The sequence is a bin clip too, so its bin id is its own and never a media clip's.
    assert sequence_properties["kdenlive:id"] not in {
        properties(producer)["kdenlive:id"]
        for producer in root.findall("producer")
        if "kdenlive:id" in properties(producer)
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


def test_the_handoff_declares_square_sample_aspect(project_root: Path) -> None:
    """The rendered file must match the canvas instead of being pillarboxed, so both documents
    declare sample aspect 1:1; Kdenlive reads it back for its profile bookkeeping."""

    from media_studio.mlt import Clip, Timeline

    timeline = Timeline(
        width=1280,
        height=720,
        fps=30.0,
        clips=(Clip(resource=project_root / "assets" / "video" / "shot-1.mp4", duration=1.0),),
    )

    render_profile = ElementTree.fromstring(timeline.to_xml(project_root)).find("profile")
    handoff_profile = ElementTree.fromstring(timeline.to_kdenlive_xml(project_root)).find("profile")
    assert render_profile is not None and handoff_profile is not None
    assert render_profile.get("sample_aspect_num") == "1"
    assert render_profile.get("sample_aspect_den") == "1"
    assert handoff_profile.get("sample_aspect_num") == "1"
    assert handoff_profile.get("sample_aspect_den") == "1"


def _properties(element: ElementTree.Element) -> dict[str, str]:
    return {item.get("name", ""): item.text or "" for item in element.findall("property")}


def sequence_of(root: ElementTree.Element) -> ElementTree.Element:
    """The sequence tractor, identified by its `kdenlive:producer_type`."""
    return next(
        tractor
        for tractor in root.findall("tractor")
        if _properties(tractor).get("kdenlive:producer_type") == mlt.KDENLIVE_SEQUENCE_TYPE
    )


def test_the_handoff_has_no_kdenlive_uuid_on_media_producers(project_root: Path) -> None:
    """Kdenlive reserves `kdenlive:uuid` for sequences; a media producer carrying one is logged as
    an incorrect sequence, skipped by the bin loader, and every timeline instance of it is then
    removed as "without bin reference found". The handoff must therefore never write it on clips."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    for producer in root.findall("producer"):
        assert "kdenlive:uuid" not in _properties(producer), producer.get("id")

    # The sequence, not a media clip, is the one entry that carries a uuid.
    sequence = sequence_of(root)
    assert _properties(sequence)["kdenlive:uuid"] == _properties(sequence)["kdenlive:control_uuid"]


def test_the_handoff_pairs_control_uuid_with_bin_entry(project_root: Path) -> None:
    """Every timeline clip carries a `kdenlive:control_uuid` that the bin loader indexes, and the
    `main_bin` playlist lists every media producer so the rebuild can match each clip to its bin
    entry instead of dropping it as "without bin reference found"."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    bin_playlist = root.find("playlist[@id='main_bin']")
    assert bin_playlist is not None
    bin_producers = {entry.get("producer") for entry in bin_playlist.findall("entry")}

    media_producers = [p for p in root.findall("producer") if p.get("id") != "producer0"]
    for producer in media_producers:
        pid = producer.get("id")
        assert pid in bin_producers, f"{pid} missing from main_bin"
        assert producer.find("property[@name='kdenlive:control_uuid']") is not None, pid

    # The sequence is itself a bin clip with its own id, distinct from any media clip id.
    sequence = sequence_of(root)
    assert sequence.get("id") in bin_producers
    sequence_bin_entry = next(
        e for e in bin_playlist.findall("entry") if e.get("producer") == sequence.get("id")
    )
    assert sequence_bin_entry.get("in") == mlt.KDENLIVE_SEQUENCE_ENTRY_OUT


def test_the_handoff_uses_double_track_tractors(project_root: Path) -> None:
    """Each sequence track is a tractor over exactly two playlists: the real playlist and an empty
    `_b` companion reserved for same-track transition overlaps. The companion is declared before
    the tractor that references it, so MLT resolves the producer while parsing."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    sequence = sequence_of(root)

    header_ids = {el.get("id") for el in root if el.get("id")}
    declared_before: dict[str, int] = {}
    for position, child in enumerate(list(root)):
        if child.get("id"):
            declared_before[child.get("id")] = position

    # The black track is a plain producer, not a tractor. Every other sequence track is a
    # double-track tractor.
    for track in sequence.findall("track"):
        tractor_id = track.get("producer")
        if tractor_id == "producer0":
            continue
        assert tractor_id is not None and tractor_id.startswith("tractor_")
        tractor = root.find(f"tractor[@id='{tractor_id}']")
        assert tractor is not None, tractor_id

        tracks = tractor.findall("track")
        assert len(tracks) == 2, f"{tractor_id} has {len(tracks)} tracks, expected 2"
        producers = [t.get("producer") for t in tracks]
        main, companion = producers
        assert companion == f"{main}_b", f"{tractor_id}: companion {companion} != {main}_b"
        assert main in header_ids and companion in header_ids

        # Companion playlist is declared before the tractor that references it.
        assert declared_before[companion] < declared_before[tractor_id], (
            f"{companion} declared after {tractor_id}"
        )

    # The sequence references the tractors, not the bare playlists. The black track
    # (producer0) is the reserved black track, not a tractor.
    seq_producers = [
        t.get("producer") for t in sequence.findall("track") if t.get("producer") != "producer0"
    ]
    assert all(p.startswith("tractor_") for p in seq_producers)


def test_the_handoff_luma_transitions_run_over_the_lower_track(project_root: Path) -> None:
    """Kdenlive reads a composition as one track drawn over another and reports the opposite
    direction as "compositing with track N was set to forced track". A plain dissolve reads the
    same frames either way with the ramp inverted, so the pair is ordered background-over-foreground
    (a_track <= b_track) and the ramp is reversed only when the original had to be swapped."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    sequence = sequence_of(root)

    for tr in sequence.findall("transition"):
        if _properties(tr).get("mlt_service") != "luma":
            continue
        props = _properties(tr)
        a = int(props["a_track"])
        b = int(props["b_track"])
        assert a <= b, f"luma {tr.get('id')} composits track {a} over {b}"
        # `reverse` is only set when the pair had to be swapped; it is optional otherwise.
        if "reverse" in props:
            assert props["reverse"] in ("0", "1"), f"luma {tr.get('id')} bad reverse value"


def test_the_handoff_title_is_a_kdenlivetitle_scene(project_root: Path) -> None:
    """A title is a `kdenlivetitle` producer (the one Kdenlive's own title clips use) whose scene
    is an `xmldata` document, not a `qtext` producer whose `argument` renders nothing. It carries
    the Text producer type so double-clicking opens the title editor."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    titles = [
        p for p in root.findall("producer") if _properties(p).get("mlt_service") == "kdenlivetitle"
    ]
    assert titles, "expected at least one kdenlivetitle producer"
    for title in titles:
        props = _properties(title)
        assert props["kdenlive:producer_type"] == mlt.KDENLIVE_TITLE_TYPE, title.get("id")
        assert title.find("property[@name='xmldata']") is not None, title.get("id")
        scene = title.find("property[@name='xmldata']").text or ""
        assert "<kdenlivetitle" in scene and "QGraphicsTextItem" in scene
        assert "font=" in scene


def test_the_handoff_title_blend_composites_opaque(project_root: Path) -> None:
    """The title transition uses qtblend with `compositing=0` so the title is drawn over the tracks
    below instead of being blended into them at low opacity (which made it disappear)."""

    result = video.handoff(load_project(project_root))
    root = ElementTree.fromstring(result.path.read_text(encoding="utf-8"))

    sequence = sequence_of(root)

    blends = [
        tr
        for tr in sequence.findall("transition")
        if _properties(tr).get("mlt_service") == "qtblend"
    ]
    assert blends, "expected a qtblend title transition"
    for tr in blends:
        props = _properties(tr)
        assert props["compositing"] == "0", tr.get("id")
        assert props["distort"] == "0", tr.get("id")
        assert props["rotate_center"] == "0", tr.get("id")
        assert props["always_active"] == "1", tr.get("id")


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
    assert "Noto Sans" in result.notes[0]
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
        # The black track is an MLT generator rather than a file, which is what a project Kdenlive
        # writes uses for it as well.
        if not path.is_absolute():
            assert resource == "black", resource
            continue
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


def _two_scene_clip(tmp_path: Path) -> Path:
    """One second of black hard-cut to one second of red at 24 fps."""

    return generate(
        tmp_path / "scenes.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "color=black:s=320x240:d=1:r=24",
            "-f",
            "lavfi",
            "-i",
            "color=red:s=320x240:d=1:r=24",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1",
        ],
    )


def test_detect_scene_cuts_finds_the_hard_cut(tmp_path: Path) -> None:
    clip = _two_scene_clip(tmp_path)

    cuts = video.detect_scene_cuts(clip)

    assert len(cuts) == 1
    assert cuts[0].frame == 24
    assert cuts[0].time == pytest.approx(1.0, abs=0.1)


def test_detect_scene_cuts_reports_one_scene_without_cuts(tmp_path: Path) -> None:
    clip = generate(tmp_path / "flat.mp4", ["-f", "lavfi", "-i", "color=gray:s=320x240:d=1:r=24"])

    assert video.detect_scene_cuts(clip) == []


def test_scene_ranges_split_around_the_cuts(tmp_path: Path) -> None:
    clip = _two_scene_clip(tmp_path)
    cuts = video.detect_scene_cuts(clip)

    ranges = video.scene_ranges(cuts, total_frames=48)

    first = (0, 23, 0.0, pytest.approx(1.0, abs=0.1))
    second = (24, 47, pytest.approx(1.0, abs=0.1), -1.0)
    assert ranges == [first, second]


def test_scene_ranges_without_cuts_cover_everything() -> None:
    ranges = video.scene_ranges([], total_frames=48)

    assert ranges == [(0, 47, 0.0, -1.0)]


def test_detect_scene_cuts_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(video.VideoError, match="no such file"):
        video.detect_scene_cuts(tmp_path / "ghost.mp4")


def _clamp_project(tmp_path: Path, music_duration: float | None) -> Project:
    """A 15 s edit over a 45 s music bed entered 30 s in."""

    sources = tmp_path / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    generate(
        sources / "long-shot.mp4",
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24:duration=15",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
        ],
    )
    generate(
        sources / "music45.wav",
        [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=45:sample_rate=48000",
            "-c:a",
            "pcm_s16le",
        ],
    )
    root = scaffold_project("clamp-demo", root=tmp_path).path
    project = load_project(root)
    shot = stage_asset(
        project,
        class_name="video",
        license_text="CC0",
        local_file=sources / "long-shot.mp4",
        provider="fixture",
        retrieved_at="2026-10-24T00:00:00+00:00",
    )
    music = stage_asset(
        project,
        class_name="music",
        license_text="CC0",
        local_file=sources / "music45.wav",
        provider="fixture",
        retrieved_at="2026-10-24T00:00:00+00:00",
    )
    duration_line = f", duration: {music_duration}" if music_duration is not None else ""
    manifest = f"""slug: clamp-demo
title: Clamp Demo
deliverables:
  - id: master
    kind: video
    resolution: 320x180
    fps: 24
    duration: 15.0
    preview: true
assets:
  shot: {shot.relative}
  music: {music.relative}
video:
  shots:
    - {{ asset: shot, duration: 15.0 }}
  music: {{ asset: music, in: 30.0, gain: 0.6{duration_line} }}
"""
    (root / "project.yaml").write_text(manifest, encoding="utf-8")
    return load_project(root)


def _music_entries(
    timeline: Timeline,
) -> tuple[list[ElementTree.Element], list[ElementTree.Element]]:
    root = ElementTree.fromstring(timeline.to_xml())
    playlist = next(
        element for element in root.findall("playlist") if element.get("id") == "music_playlist"
    )
    return playlist.findall("entry"), playlist.findall("blank")


def test_audio_cue_clamps_long_music_to_video_duration_with_offset(tmp_path: Path) -> None:
    """in_point=30 on a 45 s bed must not spill a 15 s edit past its last frame."""

    project = _clamp_project(tmp_path, music_duration=20.0)
    timeline, _evidence = video.build_timeline(project, project.deliverables[0])

    assert timeline.duration == pytest.approx(15.0, abs=0.01)
    assert timeline.music is not None
    assert timeline.music.duration == pytest.approx(15.0)
    assert timeline.music.in_point == pytest.approx(30.0)

    entries, blanks = _music_entries(timeline)
    assert int(entries[0].get("in", "0")) == mlt.frames(30.0, 24)
    span = int(entries[-1].get("out", "0")) - int(entries[0].get("in", "0")) + 1
    # Requested 20 s exceed what the source has left (15 s), which is exactly the edit
    # length: the bed must not be cut short of the video.
    assert span == mlt.frames(15.0, 24)
    assert blanks == []  # a full-length bed needs no padding

    # A shorter requested length is honoured, and the track is padded to the length of
    # the edit so the playlist never ends before the picture does.
    shorter = _clamp_project(tmp_path / "shorter", music_duration=10.0)
    short_timeline, _ = video.build_timeline(shorter, shorter.deliverables[0])
    assert short_timeline.music is not None
    assert short_timeline.music.duration == pytest.approx(10.0)
    short_entries, short_blanks = _music_entries(short_timeline)
    short_span = int(short_entries[-1].get("out", "0")) - int(short_entries[0].get("in", "0")) + 1
    assert short_span == mlt.frames(10.0, 24)
    assert [int(blank.get("length", "0")) for blank in short_blanks] == [
        mlt.frames(15.0, 24) - mlt.frames(10.0, 24)
    ]


def test_video_render_force_overwrites_existing_fcproj(
    project_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = load_project(project_root)
    target = project.root / "out" / "master.fcproj"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("manual GUI edits live here", encoding="utf-8")
    runner = MagicMock(spec=FilmCraftRunner)

    with pytest.raises(CompilerError, match="already exists"):
        video.render_filmcraft(project, runner=runner, generate_review=False)
    assert target.read_text(encoding="utf-8") == "manual GUI edits live here"

    forced: list[bool] = []

    def fake_compile(
        project: Project,
        output_fcproj: Path | None = None,
        deliverable_id: str | None = None,
        runner: FilmCraftRunner | None = None,
        force: bool = False,
    ) -> SimpleNamespace:
        forced.append(force)
        assert output_fcproj is not None
        output_fcproj.write_text("rebuilt by the compiler", encoding="utf-8")
        return SimpleNamespace(output_path=output_fcproj)

    monkeypatch.setattr(video, "compile_manifest_to_fcproj", fake_compile)
    monkeypatch.setattr(
        video,
        "qc_report",
        lambda *args, **kwargs: {"checks": {}, "ok": True, "problems": []},
    )
    runner.export.return_value = SimpleNamespace(
        render_path="cpu", gpu_fallback_to_cpu=False, details={}, returncode=0
    )

    result = video.render_filmcraft(project, runner=runner, force=True, generate_review=False)

    assert forced == [True]
    assert target.read_text(encoding="utf-8") == "rebuilt by the compiler"
    assert result.fcproj_path == target
