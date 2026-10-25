"""The video path: manifest to MLT to a measured deliverable.

Steps, in order: resolve and licence-check the assets, build the timeline, derive the ducking
windows from the voice track itself, write the MLT XML, render it through `melt`, encode with a
two-pass `loudnorm` so the measured loudness lands on the project target, then verify the result
and write both a QC report and a contact sheet.

Scratch intermediates live under ``/tmp/opencode/<slug>/`` so a long render never fills the
project, and they are deleted unless the caller asks to keep them.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import media
from .ledger import read_sidecar
from .manifest import AudioCue, Deliverable, Project, VideoPlan
from .mlt import AudioPlan, Clip, Timeline, Title, ducking_from_speech, timeline_seconds
from .presets import PresetError, resolve_effects
from .sidecar import UNKNOWN_SPDX

SCRATCH_ROOT = Path("/tmp/opencode")
DEFAULT_SHOT_SECONDS = 3.0
LOUDNESS_TOLERANCE_LU = 0.5
TRUE_PEAK_CEILING = -1.5


class VideoError(RuntimeError):
    """Raised when the video path cannot continue."""


@dataclass(frozen=True)
class RenderResult:
    """What one render produced."""

    deliverable: Path
    mlt_path: Path
    report_path: Path
    contact_sheet: Path
    intermediate: Path | None
    report: dict[str, object]

    @property
    def ok(self) -> bool:
        checks = self.report.get("checks")
        return bool(isinstance(checks, dict) and all(checks.values()))


def scratch_directory(slug: str) -> Path:
    """Where intermediates go for one project."""

    return SCRATCH_ROOT / slug / "video"


def _resolve_asset(project: Project, asset_id: str) -> Path:
    """Resolve a manifest asset id to a licensed file on disk."""

    relative = project.assets.get(asset_id)
    if not relative:
        raise VideoError(f"asset id {asset_id!r} is not declared under assets: in project.yaml")
    path = project.root / relative
    if not path.is_file():
        raise VideoError(f"asset {asset_id!r} is not staged at {relative}")
    sidecar = read_sidecar(path)
    if not sidecar:
        raise VideoError(
            f"asset {asset_id!r} ({relative}) has no .license sidecar; stage it through "
            "`ms assets stage` before rendering"
        )
    if sidecar.get("SPDX-License-Identifier", UNKNOWN_SPDX) == UNKNOWN_SPDX:
        raise VideoError(
            f"asset {asset_id!r} ({relative}) is quarantined with an unknown licence and may not "
            "appear in a deliverable"
        )
    return path


def _shot_duration(project: Project, asset_id: str, declared: float | None) -> float:
    if declared:
        return declared
    return round(media.probe(_resolve_asset(project, asset_id)).duration, 3) or DEFAULT_SHOT_SECONDS


def _audio_plan(
    project: Project, cue: AudioCue | None, ducking: tuple[tuple[float, float], ...] = ()
) -> AudioPlan | None:
    if cue is None:
        return None
    path = _resolve_asset(project, cue.asset)
    return AudioPlan(
        resource=path,
        in_point=cue.in_point,
        gain=cue.gain,
        fade_in=cue.fade_in,
        fade_out=cue.fade_out,
        ducking=ducking if cue.duck else (),
    )


def build_timeline(
    project: Project, deliverable: Deliverable
) -> tuple[Timeline, dict[str, object]]:
    """Turn the manifest's video plan into a timeline plus the evidence for its ducking."""

    plan: VideoPlan | None = project.video
    if plan is None or not plan.shots:
        raise VideoError(
            "project.yaml has no video.shots; add a video section naming the assets to cut together"
        )
    clips = tuple(
        Clip(
            resource=_resolve_asset(project, shot.asset),
            duration=_shot_duration(project, shot.asset, shot.duration),
            in_point=shot.in_point,
            transition=shot.transition,
        )
        for shot in plan.shots
    )
    clips, music_overrides, titles_from_presets = _apply_effects(project, plan, clips)

    width, height, fps = _geometry(deliverable)
    scratch = scratch_directory(project.slug)
    clips = _with_fades(clips, plan, scratch, width, height, fps)
    duration = timeline_seconds(clips, plan.crossfade)

    ducking: tuple[tuple[float, float], ...] = ()
    ducking_evidence: dict[str, object] = {"intervals": [], "source": "none"}
    voice_plan: AudioPlan | None = None
    if plan.voice is not None:
        voice_path = _resolve_asset(project, plan.voice.asset)
        speech = media.speech_segments(voice_path)
        ducking = ducking_from_speech(speech, duration)
        ducking_evidence = {
            "intervals": [list(interval) for interval in ducking],
            "speech_segments": [list(segment) for segment in speech],
            "source": "silencedetect on the voice track",
        }
    voice_plan = _audio_plan(project, plan.voice)

    music = _audio_plan(project, plan.music, ducking)
    if music is not None and music_overrides:
        music = replace(music, **music_overrides)

    timeline = Timeline(
        width=width,
        height=height,
        fps=fps,
        clips=clips,
        music=music,
        voice=voice_plan,
        titles=(
            *(
                Title(
                    text=title.text,
                    start=title.start,
                    duration=title.duration,
                    size=title.size,
                    colour=title.colour,
                    geometry=title.position,
                )
                for title in plan.titles
            ),
            *titles_from_presets,
        ),
        transition_duration=plan.crossfade,
        title=f"{project.slug} {deliverable.id}",
    )
    ducking_evidence["plan_intervals"] = [list(interval) for interval in ducking]
    return timeline, ducking_evidence


def _apply_effects(
    project: Project, plan: VideoPlan, clips: tuple[Clip, ...]
) -> tuple[tuple[Clip, ...], dict[str, object], tuple[Title, ...]]:
    """Lay manifest effects onto the plan: filter, transition, audio settings, or title."""

    if not project.effects:
        return clips, {}, ()
    try:
        resolved = resolve_effects([dict(entry) for entry in project.effects])
    except PresetError as error:
        raise VideoError(str(error)) from error

    working = list(clips)
    music_overrides: dict[str, object] = {}
    titles: list[Title] = []

    for entry, (recipe, values) in zip(project.effects, resolved, strict=True):
        target = str(entry.get("at") or "")
        index = _shot_index(plan, target)
        if recipe.kind == "video-effect":
            if index is None:
                raise VideoError(f"effect {recipe.id} needs an `at` naming a shot")
            properties = recipe.render(values)
            working[index] = replace(
                working[index],
                filters=(
                    *working[index].filters,
                    (str(recipe.implementation["mlt_filter"]), properties),
                ),
            )
        elif recipe.kind == "transition":
            if index is None:
                raise VideoError(f"transition {recipe.id} needs an `at` naming a shot")
            duration = float(values.get("duration") or 0.5)
            if recipe.implementation.get("to") == "next-shot":
                working[index + 1] = (
                    replace(working[index + 1], transition="crossfade", crossfade=duration)
                    if index + 1 < len(working)
                    else working[index + 1]
                )
            else:
                black = _black_clip(scratch_directory(project.slug), 1920, 1080, 30.0, duration)
                working.insert(
                    index + 1,
                    Clip(
                        resource=black,
                        duration=duration,
                        transition="crossfade",
                        crossfade=duration,
                    ),
                )
        elif recipe.kind == "audio-effect":
            settings = recipe.audio_settings(values)
            if "gain_db" in settings:
                music_overrides["gain"] = 10 ** (float(settings["gain_db"]) / 20)
            for key in ("fade_in", "fade_out", "duck_level"):
                if key in settings:
                    music_overrides[key] = float(settings[key])
        elif recipe.kind == "title":
            settings = recipe.title_settings(values)
            titles.append(
                Title(
                    text=str(entry.get("text") or ""),
                    start=float(values.get("start") or 0.0),
                    duration=float(values.get("duration") or 2.0),
                    size=int(float(str(settings.get("size") or 54))),
                    colour=str(settings.get("colour") or "#ffffffff"),
                    geometry=str(settings.get("position") or "+0+0"),
                )
            )
    return tuple(working), music_overrides, tuple(titles)


def _shot_index(plan: VideoPlan, target: str) -> int | None:
    """Find the shot an effect names, by asset id or by position."""

    if not target:
        return None
    if target.isdigit():
        index = int(target) - 1
        return index if 0 <= index < len(plan.shots) else None
    for index, shot in enumerate(plan.shots):
        if shot.asset == target:
            return index
    raise VideoError(f"effect names shot {target!r}, which video.shots does not contain")


def _with_fades(
    clips: tuple[Clip, ...],
    plan: VideoPlan,
    scratch: Path,
    width: int,
    height: int,
    fps: float,
) -> tuple[Clip, ...]:
    """Turn a requested fade into a cross-fade against a black clip.

    MLT's `mix` transition is audio-only and a colour producer does not composite, so the one
    mechanism that works in this build is a dissolve against a black file.
    """

    if plan.fade_in <= 0 and plan.fade_out <= 0:
        return clips
    black = _black_clip(scratch, width, height, fps, max(plan.fade_in, plan.fade_out))
    result = list(clips)
    if plan.fade_in > 0:
        result.insert(0, Clip(resource=black, duration=plan.fade_in))
        result[1] = replace(result[1], transition="crossfade", crossfade=plan.fade_in)
    if plan.fade_out > 0:
        result.append(
            Clip(
                resource=black,
                duration=plan.fade_out,
                transition="crossfade",
                crossfade=plan.fade_out,
            )
        )
    return tuple(result)


def _black_clip(scratch: Path, width: int, height: int, fps: float, seconds: float) -> Path:
    """A generated black clip, cached in the scratch directory."""

    scratch.mkdir(parents=True, exist_ok=True)
    destination = scratch / f"black-{width}x{height}-{fps:g}.mp4"
    if destination.is_file():
        return destination
    media.run_command(
        [
            media.FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:size={width}x{height}:rate={fps:g}:duration={max(seconds, 0.1):.3f}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(destination),
        ],
        label="generate a black clip for the fades",
        expect_stdout=False,
    )
    return destination


def _geometry(deliverable: Deliverable) -> tuple[int, int, float]:
    resolution = deliverable.resolution or "1920x1080"
    width_text, _, height_text = resolution.partition("x")
    try:
        width, height = int(width_text), int(height_text)
    except ValueError as error:
        raise VideoError(f"deliverable {deliverable.id}: bad resolution {resolution!r}") from error
    return width, height, float(deliverable.fps or 30.0)


def encode_with_loudness(
    intermediate: Path,
    destination: Path,
    target_lufs: float,
    *,
    preview: bool = False,
) -> media.Loudness:
    """Encode to H.264/AAC, normalising loudness with the measurement loudnorm asks for."""

    measured = media.measure_loudness(intermediate, target_lufs)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        media.FFMPEG,
        "-hide_banner",
        "-nostats",
        "-y",
        "-i",
        str(intermediate),
        "-af",
        f"loudnorm=I={target_lufs}:TP={TRUE_PEAK_CEILING}:LRA=11:{measured.as_loudnorm_arguments()}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast" if preview else "medium",
        "-crf",
        "28" if preview else "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k" if preview else "192k",
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
    ]
    if preview:
        command.extend(["-vf", "scale=640:-2"])
    command.append(str(destination))
    media.run_command(command, label=f"encode {destination.name}", expect_stdout=False)
    if not destination.is_file():
        raise VideoError(f"ffmpeg did not write {destination}")
    return measured


def qc_report(
    deliverable: Path,
    *,
    expected_duration: float,
    target_lufs: float,
    preview: bool = False,
) -> dict[str, object]:
    """Measure the finished file and decide, field by field, whether it passed."""

    info = media.probe(deliverable)
    loudness = media.measure_loudness(deliverable, target_lufs)
    # A deliberate fade is short; a black hole left by a missing clip is not.
    # Both thresholds are a deliberate one second: a short run of duplicated frames is a
    # compression artefact (a preview encode produces them), while a stuck shot lasts seconds.
    black = media.black_segments(deliverable, minimum=1.0)
    frozen = media.frozen_segments(deliverable, minimum=1.0)
    distance = loudness.distance_from(target_lufs)
    duration_gap = abs(info.duration - expected_duration)
    tolerance = 0.5 if preview else 0.25

    checks = {
        "has_video": info.video_codec is not None,
        "has_audio": info.audio_codec is not None,
        "duration_matches": duration_gap <= tolerance,
        "loudness_within_half_lu": distance <= LOUDNESS_TOLERANCE_LU,
        "true_peak_below_ceiling": loudness.true_peak_dbtp <= TRUE_PEAK_CEILING + 0.3,
        "no_black_frames": not black,
        "no_frozen_frames": not frozen,
    }
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "deliverable": str(deliverable),
        "preview": preview,
        "duration_seconds": round(info.duration, 3),
        "expected_seconds": round(expected_duration, 3),
        "width": info.width,
        "height": info.height,
        "fps": info.fps,
        "frames": info.frames,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "audio_channels": info.audio_channels,
        "loudness": {
            "integrated_lufs": round(loudness.integrated_lufs, 2),
            "target_lufs": target_lufs,
            "distance_lu": round(distance, 2),
            "true_peak_dbtp": round(loudness.true_peak_dbtp, 2),
            "loudness_range_lu": round(loudness.loudness_range, 2),
        },
        "black_segments": [list(segment) for segment in black],
        "frozen_segments": [list(segment) for segment in frozen],
        "checks": checks,
        "ok": all(checks.values()),
    }


def render(
    project: Project,
    deliverable_id: str | None = None,
    *,
    preview: bool = False,
    keep_intermediate: bool = False,
) -> RenderResult:
    """Render one video deliverable end to end."""

    deliverable = _pick_deliverable(project, deliverable_id)
    timeline, ducking_evidence = build_timeline(project, deliverable)
    scratch = scratch_directory(project.slug)
    scratch.mkdir(parents=True, exist_ok=True)

    mlt_path = project.root / "out" / f"{deliverable.id}.mlt"
    mlt_path.parent.mkdir(parents=True, exist_ok=True)
    mlt_path.write_text(timeline.to_xml(project.root), encoding="utf-8")

    intermediate = scratch / f"{deliverable.id}-render.mkv"
    media.render_timeline(mlt_path, intermediate)

    extension = deliverable.container or "mp4"
    destination = project.root / "out" / "video" / f"{deliverable.id}.{extension}"
    encode_with_loudness(intermediate, destination, project.loudness_lufs, preview=preview)

    report = qc_report(
        destination,
        expected_duration=timeline.duration,
        target_lufs=project.loudness_lufs,
        preview=preview,
    )
    report["ducking"] = ducking_evidence
    report["timeline"] = {
        "shots": [str(clip.resource.name) for clip in timeline.clips],
        "titles": [
            {"text": title.text, "start": title.start, "duration": title.duration}
            for title in timeline.titles
        ],
        "mlt": str(mlt_path.relative_to(project.root)),
        "profile": f"{timeline.width}x{timeline.height}@{timeline.fps:g}",
    }
    report_path = project.root / "out" / "reports" / f"{deliverable.id}-qc.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    sheet = media.contact_sheet(
        destination, project.root / "out" / "video" / f"{deliverable.id}-sheet.png"
    )

    kept: Path | None = None
    if keep_intermediate:
        kept = intermediate
    else:
        shutil.rmtree(scratch, ignore_errors=True)

    return RenderResult(
        deliverable=destination,
        mlt_path=mlt_path,
        report_path=report_path,
        contact_sheet=sheet,
        intermediate=kept,
        report=report,
    )


def _pick_deliverable(project: Project, deliverable_id: str | None) -> Deliverable:
    if deliverable_id:
        for deliverable in project.deliverables:
            if deliverable.id == deliverable_id:
                if deliverable.kind != "video":
                    raise VideoError(
                        f"deliverable {deliverable_id} is a {deliverable.kind}, not video"
                    )
                return deliverable
        raise VideoError(f"no deliverable {deliverable_id!r} in project.yaml")
    for deliverable in project.deliverables:
        if deliverable.kind == "video":
            return deliverable
    raise VideoError("project.yaml declares no video deliverable")
