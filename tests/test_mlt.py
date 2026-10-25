"""Tests for MLT authoring: the XML shape and the audio level curves."""

from __future__ import annotations

import itertools
from pathlib import Path
from xml.etree import ElementTree

import pytest

from media_studio.mlt import (
    AudioPlan,
    Clip,
    Timeline,
    Title,
    ducking_from_speech,
    frames,
    level_segments,
    place_clips,
    timeline_seconds,
)


def timeline(**overrides: object) -> Timeline:
    base: dict[str, object] = {
        "width": 320,
        "height": 180,
        "fps": 24.0,
        "clips": (
            Clip(resource=Path("/tmp/a.mp4"), duration=2.0),
            Clip(resource=Path("/tmp/b.mp4"), duration=2.0, in_point=1.0),
        ),
        "title": "unit test",
    }
    base.update(overrides)
    return Timeline(**base)  # type: ignore[arg-type]


def test_frames_rounds_and_never_goes_negative() -> None:
    assert frames(1.0, 24) == 24
    assert frames(0.5, 60) == 30
    assert frames(-3.0, 24) == 0


def test_xml_has_one_producer_and_entry_per_clip() -> None:
    root = ElementTree.fromstring(timeline().to_xml())

    producers = [element for element in root.findall("producer")]
    entries = root.find("playlist").findall("entry")

    assert [element.get("id") for element in producers] == ["clip0", "clip1"]
    assert len(entries) == 2
    assert entries[0].get("producer") == "clip0"
    assert entries[1].get("in") == "24"  # 1.0s at 24fps
    assert producers[1].find("property").text == "/tmp/b.mp4"
    profile = root.find("profile")
    assert profile.get("width") == "320"
    assert root.find("tractor").get("out") == "95"  # 4.0s at 24fps, inclusive


def test_timeline_refuses_to_be_empty() -> None:
    with pytest.raises(ValueError, match=r"at least one clip"):
        timeline(clips=()).to_xml()


def test_duration_is_the_sum_of_the_shots() -> None:
    assert timeline().duration == 4.0


def test_a_clip_can_override_the_cross_fade_length() -> None:
    """A fade is a cross-fade against a black clip, so it needs its own length."""

    clips = (
        Clip(resource=Path("/tmp/black.mp4"), duration=0.4),
        Clip(resource=Path("/tmp/a.mp4"), duration=2.0, transition="crossfade", crossfade=0.4),
        Clip(resource=Path("/tmp/b.mp4"), duration=2.0),
        Clip(resource=Path("/tmp/black.mp4"), duration=0.6, transition="crossfade", crossfade=0.6),
    )

    placements = place_clips(clips, 0.5)

    assert [item.start for item in placements] == [0.0, 0.0, 2.0, 3.4]
    assert [item.crossfade for item in placements] == [0.0, 0.4, 0.0, 0.6]
    assert timeline_seconds(clips, 0.5) == 4.0  # the fades replace their own overlap


def to_db(amplitude: float) -> float:
    import math

    return 20 * math.log10(amplitude)


def tile(segments: list) -> None:
    """Segments must cover the timeline exactly, with no gaps or overlaps."""

    assert segments[0].start_frame == 0
    assert all(left.end_frame == right.start_frame for left, right in itertools.pairwise(segments))


def test_level_segments_duck_through_a_stepped_ramp() -> None:
    plan = AudioPlan(
        resource=Path("/tmp/music.wav"),
        gain=1.0,
        ducking=((2.0, 4.0),),
        duck_level=0.3,
        duck_fade=0.25,
    )

    segments = level_segments(plan, duration=6.0, fps=24.0)

    tile(segments)
    assert segments[-1].end_frame == frames(6.0, 24)

    ducked = to_db(0.3)
    held = [segment for segment in segments if abs(segment.level_db - ducked) < 0.05]
    assert sum(segment.length for segment in held) >= frames(1.6, 24)
    assert any(abs(segment.level_db) < 0.05 for segment in segments)  # unity outside the duck

    ramp = [
        segment.level_db
        for segment in segments
        if segment.start_frame >= frames(1.75, 24) and segment.end_frame <= frames(2.0, 24)
    ]
    assert len(set(ramp)) >= 3
    assert ramp[0] > ramp[-1]  # on the way down into the duck


def test_gain_and_fades_become_segments() -> None:
    plan = AudioPlan(resource=Path("/tmp/music.wav"), gain=0.5, fade_in=1.0, fade_out=1.0)

    segments = level_segments(plan, duration=4.0, fps=24.0)

    tile(segments)
    assert segments[0].level_db < -60.0  # fade starts from silence
    assert any(abs(segment.level_db - to_db(0.5)) < 0.05 for segment in segments)
    assert segments[-1].level_db < -60.0  # and returns to it


def test_level_segments_is_empty_for_a_zero_length_track() -> None:
    plan = AudioPlan(resource=Path("/tmp/m.wav"))

    assert level_segments(plan, duration=0.0, fps=24.0) == []


def test_audio_tracks_use_static_entry_filters() -> None:
    music = AudioPlan(resource=Path("/tmp/music.wav"), ducking=((2.0, 4.0),))
    xml = timeline(music=music, voice=AudioPlan(resource=Path("/tmp/vo.wav"))).to_xml()

    root = ElementTree.fromstring(xml)
    playlists = {element.get("id"): element for element in root.findall("playlist")}
    assert set(playlists) == {"video0", "music_playlist", "voice_playlist"}

    entries = playlists["music_playlist"].findall("entry")
    assert len(entries) > 1  # a duck is a chain of spans, not one animated property

    filtered = [entry for entry in entries if entry.find("filter") is not None]
    assert filtered
    services = [
        element.text
        for element in filtered[0].find("filter").findall("property")
        if element.get("name") == "mlt_service"
    ]
    levels = [
        element
        for element in filtered[0].find("filter").findall("property")
        if element.get("name") == "level"
    ]
    assert services == ["volume"]
    assert levels and len(levels[0]) == 0  # static: MLT never animates a nested XML property
    assert float(levels[0].text) <= -1.0

    voice_entries = playlists["voice_playlist"].findall("entry")
    assert voice_entries
    assert voice_entries[0].find("filter") is None or all(
        float(element.text or 0) == 0.0
        for element in voice_entries[0].find("filter").findall("property")
        if element.get("name") == "level"
    )


def test_cross_fades_pull_the_shot_earlier_and_use_the_other_track() -> None:
    clips = (
        Clip(resource=Path("/tmp/a.mp4"), duration=2.0),
        Clip(resource=Path("/tmp/b.mp4"), duration=2.0),
        Clip(resource=Path("/tmp/c.mp4"), duration=2.0, transition="crossfade"),
    )

    placements = place_clips(clips, 0.5)

    assert [item.track for item in placements] == [0, 0, 1]
    assert [item.start for item in placements] == [0.0, 2.0, 3.5]
    assert placements[2].crossfade == 0.5
    assert timeline_seconds(clips, 0.5) == 5.5  # one overlap shortens the edit


def test_a_cross_fade_shorter_than_its_shots_is_clamped() -> None:
    clips = (
        Clip(resource=Path("/tmp/a.mp4"), duration=1.0),
        Clip(resource=Path("/tmp/b.mp4"), duration=0.2, transition="crossfade"),
    )

    placements = place_clips(clips, 0.5)

    assert placements[1].crossfade == 0.2  # never longer than the shortest shot involved
    assert timeline_seconds(clips, 0.5) == 1.0


def test_xml_emits_two_tracks_and_a_dissolve_for_a_cross_fade() -> None:
    xml = timeline(
        clips=(
            Clip(resource=Path("/tmp/a.mp4"), duration=2.0),
            Clip(resource=Path("/tmp/b.mp4"), duration=2.0, transition="crossfade"),
        ),
        transition_duration=0.5,
    ).to_xml()

    root = ElementTree.fromstring(xml)
    playlists = [element.get("id") for element in root.findall("playlist")]
    assert playlists == ["video0", "video1"]

    ab = next(element for element in root.findall("tractor") if element.get("id") == "video_ab")
    assert [track.get("producer") for track in ab.findall("track")] == ["video0", "video1"]
    transition = ab.find("transition")
    properties = {element.get("name"): element.text for element in transition.findall("property")}
    assert properties["mlt_service"] == "luma"
    assert properties["a_track"] == "0"
    assert properties["b_track"] == "1"
    # The window belongs on the element: `start`/`end` properties are mix levels, not a range.
    assert transition.get("in") == "36"  # 1.5s at 24fps
    assert transition.get("out") == "47"
    # The outgoing shot is padded so it keeps playing underneath the incoming one.
    assert playlists and root.find("playlist/blank") is not None


def test_titles_get_their_own_track_and_a_blend() -> None:
    xml = timeline(
        clips=(Clip(resource=Path("/tmp/a.mp4"), duration=4.0),),
        titles=(Title(text="Opening", start=1.0, duration=1.0, size=48),),
    ).to_xml()

    root = ElementTree.fromstring(xml)
    playlists = {element.get("id"): element for element in root.findall("playlist")}
    assert "titles" in playlists

    blanks = playlists["titles"].findall("blank")
    assert [blank.get("length") for blank in blanks] == ["24", "48"]  # in at 1s, out at 2s

    title_producer = next(
        element for element in root.findall("producer") if element.get("id") == "title0"
    )
    properties = {
        element.get("name"): element.text for element in title_producer.findall("property")
    }
    assert properties["mlt_service"] == "qtext"
    assert properties["argument"] == "Opening"
    assert properties["size"] == "48"
    assert properties["bgcolour"] == "#00000000"  # transparent, so the shot shows through

    video_track = next(
        element for element in root.findall("tractor") if element.get("id") == "video_track"
    )
    blend = {
        element.get("name"): element.text
        for element in video_track.find("transition").findall("property")
    }
    assert blend["mlt_service"] == "qtblend"
    assert blend["always_active"] == "1"


def test_ducking_from_speech_adds_lead_and_tail_and_merges_short_gaps() -> None:
    intervals = ducking_from_speech(
        [(2.0, 3.0), (3.2, 4.0), (8.0, 9.0)],
        duration=10.0,
        lead=0.2,
        tail=0.4,
        minimum_gap=0.5,
    )

    assert intervals[0] == (1.8, 4.4)  # the 0.2s gap between speech is too short to unduck
    assert intervals[1] == (7.8, 9.4)


def test_ducking_from_speech_clamps_to_the_timeline() -> None:
    intervals = ducking_from_speech([(0.05, 29.9)], duration=30.0, lead=0.5, tail=0.5)

    assert intervals == ((0.0, 30.0),)
