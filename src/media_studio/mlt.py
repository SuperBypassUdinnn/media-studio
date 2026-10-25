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
    """One shot on the video track."""

    resource: Path
    duration: float
    in_point: float = 0.0

    def out_point(self) -> float:
        return self.in_point + self.duration


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
class Timeline:
    """A complete edit: shots, an optional music bed, and an optional voice track."""

    width: int
    height: int
    fps: float
    clips: tuple[Clip, ...]
    music: AudioPlan | None = None
    voice: AudioPlan | None = None
    fade_in: float = DEFAULT_TRANSITION
    fade_out: float = DEFAULT_TRANSITION
    title: str = "media-studio timeline"
    extra_audio: tuple[AudioPlan, ...] = field(default=())

    @property
    def duration(self) -> float:
        return round(sum(clip.duration for clip in self.clips), 6)

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
        header.append(
            ElementTree.Element(
                "profile",
                {
                    "description": f"{self.width}x{self.height} {fps:g}fps",
                    "width": str(self.width),
                    "height": str(self.height),
                    "progressive": "1",
                    "sample_aspect_num": "1",
                    "sample_aspect_den": "1",
                    "display_aspect_num": "16",
                    "display_aspect_den": "9",
                    "frame_rate_num": f"{fps:g}",
                    "frame_rate_den": "1",
                    "colorspace": "709",
                },
            )
        )

        video = ElementTree.SubElement(header, "playlist", {"id": "video"})
        for index, clip in enumerate(self.clips):
            producer_id = f"clip{index}"
            producer = ElementTree.SubElement(
                header,
                "producer",
                {
                    "id": producer_id,
                    "in": str(frames(clip.in_point, fps)),
                    "out": str(max(frames(clip.out_point(), fps) - 1, 0)),
                },
            )
            _property(producer, "resource", str(clip.resource))
            ElementTree.SubElement(
                video,
                "entry",
                {
                    "producer": producer_id,
                    "in": str(frames(clip.in_point, fps)),
                    "out": str(max(frames(clip.out_point(), fps) - 1, 0)),
                },
            )

        tracks = [("video", video, None)]
        for name, plan in self.audio_tracks():
            playlist, _producer = self._audio_block(header, name, plan, total)
            tracks.append((name, playlist, plan))

        # A fade from and to black is a pair of `mix` transitions on the tractor.
        tractor = ElementTree.SubElement(
            header, "tractor", {"id": "main", "in": "0", "out": str(total - 1)}
        )
        for _name, playlist, _plan in tracks:
            ElementTree.SubElement(tractor, "track", {"producer": playlist.get("id", "")})
        for index, (start, end) in enumerate(self._fades(fps)):
            transition = ElementTree.SubElement(
                tractor,
                "transition",
                {"id": f"fade{index}"},
            )
            _property(transition, "mlt_service", "mix")
            _property(transition, "a_track", "0")
            _property(transition, "b_track", "0")
            _property(transition, "start", str(start))
            _property(transition, "end", str(end))
        return _serialise(header)

    def audio_tracks(self) -> list[tuple[str, AudioPlan]]:
        """Named audio tracks in playback order."""

        tracks: list[tuple[str, AudioPlan]] = []
        if self.voice is not None:
            tracks.append(("voice", self.voice))
        if self.music is not None:
            tracks.append(("music", self.music))
        tracks.extend((f"audio{index}", plan) for index, plan in enumerate(self.extra_audio))
        return tracks

    def _audio_block(
        self, header: ElementTree.Element, name: str, plan: AudioPlan, total: int
    ) -> tuple[ElementTree.Element, ElementTree.Element]:
        fps = self.fps
        offset = frames(plan.in_point, fps)
        producer = ElementTree.SubElement(
            header,
            "producer",
            {"id": f"{name}_producer", "in": "0", "out": str(max(total - 1, 0))},
        )
        _property(producer, "resource", str(plan.resource))
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
        return playlist, producer

    def _fades(self, fps: float) -> list[tuple[int, int]]:
        """Frame ranges for the opening and closing fade."""

        total = frames(self.duration, fps)
        fades: list[tuple[int, int]] = []
        if self.fade_in > 0:
            fades.append((0, min(frames(self.fade_in, fps), total - 1)))
        if self.fade_out > 0:
            start = max(total - frames(self.fade_out, fps), 1)
            fades.append((start, total - 1))
        return fades


# MLT's `volume` filter documents its animated `level` property as gain in dB
# (`melt -query filter=volume`: type float, unit dB, animation yes). It is not a linear factor,
# which is an easy and silent mistake: sending 0.3 as a factor ducks by 0.3 dB, not by 10 dB.
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


def _property(parent: ElementTree.Element, name: str, value: str) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, "property", {"name": name})
    element.text = value
    return element


def _serialise(root: ElementTree.Element) -> str:
    ElementTree.indent(root, space="  ")
    body = ElementTree.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'
