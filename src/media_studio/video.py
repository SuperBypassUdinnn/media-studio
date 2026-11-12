"""The video path: manifest to MLT to a measured deliverable.

Steps, in order: resolve and licence-check the assets, build the timeline, derive the ducking
windows from the voice track itself, write the MLT XML, render it through `melt`, encode with a
two-pass `loudnorm` so the measured loudness lands on the project target, then verify the result
and write both a QC report and a contact sheet.

Scratch intermediates live under ``/tmp/opencode/<slug>/`` so a long render never fills the
project, and they are deleted unless the caller asks to keep them.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from . import fonts, media
from .fcproj import compile_manifest_to_fcproj
from .filmcraft import ExportResult, FilmCraftError, FilmCraftRunner
from .ingest import IngestError, probe_media
from .ledger import read_sidecar
from .manifest import AudioCue, Deliverable, Project, VideoPlan, audio_span
from .mlt import AudioPlan, Clip, Timeline, Title, ducking_from_speech, timeline_seconds
from .parallax import ParallaxError, compose_parallax
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


@dataclass(frozen=True)
class FilmCraftRenderResult:
    """What a FilmCraft render produced."""

    deliverable: Path
    fcproj_path: Path
    report_path: Path
    contact_sheet: Path
    proxy_path: Path | None
    stills: list[Path]
    report: dict[str, object]
    export_result: ExportResult | None = None

    @property
    def ok(self) -> bool:
        checks = self.report.get("checks")
        return bool(isinstance(checks, dict) and all(checks.values()))


def scratch_directory(slug: str) -> Path:
    """Where intermediates go for one project."""

    return SCRATCH_ROOT / slug / "video"


def filler_directory(project: Project) -> Path:
    """Where generated filler goes: inside the project, unlike the scratch intermediates.

    The fades are cross-fades against a generated black clip, and a handed-off timeline references
    that file by path. Scratch under `/tmp/opencode` is purged after an encode, so a document that
    pointed there would break the first time someone reopened it.
    """

    return project.root / "out" / "frames"


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


def _source_seconds(path: Path) -> float | None:
    """Best-effort source length; an unprobeable file leaves the cue unclamped by the source."""

    try:
        return media.probe(path).duration
    except media.MediaError:
        return None


def _audio_plan(
    project: Project,
    cue: AudioCue | None,
    video_seconds: float,
    ducking: tuple[tuple[float, float], ...] = (),
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
        duration=audio_span(cue, video_seconds, _source_seconds(path)),
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
    clips: list[Clip] = []
    for shot in plan.shots:
        duration = _shot_duration(project, shot.asset, shot.duration)
        if shot.shot_type == "parallax":
            # A parallax shot is pre-composited into a scratch clip before the
            # timeline is assembled; the declared asset is the subject cutout.
            source = _resolve_asset(project, shot.asset)
            scratch = scratch_directory(project.slug)
            scratch.mkdir(parents=True, exist_ok=True)
            composed = scratch / f"parallax-{Path(source).stem}.mov"
            try:
                compose_parallax(
                    background=source,
                    subject=source,
                    duration=duration,
                    destination=composed,
                    fps=60,
                    motion="push_in",
                )
            except ParallaxError as error:
                raise VideoError(str(error)) from error
            resource = composed
        else:
            resource = _resolve_asset(project, shot.asset)
        clips.append(
            Clip(
                resource=resource,
                duration=duration,
                in_point=shot.in_point,
                transition=shot.transition,
            )
        )
    clips = tuple(clips)
    clips, music_overrides, titles_from_presets = _apply_effects(project, plan, clips)

    width, height, fps = _geometry(deliverable)
    clips = _with_fades(clips, plan, filler_directory(project), width, height, fps)
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
    voice_plan = _audio_plan(project, plan.voice, duration)

    music = _audio_plan(project, plan.music, duration, ducking)
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
                black = _black_clip(filler_directory(project), 1920, 1080, 30.0, duration)
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
    """A generated black clip, cached where the caller asks for it."""

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
        "28" if preview else "18",
        "-profile:v",
        "high" if not preview else "main",
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


@dataclass(frozen=True)
class SceneCut:
    """One hard cut: the first frame of the new scene and its timestamp."""

    frame: int
    time: float


def detect_scene_cuts(source: Path, *, threshold: float = 0.3) -> list[SceneCut]:
    """Find hard cuts in ``source`` with ffmpeg's scene filter.

    Every frame the ``select`` filter passes is the first frame of a new
    scene; ``showinfo`` prints its index and timestamp, which is all this
    function parses. Use it to pick one ``--frame`` anchor per scene before
    running SAM 2 background removal — a single prompt cannot survive a cut.

    Raises:
        VideoError: the file is missing or ffmpeg failed.
    """

    if not Path(source).is_file():
        raise VideoError(f"no such file: {source}")

    completed = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-i",
            str(source),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise VideoError(f"ffmpeg failed reading {source}:\n{completed.stderr.strip()[-2000:]}")

    try:
        info = probe_media(Path(source))
    except IngestError as error:
        raise VideoError(str(error)) from error

    cuts: list[SceneCut] = []
    for line in completed.stderr.splitlines():
        if "showinfo" not in line:
            continue
        time_match = re.search(r"pts_time:([\d.]+)", line)
        if time_match is None:
            continue
        # showinfo's n: counts only the frames the select filter passed, so
        # the input frame index comes from the timestamp instead.
        time_s = float(time_match.group(1))
        cuts.append(SceneCut(round(time_s * info.fps), time_s))
    return cuts


def scene_ranges(cuts: list[SceneCut], total_frames: int) -> list[tuple[int, int, float, float]]:
    """Turn cut frames into inclusive ``(start, end, start_s, end_s)`` ranges.

    A cut frame belongs to the scene that begins there, so a scene spans
    from its first frame to the frame before the next cut.
    """

    starts = [0] + [cut.frame for cut in cuts]
    times = [0.0] + [cut.time for cut in cuts]
    ranges: list[tuple[int, int, float, float]] = []
    for index, start in enumerate(starts):
        end = (starts[index + 1] - 1) if index + 1 < len(starts) else total_frames - 1
        next_time = times[index + 1] if index + 1 < len(times) else -1.0
        ranges.append((start, end, times[index], next_time))
    return ranges


def qc_report(
    deliverable: Path,
    *,
    expected_duration: float | None,
    target_lufs: float,
    preview: bool = False,
    engine: str = "unknown",
    engine_version: str | None = None,
    render_path: str = "unknown",
    gpu_fallback_to_cpu: bool = False,
    gpu_failure_text: str | None = None,
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
    # When the manifest declares no duration there is nothing to match against;
    # the measured duration is reported but the check cannot fail.
    duration_declared = expected_duration is not None
    duration_gap = abs(info.duration - (expected_duration or 0.0))
    tolerance = 0.5 if preview else 0.25

    checks = {
        "has_video": info.video_codec is not None,
        "has_audio": info.audio_codec is not None,
        "duration_matches": (duration_gap <= tolerance) if duration_declared else True,
        "loudness_within_half_lu": distance <= LOUDNESS_TOLERANCE_LU,
        "true_peak_below_ceiling": loudness.true_peak_dbtp <= TRUE_PEAK_CEILING + 0.3,
        "no_black_frames": not black,
        "no_frozen_frames": not frozen,
    }
    return {
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "deliverable": str(deliverable),
        "preview": preview,
        "engine": engine,
        "engine_version": engine_version,
        "render_path": render_path,
        "gpu_fallback_to_cpu": gpu_fallback_to_cpu,
        "gpu_failure_text": gpu_failure_text,
        "duration_seconds": round(info.duration, 3),
        "expected_seconds": round(expected_duration, 3) if duration_declared else None,
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
        engine="mlt (legacy)",
        render_path="cpu",
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


def render_filmcraft(
    project: Project,
    deliverable_id: str | None = None,
    *,
    fcproj_path: Path | None = None,
    gpu: str = "auto",
    quality: int = 70,
    runner: FilmCraftRunner | None = None,
    generate_review: bool = True,
    preview: bool = False,
    burn_captions: bool = False,
    force: bool = False,
) -> FilmCraftRenderResult:
    if isinstance(deliverable_id, Deliverable):
        deliverable = deliverable_id
    else:
        deliverable = _pick_deliverable(project, deliverable_id)
    if runner is None:
        runner = FilmCraftRunner()

    if fcproj_path is None:
        if burn_captions:
            # Transcript and caption tracks live in the working timeline
            # (<slug>.fcproj); a freshly compiled render input has neither, so
            # burning captions requires exporting from the working timeline.
            working = project.root / f"{project.slug}.fcproj"
            if not working.is_file():
                raise FilmCraftError(
                    f"burn-captions requires the working timeline {working.name}; "
                    "run `ms video build` and `ms video captions` first"
                )
            fcproj_path = working
        else:
            fcproj_path = project.root / "out" / f"{deliverable.id}.fcproj"
            fcproj_path.parent.mkdir(parents=True, exist_ok=True)
            compile_manifest_to_fcproj(
                project,
                output_fcproj=fcproj_path,
                deliverable_id=deliverable.id,
                runner=runner,
                force=force,
            )

    extension = deliverable.container or "mp4"
    destination = project.root / "out" / "video" / f"{deliverable.id}.{extension}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Stage 4.2: export to scratch first, then move into out/video/ so a failed
    # or partial encode never leaves a half-written deliverable at its final path.
    scratch = project.root / "out" / "scratch" / f"{deliverable.id}.{extension}"
    scratch.parent.mkdir(parents=True, exist_ok=True)

    target_lufs = (
        deliverable.loudness_lufs
        if deliverable.loudness_lufs is not None
        else project.loudness_lufs
    )

    fmt = extension if extension in ("h264", "hevc", "prores", "dnxhr") else "h264"
    export_result = runner.export(
        project_path=fcproj_path,
        output_file=scratch,
        format=fmt,
        quality=quality,
        loudness_lufs=target_lufs,
        gpu=gpu,
        burn_captions=burn_captions,
    )
    if scratch.exists():
        scratch.replace(destination)

    expected_duration = deliverable.duration

    report = qc_report(
        destination,
        expected_duration=expected_duration,
        target_lufs=target_lufs,
        engine="filmcraft 0.15.0",
        engine_version=runner.engine_version(),
        render_path=export_result.render_path,
        gpu_fallback_to_cpu=export_result.gpu_fallback_to_cpu,
        gpu_failure_text=export_result.details.get("gpu_failure_text"),
        preview=preview,
    )

    is_rel = False
    try:
        rel_fc = str(fcproj_path.relative_to(project.root))
        is_rel = True
    except ValueError:
        rel_fc = str(fcproj_path)

    report["fcproj"] = rel_fc if is_rel else str(fcproj_path)
    report["export"] = {
        "returncode": export_result.returncode,
        "gpu_fallback_to_cpu": export_result.gpu_fallback_to_cpu,
        "render_path": export_result.render_path,
    }

    report_path = project.root / "out" / "reports" / f"{deliverable.id}-qc.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    contact_sheet_path: Path | None = None
    proxy_path: Path | None = None
    stills: list[Path] = []
    if generate_review and destination.exists():
        sheet_dest = project.root / "out" / "video" / f"{deliverable.id}-sheet.png"
        contact_sheet_path = media.contact_sheet(destination, sheet_dest)
        proxy_dest = project.root / "out" / "video" / f"{deliverable.id}-proxy540p.mp4"
        proxy_path = media.generate_proxy(destination, proxy_dest, height=540)
        stills_dir = project.root / "out" / "video" / "stills"
        stills = media.extract_stills(destination, stills_dir)

    return FilmCraftRenderResult(
        deliverable=destination,
        fcproj_path=fcproj_path,
        report_path=report_path,
        contact_sheet=contact_sheet_path,
        proxy_path=proxy_path,
        stills=stills,
        report=report,
        export_result=export_result,
    )


def installed_kdenlive_version() -> str:
    """The Kdenlive version this machine has, or an empty string when it is not installed."""

    try:
        completed = subprocess.run(
            ["kdenlive", "--version"], capture_output=True, text=True, check=False, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", f"{completed.stdout} {completed.stderr}")
    return match.group(1) if match else ""


def _document_id(project: Project, deliverable: Deliverable) -> str:
    """A stable identifier for one handoff document, so the file is reproducible."""

    digest = hashlib.sha256(f"{project.slug}/{deliverable.id}".encode()).hexdigest()
    return f"media-studio-{digest[:12]}"


@dataclass(frozen=True)
class HandoffResult:
    """A Kdenlive-compatible project built from the same timeline the render uses."""

    path: Path
    kdenlive_version: str
    document_id: str
    # Paths the written document names that are not on disk. The shot sources are validated by
    # build_timeline, so what this really catches is the generated filler, which is produced here.
    missing_references: tuple[Path, ...]
    timeline: Timeline

    @property
    def problems(self) -> list[str]:
        if not self.missing_references:
            return []
        return [
            "the document references files that are not there: "
            + ", ".join(item.name for item in self.missing_references)
        ]

    @property
    def notes(self) -> list[str]:
        """What the handoff changed for Kdenlive's sake, named here instead of discovered there."""

        return [
            f"title font {title.family!r} is not installed; the document names {resolved!r}"
            for title in self.timeline.titles
            if (resolved := fonts.resolved_family(title.family)) and resolved != title.family
        ]


def handoff(project: Project, deliverable_id: str | None = None) -> HandoffResult:
    """Write a Kdenlive-compatible project beside the render input, without touching it.

    [LEGACY in v2]: Maintained for backward compatibility. Use FilmCraft (`ms video open`)
    for modern timeline editing.
    """
    import sys

    print(
        "DEPRECATION WARNING: Kdenlive handoff is legacy in media-studio v2. "
        "FilmCraft (`ms video open`) is the recommended editor.",
        file=sys.stderr,
    )
    deliverable = _pick_deliverable(project, deliverable_id)
    timeline, _evidence = build_timeline(project, deliverable)
    directory = project.root / "out" / "kdenlive"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{deliverable.id}.kdenlive"
    # Probed once: the report must name the version the document itself was stamped with.
    version = installed_kdenlive_version()
    document_id = _document_id(project, deliverable)
    path.write_text(
        timeline.to_kdenlive_xml(
            project.root,
            kdenlive_version=version,
            document_id=document_id,
        ),
        encoding="utf-8",
    )
    references = tuple(
        sorted({clip.resource for clip in timeline.clips if not clip.resource.is_file()})
    )
    return HandoffResult(
        path=path,
        kdenlive_version=version,
        document_id=document_id,
        missing_references=references,
        timeline=timeline,
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
