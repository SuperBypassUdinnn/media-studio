"""Authoring plain MLT XML for `melt`.

Everything here is a pure function of a plan, so the XML and the audio curves can be checked
without running a renderer. Times cross into MLT as frame numbers, which keeps a 60 fps timeline
exact; floating-point timecodes would drift by a frame at the edges.

Audio shaping (gain, fades, ducking) becomes one animated `level` curve per track, attached at
playlist level so keyframes are relative to the timeline rather than to a clip.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

MLT_VERSION = "7.42.0"
DEFAULT_TRANSITION = 0.5
# A ducked bed sits well under the voice without disappearing.
DEFAULT_DUCK_LEVEL = 0.3


def frames(seconds: float, fps: float) -> int:
    """Seconds to whole frames, never negative."""

    return max(0, round(seconds * fps))


@dataclass(frozen=True)
class Clip:
    """One shot. `transition` describes how it enters, relative to the shot before it."""

    resource: Path
    duration: float
    in_point: float = 0.0
    transition: str = "cut"
    crossfade: float | None = None  # overrides the timeline default for this clip

    def out_point(self) -> float:
        return self.in_point + self.duration


@dataclass(frozen=True)
class ClipPlacement:
    """Where one shot lands when cross-fades overlap the shots around it."""

    index: int
    track: int
    start: float
    duration: float
    in_point: float
    crossfade: float


def place_clips(clips: tuple[Clip, ...], crossfade: float) -> list[ClipPlacement]:
    """Lay shots on two alternating tracks, pulling each cross-faded shot earlier.

    A cross-fade overlaps two shots, so the timeline shortens by that overlap; the outgoing shot
    keeps playing on its own track under the incoming one, which is what a `mix` transition needs.
    """

    placements: list[ClipPlacement] = []
    for index, clip in enumerate(clips):
        if not placements:
            placements.append(ClipPlacement(index, 0, 0.0, clip.duration, clip.in_point, 0.0))
            continue
        previous = placements[-1]
        wanted = clip.crossfade if clip.crossfade is not None else crossfade
        overlap = (
            min(wanted, previous.duration, clip.duration)
            if clip.transition == "crossfade" or clip.crossfade
            else 0.0
        )
        # Only a cross-fade needs the other track; a cut continues on the current one, which keeps
        # an all-cuts edit a single playlist a person can still read in Kdenlive.
        placements.append(
            ClipPlacement(
                index=index,
                track=1 - previous.track if overlap else previous.track,
                start=previous.start + previous.duration - overlap,
                duration=clip.duration,
                in_point=clip.in_point,
                crossfade=overlap,
            )
        )
    return placements


def timeline_seconds(clips: tuple[Clip, ...], crossfade: float) -> float:
    """Length of the edit once cross-fades have pulled the shots together."""

    placements = place_clips(clips, crossfade)
    if not placements:
        return 0.0
    last = placements[-1]
    return round(last.start + last.duration, 6)


@dataclass(frozen=True)
class AudioPlan:
    """One audio bed: music or voice, with its level curve inputs."""

    resource: Path
    in_point: float = 0.0
    gain: float = 1.0
    fade_in: float = 0.0
    fade_out: float = 0.0
    ducking: tuple[tuple[float, float], ...] = ()
    duck_level: float = DEFAULT_DUCK_LEVEL
    duck_fade: float = 0.25


@dataclass(frozen=True)
class Title:
    """Text overlaid on the picture, composited with `qtblend`."""

    text: str
    start: float
    duration: float
    size: int = 64
    colour: str = "#ffffffff"
    outline: str = "#ff000000"
    family: str = "Sans"
    geometry: str = "+0+0"


@dataclass(frozen=True)
class Timeline:
    """A complete edit: shots, an optional music bed, and an optional voice track."""

    width: int
    height: int
    fps: float
    clips: tuple[Clip, ...]
    music: AudioPlan | None = None
    voice: AudioPlan | None = None
    title: str = "media-studio timeline"
    titles: tuple[Title, ...] = field(default=())
    transition_duration: float = DEFAULT_TRANSITION
    extra_audio: tuple[AudioPlan, ...] = field(default=())

    @property
    def duration(self) -> float:
        return timeline_seconds(self.clips, self.transition_duration)

    def to_xml(self, root: Path | None = None) -> str:
        """Serialise the timeline as MLT XML."""

        if not self.clips:
            raise ValueError("a timeline needs at least one clip")
        fps = self.fps
        total = frames(self.duration, fps)
        header = ElementTree.Element(
            "mlt",
            {
                "LC_NUMERIC": "C",
                "version": MLT_VERSION,
                "title": self.title,
                "root": str(root or Path.cwd()),
                "producer": "main",
            },
        )
        gcd = math.gcd(self.width, self.height) or 1
        header.append(
            ElementTree.Element(
                "profile",
                {
                    "description": f"{self.width}x{self.height} {fps:g}fps",
                    "width": str(self.width),
                    "height": str(self.height),
                    "progressive": "1",
                    # The display aspect comes from the canvas, so a square or vertical
                    # deliverable is described correctly. Sample aspect is deliberately not
                    # declared: with it set to 1:1 this MLT build drops qtblend title composites.
                    "display_aspect_num": str(self.width // gcd),
                    "display_aspect_den": str(self.height // gcd),
                    "frame_rate_num": f"{fps:g}",
                    "frame_rate_den": "1",
                    "colorspace": "709",
                },
            )
        )

        # Producers come before the playlists that reference them: `melt` resolves an entry's
        # producer while parsing the playlist, so a producer defined later is invisible and the
        # entry renders nothing.
        for index, clip in enumerate(self.clips):
            producer = ElementTree.SubElement(
                header,
                "producer",
                {
                    "id": f"clip{index}",
                    "in": str(frames(clip.in_point, fps)),
                    "out": str(max(frames(clip.out_point(), fps) - 1, 0)),
                },
            )
            _property(producer, "resource", str(clip.resource))

        if self.titles:
            self._title_producers(header)
        audio_tracks = self.audio_tracks()
        for name, plan in audio_tracks:
            producer = ElementTree.SubElement(
                header,
                "producer",
                {"id": f"{name}_producer", "in": "0", "out": str(max(total - 1, 0))},
            )
            _property(producer, "resource", str(plan.resource))

        placements = place_clips(self.clips, self.transition_duration)
        # A cross-fade needs two tracks: the outgoing shot keeps playing under the incoming one
        # while a `mix` transition dissolves between them.
        track_count = max(item.track for item in placements) + 1
        video_tracks = [
            ElementTree.SubElement(header, "playlist", {"id": f"video{track}"})
            for track in range(track_count)
        ]
        cursors = [0] * track_count
        for placement in placements:
            playlist = video_tracks[placement.track]
            start_frame = frames(placement.start, fps)
            gap = start_frame - cursors[placement.track]
            if gap > 0:
                ElementTree.SubElement(playlist, "blank", {"length": str(gap)})
            ElementTree.SubElement(
                playlist,
                "entry",
                {
                    "producer": f"clip{placement.index}",
                    "in": str(frames(placement.in_point, fps)),
                    "out": str(max(frames(placement.in_point + placement.duration, fps) - 1, 0)),
                },
            )
            cursors[placement.track] = start_frame + frames(placement.duration, fps)
        for track, playlist in enumerate(video_tracks):
            tail = total - cursors[track]
            if tail > 0:
                ElementTree.SubElement(playlist, "blank", {"length": str(tail)})

        if track_count == 1:
            video_element = video_tracks[0]
        else:
            video_element = ElementTree.SubElement(
                header, "tractor", {"id": "video_ab", "in": "0", "out": str(max(total - 1, 0))}
            )
            for playlist in video_tracks:
                ElementTree.SubElement(video_element, "track", {"producer": playlist.get("id", "")})
            for placement in placements:
                if not placement.crossfade:
                    continue
                start = frames(placement.start, fps)
                stop = max(frames(placement.start + placement.crossfade, fps) - 1, start)
                _dissolve(
                    video_element,
                    f"cross{placement.index}",
                    start,
                    stop,
                    a_track=1 - placement.track,
                    b_track=placement.track,
                )

        if self.titles:
            # A title needs its own track and an alpha composite over the shots below it.
            title_playlist = self._title_playlist(header, total)
            video_track = ElementTree.SubElement(
                header,
                "tractor",
                {"id": "video_track", "in": "0", "out": str(max(total - 1, 0))},
            )
            ElementTree.SubElement(video_track, "track", {"producer": video_element.get("id", "")})
            ElementTree.SubElement(video_track, "track", {"producer": title_playlist.get("id", "")})
            blend = ElementTree.SubElement(video_track, "transition", {"id": "title_blend"})
            _property(blend, "mlt_service", "qtblend")
            _property(blend, "a_track", "0")
            _property(blend, "b_track", "1")
            _property(blend, "always_active", "1")
            # Without an explicit composition mode qtblend freezes the whole frame behind the
            # title, so the picture underneath stops moving; verified by rendering both ways.
            _property(blend, "compositing", "1")
            video_element = video_track

        tracks = [("video", video_element)]
        for name, plan in audio_tracks:
            tracks.append((name, self._audio_playlist(header, name, plan, total)))

        # A fade from and to black is a pair of `mix` transitions on the composite video.
        tractor = ElementTree.SubElement(
            header, "tractor", {"id": "main", "in": "0", "out": str(max(total - 1, 0))}
        )
        for _name, element in tracks:
            ElementTree.SubElement(tractor, "track", {"producer": element.get("id", "")})
        return _serialise(header)

    def _title_producers(self, header: ElementTree.Element) -> None:
        """One `qtext` producer per title."""

        for index, title in enumerate(self.titles):
            producer = ElementTree.SubElement(
                header,
                "producer",
                {
                    "id": f"title{index}",
                    "in": "0",
                    "out": str(max(frames(title.duration, self.fps) - 1, 0)),
                },
            )
            _property(producer, "mlt_service", "qtext")
            _property(producer, "argument", title.text)
            _property(producer, "length", str(max(frames(title.duration, self.fps), 1)))
            _property(producer, "geometry", title.geometry)
            _property(producer, "family", title.family)
            _property(producer, "size", str(title.size))
            _property(producer, "fgcolour", title.colour)
            _property(producer, "olcolour", title.outline)
            _property(producer, "bgcolour", "#00000000")

    def _title_playlist(self, header: ElementTree.Element, total: int) -> ElementTree.Element:
        """Titles padded with transparent blanks so each one lands on time."""

        fps = self.fps
        playlist = ElementTree.SubElement(header, "playlist", {"id": "titles"})
        cursor = 0
        for index, title in enumerate(self.titles):
            start = min(frames(title.start, fps), max(total - 1, 0))
            length = max(min(frames(title.duration, fps), max(total - start, 1)), 1)
            if start > cursor:
                ElementTree.SubElement(playlist, "blank", {"length": str(start - cursor)})
            ElementTree.SubElement(
                playlist,
                "entry",
                {"producer": f"title{index}", "in": "0", "out": str(length - 1)},
            )
            cursor = start + length
        if total > cursor:
            ElementTree.SubElement(playlist, "blank", {"length": str(total - cursor)})
        return playlist

    def audio_tracks(self) -> list[tuple[str, AudioPlan]]:
        """Named audio tracks in playback order."""

        tracks: list[tuple[str, AudioPlan]] = []
        if self.voice is not None:
            tracks.append(("voice", self.voice))
        if self.music is not None:
            tracks.append(("music", self.music))
        tracks.extend((f"audio{index}", plan) for index, plan in enumerate(self.extra_audio))
        return tracks

    def _audio_playlist(
        self, header: ElementTree.Element, name: str, plan: AudioPlan, total: int
    ) -> tuple[ElementTree.Element, ElementTree.Element]:
        fps = self.fps
        offset = frames(plan.in_point, fps)
        playlist = ElementTree.SubElement(header, "playlist", {"id": f"{name}_playlist"})

        # One entry per constant-level span: an XML filter cannot animate, but it can hold a
        # level, and a chain of entries is what turns that into a duck with ramps.
        for index, segment in enumerate(level_segments(plan, total / fps, fps)):
            entry = ElementTree.SubElement(
                playlist,
                "entry",
                {
                    "producer": f"{name}_producer",
                    "in": str(segment.start_frame + offset),
                    "out": str(max(segment.end_frame + offset - 1, segment.start_frame + offset)),
                },
            )
            if abs(segment.level_db) > 0.05:
                audio_filter = ElementTree.SubElement(
                    entry, "filter", {"id": f"{name}_level{index}"}
                )
                _property(audio_filter, "mlt_service", "volume")
                _property(audio_filter, "level", f"{segment.level_db:.2f}")
        return playlist


# MLT's `volume` filter documents its animated `level` property as gain in dB, not a factor.
SILENCE_DB = -120.0


def to_decibels(amplitude: float) -> float:
    """Linear amplitude to dB, floored so silence stays a finite number MLT accepts."""

    if amplitude <= 0:
        return SILENCE_DB
    return max(20 * math.log10(amplitude), SILENCE_DB)


@dataclass(frozen=True)
class AudioSegment:
    """A span of constant level: the unit `melt` can actually apply from XML."""

    start_frame: int
    end_frame: int
    level_db: float

    @property
    def length(self) -> int:
        return self.end_frame - self.start_frame


def envelope_at(plan: AudioPlan, frame: int, total: int, fps: float) -> float:
    """Linear amplitude of the plan at one frame, before conversion to dB."""

    level = plan.gain
    if plan.fade_in > 0:
        fade_frames = frames(plan.fade_in, fps)
        if fade_frames and frame < fade_frames:
            level = plan.gain * (frame / fade_frames)
    if plan.fade_out > 0:
        fade_frames = frames(plan.fade_out, fps)
        if fade_frames and frame >= total - fade_frames:
            remaining = max(total - 1 - frame, 0) / max(fade_frames - 1, 1)
            level = min(level, plan.gain * remaining)
    ramp = frames(plan.duck_fade, fps)
    ducked = plan.gain * plan.duck_level
    for begin, end in plan.ducking:
        begin_frame = frames(begin, fps)
        end_frame = frames(end, fps)
        if not ramp:
            if begin_frame <= frame < end_frame:
                level = min(level, ducked)
        elif begin_frame - ramp <= frame < begin_frame:
            progress = (frame - (begin_frame - ramp)) / ramp
            level = min(level, plan.gain + progress * (ducked - plan.gain))
        elif begin_frame <= frame < end_frame:
            level = min(level, ducked)
        elif end_frame <= frame < end_frame + ramp:
            progress = (frame - end_frame) / ramp
            level = min(level, ducked + progress * (plan.gain - ducked))
    return max(level, 0.0)


def _ramp_ranges(plan: AudioPlan, total: int, fps: float) -> list[tuple[int, int]]:
    """Frame ranges that ramp rather than hold, from fades and ducking."""

    ranges: list[tuple[int, int]] = []
    if plan.fade_in > 0:
        ranges.append((0, min(frames(plan.fade_in, fps), total)))
    if plan.fade_out > 0:
        ranges.append((max(total - frames(plan.fade_out, fps), 0), total))
    ramp = frames(plan.duck_fade, fps)
    for begin, end in plan.ducking:
        begin_frame = frames(begin, fps)
        end_frame = frames(end, fps)
        ranges.append((max(begin_frame - ramp, 0), min(begin_frame, total)))
        ranges.append((max(end_frame, 0), min(end_frame + ramp, total)))
    return [(start, stop) for start, stop in ranges if stop > start]


# A ramp is quantised into one step per this many seconds, because each step becomes a separate
# playlist entry; 50 ms keeps a fade smooth without exploding the entry count.
RAMP_STEP_SECONDS = 0.05
MIN_RAMP_STEPS = 4
MAX_RAMP_STEPS = 60


def level_segments(plan: AudioPlan, duration: float, fps: float) -> list[AudioSegment]:
    """Split a track into spans of constant level in dB.

    MLT applies XML-declared filters statically (its XML module keeps nested properties as a
    string, so an animated `level` never reaches the filter), therefore a duck is expressed as
    consecutive entries: a short stepped ramp down, the ducked hold, then a stepped ramp back.
    """

    total = frames(duration, fps)
    if total <= 0:
        return []
    ramps = _ramp_ranges(plan, total, fps)

    boundaries = {0, total}
    for start, stop in ramps:
        boundaries.update({start, stop})
    ordered = sorted(boundaries)

    segments: list[AudioSegment] = []
    for left, right in itertools.pairwise(ordered):
        if right <= left:
            continue
        ramping = any(left < stop and right > start for start, stop in ramps)
        if ramping:
            seconds = (right - left) / fps
            pieces = max(MIN_RAMP_STEPS, min(MAX_RAMP_STEPS, round(seconds / RAMP_STEP_SECONDS)))
            pieces = min(pieces, right - left)
        else:
            pieces = 1
        for index in range(max(pieces, 1)):
            start = left + (right - left) * index // max(pieces, 1)
            stop = left + (right - left) * (index + 1) // max(pieces, 1)
            if stop <= start:
                continue
            if start == 0 and plan.fade_in > 0:
                level = envelope_at(plan, 0, total, fps)  # a fade must begin at silence
            elif stop >= total and plan.fade_out > 0:
                level = envelope_at(plan, total - 1, total, fps)  # and end at silence
            else:
                middle = start + (stop - start) // 2
                level = envelope_at(plan, middle, total, fps)
            segments.append(AudioSegment(start, stop, to_decibels(level)))

    merged: list[AudioSegment] = []
    for segment in segments:
        if (
            merged
            and merged[-1].end_frame == segment.start_frame
            and abs(merged[-1].level_db - segment.level_db) < 0.05
        ):
            previous = merged[-1]
            merged[-1] = AudioSegment(previous.start_frame, segment.end_frame, previous.level_db)
        else:
            merged.append(segment)
    return merged


def ducking_from_speech(
    speech: list[tuple[float, float]],
    duration: float,
    *,
    lead: float = 0.2,
    tail: float = 0.4,
    minimum_gap: float = 0.3,
) -> tuple[tuple[float, float], ...]:
    """Turn speech segments into duck intervals, merging anything too short to hear.

    `lead` opens the duck slightly before the voice starts and `tail` holds it after the voice
    stops, so words are never clipped by the ramp.
    """

    merged: list[list[float]] = []
    for start, end in sorted(speech):
        begin = max(start - lead, 0.0)
        finish = min(end + tail, duration)
        if merged and begin - merged[-1][1] < minimum_gap:
            merged[-1][1] = max(merged[-1][1], finish)
        else:
            merged.append([begin, finish])
    return tuple((round(begin, 3), round(finish, 3)) for begin, finish in merged)


def _dissolve(
    parent: ElementTree.Element,
    identifier: str,
    start: int,
    stop: int,
    *,
    a_track: int,
    b_track: int,
) -> ElementTree.Element:
    """A video dissolve over a frame window, expressed the way `melt` honours it.

    The window belongs on the element: `mix` is an audio-only transition, and the `start`/`end`
    properties of a transition are levels, not a time range.
    """

    transition = ElementTree.SubElement(
        parent, "transition", {"id": identifier, "in": str(start), "out": str(stop)}
    )
    _property(transition, "mlt_service", "luma")
    _property(transition, "a_track", str(a_track))
    _property(transition, "b_track", str(b_track))
    return transition


def _property(parent: ElementTree.Element, name: str, value: str) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, "property", {"name": name})
    element.text = value
    return element


def _serialise(root: ElementTree.Element) -> str:
    ElementTree.indent(root, space="  ")
    body = ElementTree.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'
