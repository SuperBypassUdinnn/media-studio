"""Authoring plain MLT XML: what `melt` renders, and the envelope Kdenlive needs to open it.

Everything here is a pure function of a plan, so the XML and the audio curves can be checked
without running a renderer. Times cross into MLT as frame numbers, which keeps a 60 fps timeline
exact; floating-point timecodes would drift by a frame at the edges.

Audio shaping (gain, fades, ducking) becomes one animated `level` curve per track, attached at
playlist level so keyframes are relative to the timeline rather than to a clip.
"""

from __future__ import annotations

import itertools
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from . import fonts

MLT_VERSION = "7.42.0"
DEFAULT_TRANSITION = 0.5
# Kdenlive refuses a project file whose document version it cannot read, and reads that version from
# `kdenlive:docproperties.version`. The value below is what Kdenlive 26.08.2 writes itself; the
# surrounding envelope in `to_kdenlive_xml` mirrors a project saved by that version.
KDENLIVE_DOCUMENT_VERSION = "1.1"
# Track height in pixels, also taken from a project Kdenlive wrote itself; it only affects how the
# timeline looks when the document is first opened.
KDENLIVE_TRACK_HEIGHT = "62"
# Kdenlive tags a sequence producer with this type; a document without it leaves the sequence
# untyped, which is not what a project Kdenlive writes looks like.
KDENLIVE_SEQUENCE_TYPE = "17"
# Kdenlive's Text type, which is what a `kdenlivetitle` clip is loaded as; the bin then offers the
# title editor for it instead of treating it as footage.
KDENLIVE_TITLE_TYPE = "6"
# Kdenlive keeps sequences in a bin folder of their own and writes that folder into the bin as
# `kdenlive:folder.<parent>.<id>`. The values below are the ones a project saved by Kdenlive 26.08.2
# carries, and the sequence's own `kdenlive:folderid` points at the same number.
KDENLIVE_SEQUENCE_FOLDER = "2"
KDENLIVE_SEQUENCE_FOLDER_NAME = "Sequences"
# A sequence's bin entry is written as a zero length timecode rather than a frame count, which is
# how Kdenlive itself records it.
KDENLIVE_SEQUENCE_ENTRY_OUT = "00:00:00.000"
# Kdenlive stores the document's profile as the name of an MLT preset, which is what it later reads
# back for the frame rate. The names below are the presets MLT ships for the sizes this toolkit
# renders; a profile outside the table gets no name rather than an invented one.
KDENLIVE_PROFILE_PRESETS = {
    (1280, 720, 24.0): "atsc_720p_24",
    (1280, 720, 25.0): "atsc_720p_25",
    (1280, 720, 30.0): "atsc_720p_30",
    (1280, 720, 50.0): "atsc_720p_50",
    (1280, 720, 60.0): "atsc_720p_60",
    (1920, 1080, 24.0): "atsc_1080p_24",
    (1920, 1080, 25.0): "atsc_1080p_25",
    (1920, 1080, 30.0): "atsc_1080p_30",
    (1920, 1080, 50.0): "atsc_1080p_50",
    (1920, 1080, 60.0): "atsc_1080p_60",
}
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
    filters: tuple[tuple[str, dict[str, str]], ...] = ()  # static MLT filters, name then properties

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

        return _serialise(self._tree(root))

    def to_kdenlive_xml(
        self,
        root: Path | None = None,
        *,
        kdenlive_version: str = "",
        document_id: str = "",
    ) -> str:
        """Serialise the timeline inside the envelope Kdenlive needs to open it as a project.

        The edit itself is identical to `to_xml`; what is added is what Kdenlive looks for before it
        will read a document: a `main_bin` playlist carrying the document properties (including the
        format version it refuses to guess), track properties on the sequence, and a final tractor
        marked as the project tractor.
        """

        header = self._tree(root)
        header.set("producer", "main_bin")
        # The render input deliberately leaves sample aspect out: with it declared as 1:1 this MLT
        # build drops qtblend title composites. Kdenlive reads the field and does integer arithmetic
        # with it, so the handoff document declares it and only the handoff does.
        profile = header.find("profile")
        if profile is not None:
            profile.set("sample_aspect_num", "1")
            profile.set("sample_aspect_den", "1")
        sequence_uuid = _kdenlive_uuid(document_id)
        fps = self.fps
        total = frames(self.duration, fps)
        self._substitute_title_fonts(header)

        for index, producer in enumerate(header.findall("producer")):
            identifier = producer.get("id", f"clip{index}")
            service = _property_value(producer, "mlt_service")
            is_title = service == "kdenlivetitle"
            if is_title:
                clipname = _property_value(producer, "text")
            else:
                clipname = Path(producer.findtext("property[@name='resource']") or identifier).name
            _properties(
                producer,
                (
                    # No `kdenlive:uuid` here. Kdenlive reserves that property for sequences: its
                    # bin loader sends every entry that carries one down the sequence branch, and an
                    # avformat producer there is logged as an "incorrect sequence found in project
                    # bin" and skipped, so the clip never reaches the bin index and every timeline
                    # instance of it is then removed as "without bin reference found".
                    # Kdenlive maps a timeline clip back to its bin clip through this property: the
                    # rebuild walks the bin entries, and a timeline instance whose producer has no
                    # control uuid here is dropped as "without bin reference found and removed".
                    ("kdenlive:control_uuid", _kdenlive_uuid(f"{document_id}/{identifier}")),
                    ("kdenlive:clipname", clipname),
                    # Kdenlive's Text type is what makes double-clicking the clip open the title
                    # editor; anything else loads the clip as ordinary footage.
                    ("kdenlive:producer_type", KDENLIVE_TITLE_TYPE if is_title else "2"),
                    ("kdenlive:id", str(index + 1)),
                    ("kdenlive:clip_type", "0"),
                    ("kdenlive:folderid", "-1"),
                    ("kdenlive:maxduration", str(total)),
                ),
            )

        # Kdenlive reads the frame rate back from this name, and a document without it is what a
        # zero divisor later in its timeline maths looks like, so it is written whenever known.
        profile_name = kdenlive_profile_name(self.width, self.height, fps)
        bin_playlist = ElementTree.Element("playlist", {"id": "main_bin"})
        # Kdenlive rebuilds the bin from the XML that MLT *retains* for this playlist, so a document
        # without the retain marker opens with an empty bin no matter which entries it lists.
        _properties(
            bin_playlist,
            (
                ("xml_retain", "1"),
                ("kdenlive:docproperties.version", KDENLIVE_DOCUMENT_VERSION),
                ("kdenlive:docproperties.kdenliveversion", kdenlive_version),
                ("kdenlive:docproperties.documentid", document_id),
                ("kdenlive:docproperties.profile", profile_name),
                ("kdenlive:docproperties.patchversion", "1"),
                ("kdenlive:docproperties.uuid", sequence_uuid),
                ("kdenlive:docproperties.activetimeline", sequence_uuid),
                ("kdenlive:docproperties.opensequences", sequence_uuid),
                ("kdenlive:docproperties.sessionid", _kdenlive_uuid(f"{document_id}/session")),
                ("kdenlive:docproperties.enableTimelineZone", "0"),
                ("kdenlive:docproperties.binsort", "0"),
                ("kdenlive:docproperties.audioChannels", "2"),
                ("kdenlive:docproperties.decimalPoint", "."),
                ("kdenlive:docproperties.enableproxy", "0"),
                ("kdenlive:docproperties.generateproxy", "0"),
                ("kdenlive:docproperties.generateimageproxy", "0"),
                ("kdenlive:expandedFolders", ""),
                ("kdenlive:sequenceFolder", KDENLIVE_SEQUENCE_FOLDER),
                (f"kdenlive:folder.-1.{KDENLIVE_SEQUENCE_FOLDER}", KDENLIVE_SEQUENCE_FOLDER_NAME),
                ("kdenlive:extraBins", "project_bin:-1:0"),
                ("kdenlive:binZoom", "4"),
            ),
        )
        media_producers = header.findall("producer")
        for producer in media_producers:
            ElementTree.SubElement(
                bin_playlist,
                "entry",
                {
                    "producer": producer.get("id", ""),
                    "in": "0",
                    "out": producer.get("out", str(max(total - 1, 0))),
                },
            )
        # The sequence is itself a bin clip, and Kdenlive aborts the load with "Could not recover
        # corrupted file." when the active timeline uuid has no clip in the bin.
        ElementTree.SubElement(
            bin_playlist,
            "entry",
            {
                "producer": sequence_uuid,
                "in": KDENLIVE_SEQUENCE_ENTRY_OUT,
                "out": KDENLIVE_SEQUENCE_ENTRY_OUT,
            },
        )

        sequence = header.find("tractor[@id='main']")
        if sequence is None:
            raise ValueError("the timeline has no main tractor to mark as the sequence")
        sequence.set("id", sequence_uuid)
        tracks = self._flatten_for_kdenlive(header, sequence, total)
        _properties(
            sequence,
            (
                ("kdenlive:uuid", sequence_uuid),
                ("kdenlive:clipname", "Sequence 1"),
                ("kdenlive:control_uuid", sequence_uuid),
                ("kdenlive:producer_type", KDENLIVE_SEQUENCE_TYPE),
                ("kdenlive:id", str(len(header.findall("producer")) + 1)),
                ("kdenlive:clip_type", "0"),
                ("kdenlive:file_size", "0"),
                ("kdenlive:folderid", KDENLIVE_SEQUENCE_FOLDER),
                ("kdenlive:sequenceproperties.tracksCount", str(len(tracks))),
                ("kdenlive:sequenceproperties.tracks", str(len(tracks))),
                ("kdenlive:sequenceproperties.hasVideo", "1"),
                ("kdenlive:sequenceproperties.hasAudio", "1" if self.audio_tracks() else "0"),
                ("kdenlive:sequenceproperties.documentuuid", sequence_uuid),
                ("kdenlive:sequenceproperties.activeTrack", "1"),
                ("kdenlive:sequenceproperties.videoTarget", "1"),
                ("kdenlive:sequenceproperties.audioTarget", "1"),
                ("kdenlive:sequenceproperties.position", "0"),
                ("kdenlive:sequenceproperties.scrollPos", "0"),
                ("kdenlive:sequenceproperties.verticalzoom", "1"),
                ("kdenlive:sequenceproperties.disablepreview", "0"),
                ("kdenlive:sequenceproperties.zonein", "0"),
                ("kdenlive:sequenceproperties.zoneout", str(total)),
                ("kdenlive:sequenceproperties.zoom", "8"),
                ("kdenlive:duration", _timecode(total, fps)),
                ("kdenlive:maxduration", str(total)),
                ("kdenlive:sequenceproperties.groups", "[\n]"),
                ("kdenlive:sequenceproperties.guides", "[\n]"),
                ("kdenlive:trackheight", KDENLIVE_TRACK_HEIGHT),
                ("kdenlive:timeline_active", "1"),
            ),
        )
        # The per-track fields belong on the track element itself, and Kdenlive names its tracks
        # after the direction they carry.
        video_count = 0
        audio_count = 0
        for track in tracks:
            if _property_value(track, "kdenlive:audio_track") == "1":
                audio_count += 1
                name = f"A{audio_count}"
            else:
                video_count += 1
                name = f"V{video_count}"
            _properties(
                track,
                (
                    ("kdenlive:track_name", name),
                    ("kdenlive:trackheight", KDENLIVE_TRACK_HEIGHT),
                    ("kdenlive:timeline_active", "1"),
                ),
            )

        # Written last, after the producers its entries reference, which is the order a project
        # Kdenlive saved itself uses.
        header.append(bin_playlist)
        project = ElementTree.SubElement(
            header,
            "tractor",
            {"id": "kdenlive_project", "in": "0", "out": sequence.get("out", "0")},
        )
        _property(project, "kdenlive:projectTractor", "1")
        ElementTree.SubElement(project, "track", {"producer": sequence_uuid})
        return _serialise(header)

    def _substitute_title_fonts(self, header: ElementTree.Element) -> None:
        """Name the family fontconfig actually resolves, so the load reports no clip problem.

        A text producer carries a family name and its renderer silently substitutes a missing one.
        Kdenlive turns that substitution into a "Clip Problems" dialog it makes the user dismiss
        before the project is opened, which is exactly the kind of surprise a handoff should not
        hand over. The render document keeps the requested family; only the handoff renames it. A
        title keeps its family inside the `xmldata` scene rather than in a property of its own, so
        both places are rewritten.
        """

        for producer in header.findall("producer"):
            family = producer.findtext("property[@name='family']")
            if family is not None:
                resolved = fonts.resolved_family(family)
                if resolved and resolved != family:
                    for property_element in producer.findall("property[@name='family']"):
                        property_element.text = resolved
            scene = producer.findtext("property[@name='xmldata']")
            if not scene:
                continue
            self._substitute_scene_font(producer, scene)

    @staticmethod
    def _substitute_scene_font(producer: ElementTree.Element, scene: str) -> None:
        """Rewrite the font in a title scene when fontconfig resolves it differently."""

        try:
            document = ElementTree.fromstring(scene)
        except ElementTree.ParseError:
            return
        changed = False
        for content in document.iter("content"):
            family = content.get("font", "")
            resolved = fonts.resolved_family(family)
            if resolved and resolved != family:
                content.set("font", resolved)
                changed = True
        if not changed:
            return
        for property_element in producer.findall("property[@name='xmldata']"):
            property_element.text = ElementTree.tostring(document, encoding="unicode")

    def _flatten_for_kdenlive(
        self, header: ElementTree.Element, sequence: ElementTree.Element, total: int
    ) -> list[ElementTree.Element]:
        """Rewrite the edit into the flat track model Kdenlive rebuilds, and return its tracks.

        Kdenlive rebuilds a sequence from tracks that are each a "double track": a tractor over
        exactly two playlists. It still reads a bare playlist track, but it then converts it during
        the rebuild, and that path is where its timeline refresh recurses without bound on these
        documents; a document already shaped the way Kdenlive saves its own loads cleanly. The
        render document nests tractors on purpose - a dissolve needs two playlists under one
        composite, and a title needs a composite of its own - so the same edit is flattened here:
        every playlist becomes a track of its own, and the transitions that joined them move onto
        the sequence with their track numbers rewritten to the new positions.
        """

        # A project Kdenlive writes starts the sequence with a black producer under the reserved
        # black track name, and every other track sits above it.
        black = ElementTree.Element(
            "producer", {"id": "producer0", "in": "0", "out": str(max(total - 1, 0))}
        )
        for name, value in (
            ("resource", "black"),
            ("mlt_service", "color"),
            ("kdenlive:playlistid", "black_track"),
            ("set.test_audio", "0"),
        ):
            _property(black, name, value)

        audio_ids = {f"{name}_playlist" for name, _plan in self.audio_tracks()}
        playlists: list[ElementTree.Element] = []
        moved: list[tuple[ElementTree.Element, ElementTree.Element, int, int]] = []
        collapsed: list[ElementTree.Element] = []

        def expand(element: ElementTree.Element) -> list[int]:
            """Flatten one sequence track into the positions it occupies, bottom track first."""

            if element.tag == "playlist":
                playlists.append(element)
                return [len(playlists)]
            collapsed.append(element)
            blocks = [
                expand(target)
                for target in (
                    header.find(f"*[@id='{track.get('producer', '')}']")
                    for track in element.findall("track")
                )
                if target is not None
            ]
            for transition in element.findall("transition"):
                a_track = int(_property_value(transition, "a_track") or 0)
                b_track = int(_property_value(transition, "b_track") or 0)
                # A transition inside a composite can name that composite as its background; the
                # flattened equivalent is the topmost track the block expanded into, which is what
                # Kdenlive composites a title with as well.
                background = blocks[a_track][-1] if a_track < len(blocks) and blocks[a_track] else 0
                foreground = blocks[b_track][-1] if b_track < len(blocks) and blocks[b_track] else 0
                moved.append((element, transition, background, foreground))
            return [position for block in blocks for position in block]

        for track in list(sequence.findall("track")):
            target = header.find(f"*[@id='{track.get('producer', '')}']")
            if target is not None:
                expand(target)

        for track in list(sequence.findall("track")):
            sequence.remove(track)
        for element in collapsed:
            header.remove(element)
        header.insert(list(header).index(sequence), black)
        ElementTree.SubElement(sequence, "track", {"producer": black.get("id", "")})

        # Each track becomes the double-track tractor Kdenlive saves itself: two playlists under one
        # tractor, the second reserved for a clip that overlaps during a same-track transition. A
        # bare playlist track is still read, but the timeline rebuild then has to convert it, and on
        # a document like this one that conversion is where MLT's refresh starts recursing until the
        # stack dies. The companion playlist is declared before the tractor that references it: MLT
        # resolves a track producer while parsing, and an id it has not seen yet stays unresolved.
        tractors: list[ElementTree.Element] = []
        for playlist in playlists:
            identifier = playlist.get("id", "")
            is_audio = identifier in audio_ids
            companion = ElementTree.Element("playlist", {"id": f"{identifier}_b"})
            if is_audio:
                for element in (playlist, companion):
                    _property(element, "kdenlive:audio_track", "1")
            tractor = ElementTree.Element("tractor", {"id": f"tractor_{identifier}", "in": "0"})
            if is_audio:
                _property(tractor, "kdenlive:audio_track", "1")
            for producer_id in (identifier, companion.get("id", "")):
                ElementTree.SubElement(
                    tractor,
                    "track",
                    {"hide": "video" if is_audio else "audio", "producer": producer_id},
                )
            index = list(header).index(sequence)
            header.insert(index, companion)
            header.insert(index + 1, tractor)
            ElementTree.SubElement(sequence, "track", {"producer": tractor.get("id", "")})
            tractors.append(tractor)

        for parent, transition, background, foreground in moved:
            parent.remove(transition)
            service = _property_value(transition, "mlt_service")
            if (
                background > foreground
                and service == "luma"
                and not _property_value(transition, "resource")
            ):
                # Kdenlive reads a composition as one track drawn over another and reports any
                # other direction as "compositing with track N was set to forced track". MLT does
                # not care, and for a plain dissolve the two directions are the same frames with
                # the ramp inverted, so the pair is swapped and the ramp reversed instead.
                background, foreground = foreground, background
                _set_property(
                    transition,
                    "reverse",
                    "0" if _property_value(transition, "reverse") == "1" else "1",
                )
            _set_property(transition, "a_track", str(background))
            _set_property(transition, "b_track", str(foreground))
            # Kdenlive names a composition from this property; without it the load reports a
            # transition without an id even though it recovers the name from the service.
            if not _property_value(transition, "kdenlive_id"):
                _set_property(transition, "kdenlive_id", _property_value(transition, "mlt_service"))
            sequence.append(transition)
        return tractors

    def _tree(self, root: Path | None = None) -> ElementTree.Element:
        """Build the MLT element tree for this timeline."""

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
                    # deliverable is described correctly. Sample aspect is declared 1:1 so the
                    # rendered file matches the canvas (16:9 here) instead of being pillarboxed to
                    # 256:135; the handoff document declares the same field and only the handoff
                    # needs it for Kdenlive's profile bookkeeping.
                    "sample_aspect_num": "1",
                    "sample_aspect_den": "1",
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
            for service, properties in clip.filters:
                clip_filter = ElementTree.SubElement(
                    producer, "filter", {"id": f"clip{index}_{service}"}
                )
                _property(clip_filter, "mlt_service", service)
                for name, value in properties.items():
                    _property(clip_filter, name, value)

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
            # `compositing=1` blends the title track at a few percent opacity instead of compositing
            # it, which makes the title invisible over a moving picture; `compositing=0` is what
            # Kdenlive writes for the compositions it saves itself, and it draws the title over the
            # tracks below while they keep playing. Verified against a static pair of colour
            # producers (where `compositing=1` returned the lower track's colour essentially
            # unchanged) and against this timeline, where the title reads normally and the picture
            # underneath still advances.
            _property(blend, "compositing", "0")
            _property(blend, "distort", "0")
            _property(blend, "rotate_center", "0")
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
        return header

    def _title_producers(self, header: ElementTree.Element) -> None:
        """One `kdenlivetitle` producer per title, carrying the document Kdenlive's titler saves.

        `qtext` is the wrong producer twice over: it renders only from a `text`/`resource`
        property, so a title written with the service `argument` renders nothing, and it rescales
        its bitmap to whatever the consumer asks for, so a title sized 36 px fills the frame.
        `kdenlivetitle` renders the scene at the frame's own coordinates, which is what
        `Title.geometry` and `Title.size` mean, and it is the producer Kdenlive's own title clips
        use, so the handoff opens as an editable title rather than as a stray text clip.
        """

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
            _property(producer, "mlt_service", "kdenlivetitle")
            _property(producer, "xmldata", _title_document(title, self.width, self.height))
            _property(producer, "length", str(max(frames(title.duration, self.fps), 1)))

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


def kdenlive_profile_name(width: int, height: int, fps: float) -> str:
    """The MLT preset name for a canvas, or "" when it is not one this toolkit can name.

    Kdenlive reads the document's frame rate back from this name, so a size outside the table gets
    nothing instead of a plausible-looking invention.
    """

    return KDENLIVE_PROFILE_PRESETS.get((width, height, float(fps)), "")


def _title_document(title: Title, width: int, height: int) -> str:
    """The title scene Kdenlive's titler writes, which is what `kdenlivetitle` renders.

    The producer draws this XML with Qt at the frame's own coordinates, so the text keeps the pixel
    size and the corner it was asked for. Only the single text item a `Title` describes is written;
    `box-width` is left out on purpose, which is the shape the MLT title wrapper reads as "measure
    the text yourself".
    """

    root = ElementTree.Element(
        "kdenlivetitle",
        {"width": str(width), "height": str(height), "LC_NUMERIC": "C"},
    )
    item = ElementTree.SubElement(root, "item", {"type": "QGraphicsTextItem"})
    content = ElementTree.SubElement(
        item,
        "content",
        {
            "font": title.family,
            "font-weight": "50",
            "font-pixel-size": str(title.size),
            "font-italic": "0",
            "font-underline": "0",
            "letter-spacing": "0",
            "font-color": _kdenlive_colour(title.colour),
            "font-outline": "2",
            "font-outline-color": _kdenlive_colour(title.outline),
            "shadow": "0;#64000000;3;3;1",
            "typewriter": "0;2;1;0;0",
        },
    )
    content.text = title.text
    x, y = _geometry_offset(title.geometry)
    position = ElementTree.SubElement(item, "position", {"x": f"{x}", "y": f"{y}"})
    ElementTree.SubElement(position, "transform").text = "1,0,0,0,1,0,0,0,1"
    # A title is an overlay, so its own background is fully transparent.
    ElementTree.SubElement(root, "background", {"color": "0,0,0,0"})
    return ElementTree.tostring(root, encoding="unicode")


def _geometry_offset(geometry: str) -> tuple[int, int]:
    """The x and y of an MLT `+x+y` geometry, as the title scene wants them."""

    numbers = re.findall(r"[-+]?\d+", geometry or "")
    if len(numbers) < 2:
        return 0, 0
    return int(numbers[0]), int(numbers[1])


def _kdenlive_colour(colour: str) -> str:
    """An MLT `#aarrggbb` colour as the `r,g,b,a` component list the titler writes."""

    value = (colour or "").lstrip("#")
    if len(value) == 6:
        value = f"ff{value}"
    if len(value) != 8:
        return "255,255,255,255"
    alpha, red, green, blue = (int(value[index : index + 2], 16) for index in range(0, 8, 2))
    return f"{red},{green},{blue},{alpha}"


def _timecode(frames_count: int, fps: float) -> str:
    """A timecode in the `HH:MM:SS:FF` form Kdenlive writes for a duration."""

    rate = max(round(fps), 1)
    seconds, remainder = divmod(max(frames_count, 0), rate)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{remainder:02d}"


def _kdenlive_uuid(seed: str) -> str:
    """A stable UUID in the braces Kdenlive writes, derived from the document instead of random."""

    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"media-studio/{seed}")) + "}"


def _properties(parent: ElementTree.Element, pairs: tuple[tuple[str, str], ...]) -> None:
    """Emit the pairs that carry a value, which is how every Kdenlive property block is written."""

    for name, value in pairs:
        if value:
            _property(parent, name, value)


def _property(parent: ElementTree.Element, name: str, value: str) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, "property", {"name": name})
    element.text = value
    return element


def _property_value(element: ElementTree.Element, name: str) -> str:
    """The value of one property on an element, or an empty string when it carries none."""

    for item in element.findall("property"):
        if item.get("name") == name:
            return item.text or ""
    return ""


def _set_property(element: ElementTree.Element, name: str, value: str) -> None:
    """Rewrite one property in place, adding it when the element has none of that name."""

    for item in element.findall("property"):
        if item.get("name") == name:
            item.text = value
            return
    _property(element, name, value)


def _serialise(root: ElementTree.Element) -> str:
    ElementTree.indent(root, space="  ")
    body = ElementTree.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n{body}\n'
