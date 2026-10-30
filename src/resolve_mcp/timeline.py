"""
Build a DaVinci Resolve timeline from a media-studio ``project.yaml``.

The two tools meet at the artifact level: ``project.yaml`` is the shot-list
authority, and this module reads it directly instead of converting through an
intermediate format (MLT, EDL, FCPXML) that Resolve would have to re-interpret.

Planning (``plan_from_project``) is pure and testable without Resolve.
Execution (``build_timeline``) talks to the Resolve Python API and reports
honestly what it applied and what it skipped, with a reason for every skip.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from media_studio.manifest import Project, load_project


class TimelineError(RuntimeError):
    """Raised when a project cannot be turned into a timeline plan."""


# Presets that describe a look Resolve cannot reproduce with a single call.
# Each maps to the note shown when the effect is skipped.
_UNSUPPORTED_VIDEO_EFFECTS = {
    "video-glitch-wave": "wave-glitch is an MLT filter; use a Fusion macro for this look",
    "video-soften": "soften is an MLT filter; use a Fusion Blur node for this look",
    "video-crop-vertical": "crop is a framing decision; set the timeline resolution instead",
    "video-brighten": "brighten is an MLT filter; grade the clip in the Color page",
    "video-gamma-lift": "gamma-lift is an MLT filter; grade the clip in the Color page",
}

# Presets that are a colour look Resolve can reproduce with a grade or a LUT.
_GRADE_PRESETS = {
    "video-warm-grade": "warm grade",
    "video-sepia": "sepia grade",
}

# Transition presets that Resolve has a native equivalent for.
_TRANSITION_PRESETS = {
    "video-cross-dissolve": "Cross Dissolve",
    "video-dip-to-black": "Dip to Color Dissolve",
}


@dataclass(frozen=True)
class PlannedClip:
    """One clip to append to the timeline, in frames."""

    asset_id: str
    path: Path
    in_frame: int
    out_frame: int
    transition: str = "cut"


@dataclass(frozen=True)
class PlannedAudio:
    """A music or voice bed, in frames."""

    asset_id: str
    path: Path
    in_frame: int
    out_frame: int
    gain: float = 1.0


@dataclass(frozen=True)
class PlannedTitle:
    """A text overlay, in frames."""

    text: str
    start_frame: int
    duration_frames: int
    size: int = 64
    colour: str = "#ffffffff"
    position: str = "+0+0"


@dataclass(frozen=True)
class TimelinePlan:
    """Everything needed to build one Resolve timeline, with no Resolve objects."""

    name: str
    fps: float
    width: int
    height: int
    clips: tuple[PlannedClip, ...] = ()
    audio: tuple[PlannedAudio, ...] = ()
    titles: tuple[PlannedTitle, ...] = ()
    grade_targets: tuple[str, ...] = ()
    transitions: tuple[tuple[int, str], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass
class BuildResult:
    """What ``build_timeline`` actually did."""

    timeline_name: str
    clips_added: int = 0
    audio_added: int = 0
    titles_added: int = 0
    transitions_added: int = 0
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _parse_resolution(text: str | None) -> tuple[int, int]:
    """Turn ``"1280x720"`` into ``(1280, 720)``, defaulting to 1920x1080."""

    if not text:
        return 1920, 1080
    lowered = text.lower().replace(" ", "")
    if "x" not in lowered:
        raise TimelineError(f"deliverable resolution {text!r} is not WIDTHxHEIGHT")
    width_text, _, height_text = lowered.partition("x")
    try:
        return int(width_text), int(height_text)
    except ValueError as error:
        raise TimelineError(f"deliverable resolution {text!r} is not numeric") from error


def _asset_path(project: Project, asset_id: str) -> Path:
    """Resolve an asset id to an absolute path, failing loudly when it is missing."""

    relative = project.assets.get(asset_id)
    if relative is None:
        raise TimelineError(
            f"video.shots references asset {asset_id!r}, which assets does not define"
        )
    path = project.root / relative
    if not path.is_file():
        raise TimelineError(f"asset {asset_id!r} points at {path}, which does not exist")
    return path


def plan_from_project(location: str | Path | None = None) -> TimelinePlan:
    """
    Read ``project.yaml`` and return a :class:`TimelinePlan` in frames.

    Raises :class:`TimelineError` when the project has no video plan, declares
    no video deliverable, or references an asset that is not on disk.
    """

    project = load_project(location)
    plan = project.video
    if plan is None or not plan.shots:
        raise TimelineError(f"{project.slug} has no video.shots, so there is nothing to lay down")

    deliverable = next((d for d in project.deliverables if d.kind == "video"), None)
    if deliverable is None:
        raise TimelineError(f"{project.slug} declares no deliverable of kind 'video'")

    fps = float(deliverable.fps or 30.0)
    width, height = _parse_resolution(deliverable.resolution)
    notes: list[str] = []

    clips: list[PlannedClip] = []
    transitions: list[tuple[int, str]] = []
    for index, shot in enumerate(plan.shots):
        path = _asset_path(project, shot.asset)
        in_frame = round(shot.in_point * fps)
        if shot.duration is None:
            raise TimelineError(
                f"shot {index + 1} ({shot.asset}) has no duration; "
                "a Resolve timeline needs an explicit out point"
            )
        length = max(1, round(shot.duration * fps))
        clips.append(
            PlannedClip(
                asset_id=shot.asset,
                path=path,
                in_frame=in_frame,
                out_frame=in_frame + length - 1,
                transition=shot.transition,
            )
        )
        if shot.transition == "crossfade" and index > 0:
            transitions.append((index, "Cross Dissolve"))

    audio: list[PlannedAudio] = []
    for cue, label in ((plan.music, "music"), (plan.voice, "voice")):
        if cue is None:
            continue
        path = _asset_path(project, cue.asset)
        start = round(cue.in_point * fps)
        length = max(1, round((deliverable.duration or 0.0) * fps)) or 1
        audio.append(
            PlannedAudio(
                asset_id=cue.asset,
                path=path,
                in_frame=start,
                out_frame=start + length - 1,
                gain=cue.gain,
            )
        )
        if cue.duck:
            notes.append(
                f"{label} asks for ducking; Resolve needs a sidechain in Fairlight, "
                "so duck it with `ms`/ffmpeg before importing"
            )

    titles = tuple(
        PlannedTitle(
            text=title.text,
            start_frame=round(title.start * fps),
            duration_frames=max(1, round(title.duration * fps)),
            size=title.size,
            colour=title.colour,
            position=title.position,
        )
        for title in plan.titles
    )

    grade_targets: list[str] = []
    for entry in project.effects:
        preset_id = str(entry.get("id") or "")
        target = str(entry.get("at") or "")
        if preset_id in _GRADE_PRESETS:
            if target and target not in grade_targets:
                grade_targets.append(target)
            notes.append(
                f"effect {preset_id} is a colour look; grade shot {target or '(all)'} "
                "in the Color page or apply a matching LUT"
            )
        elif preset_id in _UNSUPPORTED_VIDEO_EFFECTS:
            notes.append(f"effect {preset_id}: {_UNSUPPORTED_VIDEO_EFFECTS[preset_id]}")
        elif preset_id in _TRANSITION_PRESETS:
            notes.append(
                f"effect {preset_id} is expressed as a shot transition in project.yaml; "
                "the transition is laid down from the shot, not the effect"
            )
        elif preset_id.startswith("title-"):
            notes.append(f"effect {preset_id} duplicates video.titles; titles are laid down once")
        elif preset_id == "audio-music-bed":
            notes.append("effect audio-music-bed is expressed as video.music; not applied twice")

    return TimelinePlan(
        name=project.slug,
        fps=fps,
        width=width,
        height=height,
        clips=tuple(clips),
        audio=tuple(audio),
        titles=titles,
        grade_targets=tuple(grade_targets),
        transitions=tuple(transitions),
        notes=tuple(notes),
    )


def build_timeline(
    location: str | Path | None = None,
    *,
    media_pool=None,
    project=None,
    apply_grades: bool = False,
    lut_name: str | None = None,
) -> BuildResult:
    """
    Lay a ``project.yaml`` shot list down as a new Resolve timeline.

    Parameters
    ----------
    location:
        Project directory or any path inside it. Defaults to the current directory.
    media_pool:
        A live ``MediaPool`` object. When omitted it is taken from the open Resolve
        project, which means Resolve must be running.
    project:
        A live Resolve ``Project``. Taken from Resolve when omitted.
    apply_grades:
        When true, apply ``lut_name`` to every clip named in ``plan.grade_targets``.
    lut_name:
        Installed LUT name to apply when ``apply_grades`` is true.

    Returns a :class:`BuildResult` listing what was applied and what was skipped.
    """

    plan = plan_from_project(location)

    if media_pool is None:
        from resolve_mcp.resolve_bridge import get_media_pool

        media_pool = get_media_pool()
    if project is None:
        from resolve_mcp.resolve_bridge import get_current_project

        project = get_current_project()

    result = BuildResult(timeline_name=plan.name)
    result.notes = list(plan.notes)

    media_pool.SetCurrentFolder(media_pool.GetRootFolder())

    try:
        project.SetSetting("timelineFrameRate", str(plan.fps))
        project.SetSetting("timelineResolutionWidth", str(plan.width))
        project.SetSetting("timelineResolutionHeight", str(plan.height))
        result.applied.append(f"set project to {plan.width}x{plan.height} {plan.fps:g} fps")
    except Exception as error:
        result.skipped.append(f"project resolution/fps: {error}")

    by_path: dict[Path, object] = {}
    for path in {clip.path for clip in plan.clips} | {cue.path for cue in plan.audio}:
        imported = media_pool.ImportMedia([str(path)])
        if not imported:
            result.skipped.append(f"could not import {path.name}")
            continue
        by_path[path] = imported[0]
        result.applied.append(f"imported {path.name}")

    timeline = media_pool.CreateEmptyTimeline(plan.name)
    if timeline is None:
        raise TimelineError(
            f"Resolve refused to create timeline {plan.name!r}; "
            "a timeline with that name may already exist"
        )

    for clip in plan.clips:
        item = by_path.get(clip.path)
        if item is None:
            result.skipped.append(f"clip {clip.asset_id} was not imported")
            continue
        appended = media_pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": item,
                    "startFrame": clip.in_frame,
                    "endFrame": clip.out_frame,
                    "trackIndex": 1,
                    "mediaType": 1,
                }
            ]
        )
        if appended:
            result.clips_added += 1
        else:
            result.skipped.append(f"Resolve refused clip {clip.asset_id}")

    for index, transition_name in plan.transitions:
        try:
            timeline.ApplyTransition(transition_name, 1)
            result.transitions_added += 1
        except Exception as error:
            result.skipped.append(f"transition {transition_name} at shot {index}: {error}")

    audio_track = 1
    for cue in plan.audio:
        item = by_path.get(cue.path)
        if item is None:
            result.skipped.append(f"audio {cue.asset_id} was not imported")
            continue
        appended = media_pool.AppendToTimeline(
            [
                {
                    "mediaPoolItem": item,
                    "startFrame": cue.in_frame,
                    "endFrame": cue.out_frame,
                    "trackIndex": audio_track,
                    "mediaType": 2,
                }
            ]
        )
        if appended:
            result.audio_added += 1
            audio_track += 1
        else:
            result.skipped.append(f"Resolve refused audio {cue.asset_id}")

    for title in plan.titles:
        try:
            item = timeline.InsertFusionTitleIntoTimeline("Text+")
        except Exception as error:
            result.skipped.append(f"title {title.text!r}: {error}")
            continue
        if item is None:
            result.skipped.append(f"title {title.text!r}: Resolve returned no timeline item")
            continue
        result.titles_added += 1
        try:
            item.SetProperty("Start", title.start_frame)
            item.SetProperty("Duration", title.duration_frames)
        except Exception as error:
            result.skipped.append(f"title {title.text!r} timing: {error}")

    if apply_grades and lut_name:
        from resolve_mcp.presets import apply_lut_to_clip, find_lut

        lut = find_lut(lut_name)
        if lut is None:
            result.skipped.append(f"LUT {lut_name!r} is not installed")
        else:
            for asset_id in plan.grade_targets:
                item = next(
                    (
                        by_path[c.path]
                        for c in plan.clips
                        if c.asset_id == asset_id and c.path in by_path
                    ),
                    None,
                )
                if item is None:
                    result.skipped.append(f"grade {asset_id}: clip was not imported")
                    continue
                try:
                    apply_lut_to_clip(item, lut)
                    result.applied.append(f"applied LUT {lut.name} to {asset_id}")
                except Exception as error:
                    result.skipped.append(f"grade {asset_id}: {error}")
    elif plan.grade_targets:
        result.skipped.append(
            "grades requested for "
            + ", ".join(plan.grade_targets)
            + "; pass apply_grades=True with a lut_name to apply one"
        )

    result.applied.append(
        f"created timeline {plan.name!r} at {plan.width}x{plan.height} {plan.fps:g} fps"
    )
    return result
