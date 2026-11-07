"""EDL cut-plan tool: generate, render, and preview edit decision lists.

Consumes ``beatfx.auto_schedule``, ``beatramp.schedule_ramp_events``,
``ingest.probe_media`` and ``audio.analyze_rhythm``. LUT application
(``lut.apply_lut`` from Task 5) is imported lazily so Task 4's test suite
passes independently of that module.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .beatfx import (
    BeatFxError,
    ScheduledEffect,
    apply_effects,
    auto_schedule,
)
from .beatramp import (
    BeatRampError,
    RampEvent,
    apply_ramp_events,
    schedule_ramp_events,
)
from .ingest import IngestError, probe_media
from .scene import SceneError, scene_segments, sustained_scene_changes

EDL_VERSION = 1

#: Density band (seconds per cut) per music section level.
_DENSITY_BY_LEVEL: dict[str, float] = {
    "high": 0.5,
    "build": 1.0,
    "low": 1.5,
}

#: Default density (seconds per cut) when sections are empty or beats missing.
_DEFAULT_DENSITY = 1.5

#: Allowed cut duration range in seconds.
_MIN_CUT_S = 0.2
_MAX_CUT_S = 2.0

#: Staging root for rendered cut intermediates.
_STAGING_ROOT = Path("/tmp/opencode")


class EdlError(RuntimeError):
    """Raised when an EDL cannot be loaded, generated, or rendered."""


@dataclass(frozen=True)
class EdlCut:
    """One cut in an EDL document."""

    id: str
    shot: str
    in_ms: int
    out_ms: int
    music_offset_ms: int
    beatfx: list[str]
    ramp: list[str]
    color_lut: str | None
    notes: str


@dataclass(frozen=True)
class EdlDocument:
    """An edit decision list document."""

    version: int
    music: str
    beats_json: str
    total_duration_s: float
    cuts: list[EdlCut]


def load_edl(path: Path) -> EdlDocument:
    """Load an EDL document from ``path``.

    Raises:
        EdlError: the file is missing or contains invalid JSON.
    """

    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EdlError(f"invalid EDL: {error}") from error

    try:
        cuts = [
            EdlCut(
                id=entry["id"],
                shot=entry["shot"],
                in_ms=int(entry["in_ms"]),
                out_ms=int(entry["out_ms"]),
                music_offset_ms=int(entry["music_offset_ms"]),
                beatfx=list(entry.get("beatfx") or []),
                ramp=list(entry.get("ramp") or []),
                color_lut=entry.get("color_lut"),
                notes=entry.get("notes", ""),
            )
            for entry in payload.get("cuts", [])
        ]
    except (KeyError, TypeError, ValueError) as error:
        raise EdlError(f"invalid EDL: {error}") from error

    return EdlDocument(
        version=int(payload.get("version", EDL_VERSION)),
        music=str(payload.get("music", "")),
        beats_json=str(payload.get("beats_json", "")),
        total_duration_s=float(payload.get("total_duration_s", 0.0)),
        cuts=cuts,
    )


def save_edl(doc: EdlDocument, path: Path) -> None:
    """Serialize ``doc`` to JSON at ``path``."""

    path = Path(path)
    payload = asdict(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class SyncReport:
    """Authoritative cut-to-beat alignment report for an EDL document."""

    cuts: int
    onsets: int
    tolerance: float
    share_on_onset: float
    baseline: float
    median_distance: float
    p90_distance: float
    verdict: str  # "synced" | "unsynced" | "inconclusive"


def _median(values: list[float]) -> float:
    """Return the median of a non-empty list of numbers."""

    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _p90(values: list[float]) -> float:
    """Return the 90th percentile value of a non-empty list of numbers."""

    ordered = sorted(values)
    index = min(int(len(ordered) * 0.9), len(ordered) - 1)
    return ordered[index]


def check_sync(doc: EdlDocument, beats: dict, *, tolerance: float = 0.1) -> SyncReport:
    """Check EDL cut points against the beat grid.

    Returns a :class:`SyncReport` comparing each cut's music offset to the
    nearest beat onset. Raises :class:`EdlError` when the EDL has no cuts.
    """

    if not doc.cuts:
        raise EdlError("no cuts in the EDL")

    points = sorted(cut.music_offset_ms / 1000 for cut in doc.cuts)
    onsets = sorted(float(b) for b in beats.get("beats") or [])

    if not onsets:
        # No beat grid to align against: the check is inconclusive and no
        # distance loop runs over an empty onset list.
        return SyncReport(
            cuts=len(points),
            onsets=0,
            tolerance=float(tolerance),
            share_on_onset=0.0,
            baseline=0.0,
            median_distance=0.0,
            p90_distance=0.0,
            verdict="inconclusive",
        )

    distances = [min(abs(point - onset) for onset in onsets) for point in points]

    on_onset = sum(1 for distance in distances if distance <= tolerance)
    share_on_onset = on_onset / len(points)

    duration = float(beats.get("duration") or doc.total_duration_s or 0.0)
    if duration == 0.0:
        # Only defensive branch: avoid a divide-by-zero with no duration info.
        duration = 1.0
    baseline = min(1.0, len(onsets) * 2 * tolerance / duration)

    if len(points) < 3 or len(onsets) < 3:
        verdict = "inconclusive"
    elif share_on_onset > baseline and share_on_onset >= 0.5:
        verdict = "synced"
    elif share_on_onset <= baseline:
        verdict = "unsynced"
    else:
        verdict = "inconclusive"

    return SyncReport(
        cuts=len(points),
        onsets=len(onsets),
        tolerance=float(tolerance),
        share_on_onset=share_on_onset,
        baseline=baseline,
        median_distance=round(_median(distances), 4),
        p90_distance=round(_p90(distances), 4),
        verdict=verdict,
    )


@dataclass(frozen=True)
class CutIntegrityFinding:
    """One cut whose shot-local window spans a sustained scene boundary."""

    cut_id: str
    shot: str
    timeline_start_s: float
    window: str
    boundary_s: float


@dataclass(frozen=True)
class CutIntegrityReport:
    """check_cuts result: which cuts span sustained scene boundaries."""

    cuts: int
    spanning: int
    threshold: float
    verdict: str  # "clean" | "spanning"
    findings: list[CutIntegrityFinding]


def check_cuts(
    doc: EdlDocument,
    *,
    threshold: float = 0.3,
    margin_s: float = 0.02,
    detector: Callable[..., list[float]] | None = None,
) -> CutIntegrityReport:
    """Check each EDL cut's shot-local window for sustained scene boundaries.

    Detects boundaries once per unique shot (cached), then flags any cut whose
    window [in_s, out_s] contains a sustained boundary strictly inside the
    margin-expanded window. Returns a :class:`CutIntegrityReport`.
    Raises EdlError when the EDL has no cuts or the detector fails.
    """

    if not doc.cuts:
        raise EdlError("no cuts in the EDL")

    if detector is None:
        detector = sustained_scene_changes

    boundaries_by_shot: dict[str, list[float]] = {}
    findings: list[CutIntegrityFinding] = []
    timeline_s = 0.0

    for cut in doc.cuts:
        shot = cut.shot
        if shot not in boundaries_by_shot:
            try:
                boundaries_by_shot[shot] = sorted(detector(Path(shot), threshold=threshold))
            except SceneError as error:
                raise EdlError(str(error)) from error

        in_s = cut.in_ms / 1000
        out_s = cut.out_ms / 1000
        inner = [b for b in boundaries_by_shot[shot] if in_s + margin_s < b < out_s - margin_s]
        if inner:
            findings.append(
                CutIntegrityFinding(
                    cut_id=cut.id,
                    shot=shot,
                    timeline_start_s=round(timeline_s, 3),
                    window=f"{in_s:.3f}-{out_s:.3f}",
                    boundary_s=round(inner[0], 3),
                )
            )
        timeline_s += out_s - in_s

    verdict = "clean" if not findings else "spanning"
    return CutIntegrityReport(
        cuts=len(doc.cuts),
        spanning=len(findings),
        threshold=float(threshold),
        verdict=verdict,
        findings=findings,
    )


def _slug(text: str) -> str:
    """Return a filesystem-safe slug for ``text``."""

    return "".join(ch if ch.isalnum() else "-" for ch in text).strip("-") or "edl"


def _density_for_section(section: dict[str, Any] | None) -> float:
    """Return seconds-per-cut density for a music section."""

    if section is None:
        return _DEFAULT_DENSITY
    level = str(section.get("level", "low"))
    return _DENSITY_BY_LEVEL.get(level, _DEFAULT_DENSITY)


def _current_section(sections: list[dict[str, Any]], time_s: float) -> dict[str, Any] | None:
    """Return the music section containing ``time_s``."""

    for section in sections:
        if section.get("start", 0.0) <= time_s < section.get("end", float("inf")):
            return section
    return None


def generate_edl(
    shots: list[Path],
    beats_json: Path,
    music: Path,
    *,
    total_duration_s: float,
    out: Path,
    scene_aware: bool = False,
    scene_threshold: float = 0.3,
    detector: Callable[..., list[float]] | None = None,
) -> EdlDocument:
    """Generate an EDL document filling ``total_duration_s`` with shots.

    Probes each shot for its duration, greedily assigns shot windows to fill
    ``total_duration_s`` using ``beats.json`` section energy to target cut
    density, and populates ``beatfx``/``ramp`` from auto-scheduling using
    ``music_offset_ms / 1000`` as the offset.

    With ``scene_aware=True`` (opt-in), sustained scene boundaries are detected
    per shot and every emitted cut lies entirely inside one scene segment, so
    a multi-scene shot can yield more than one cut. A segment tail shorter than
    ``_MIN_CUT_S`` is skipped. This is intentional ("split the multi-scene cut
    so each part lands on its own beat"). When ``scene_aware=False`` (default)
    behavior is unchanged byte-for-byte and the detector is never called.
    """

    shots = [Path(s) for s in shots]
    beats_json = Path(beats_json)
    music = Path(music)
    out = Path(out)

    try:
        beats_payload = json.loads(beats_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise EdlError(f"invalid beats JSON: {error}") from error

    sections = beats_payload.get("sections") or []

    shot_infos: list[dict[str, Any]] = []
    for shot in shots:
        try:
            info = probe_media(shot)
        except IngestError as error:
            raise EdlError(str(error)) from error
        if info.width < 1920 or info.height < 1080:
            raise EdlError("below the 1920x1080 minimum floor")
        shot_infos.append(
            {
                "path": shot,
                "duration": float(info.duration),
                "width": info.width,
                "height": info.height,
            }
        )

    cuts: list[EdlCut] = []
    remaining = float(total_duration_s)
    music_time = 0.0
    cut_counter = 0

    def emit_cut(shot: Path, in_s: float, out_s: float) -> None:
        nonlocal remaining, music_time, cut_counter
        cut_s = out_s - in_s
        music_offset_ms = round(music_time * 1000)
        offset_s = music_time
        beatfx_shorthand = []
        try:
            effects = auto_schedule(
                beats_payload,
                offset=offset_s,
                duration=cut_s,
                max_per_second=2,
            )
            beatfx_shorthand = _schedule_shorthand(effects, cut_s)
        except BeatFxError:
            pass
        ramp_shorthand = []
        try:
            events = schedule_ramp_events(
                beats_payload,
                offset=offset_s,
                duration=cut_s,
            )
            ramp_shorthand = _ramp_shorthand(events, cut_s)
        except BeatRampError:
            pass

        cuts.append(
            EdlCut(
                id=f"cut-{cut_counter}",
                shot=str(shot),
                in_ms=round(in_s * 1000),
                out_ms=round(out_s * 1000),
                music_offset_ms=music_offset_ms,
                beatfx=beatfx_shorthand,
                ramp=ramp_shorthand,
                color_lut=None,
                notes="",
            )
        )
        cut_counter += 1
        music_time += cut_s
        remaining -= cut_s

    if scene_aware:
        detector = detector or sustained_scene_changes
        boundaries_by_shot: dict[str, list[float]] = {}
        for shot in shot_infos:
            try:
                boundaries_by_shot[str(shot["path"])] = sorted(
                    detector(Path(shot["path"]), threshold=scene_threshold)
                )
            except SceneError as error:
                raise EdlError(str(error)) from error
        for shot in shot_infos:
            if remaining <= 0:
                break
            for seg_start, seg_end in scene_segments(
                shot["duration"], boundaries_by_shot[str(shot["path"])]
            ):
                cursor = seg_start
                while remaining > 0 and (seg_end - cursor) >= _MIN_CUT_S:
                    density = _density_for_section(_current_section(sections, music_time))
                    cut_s = min(density, remaining, seg_end - cursor)
                    cut_s = max(_MIN_CUT_S, min(cut_s, _MAX_CUT_S))
                    if cut_s > (seg_end - cursor):
                        cut_s = seg_end - cursor
                    if cut_s > remaining:
                        cut_s = remaining
                    emit_cut(shot["path"], cursor, cursor + cut_s)
                    cursor += cut_s
    else:
        for shot in shot_infos:
            if remaining <= 0:
                break
            density = _density_for_section(_current_section(sections, music_time))
            # One cut per shot, capped by density, remaining time, and shot length.
            cut_s = min(density, remaining, shot["duration"])
            cut_s = max(_MIN_CUT_S, min(cut_s, _MAX_CUT_S))
            if cut_s > remaining:
                cut_s = remaining
            emit_cut(shot["path"], 0.0, cut_s)

    doc = EdlDocument(
        version=EDL_VERSION,
        music=str(music),
        beats_json=str(beats_json),
        total_duration_s=float(total_duration_s),
        cuts=cuts,
    )
    save_edl(doc, out)
    return doc


def _schedule_shorthand(effects: list[ScheduledEffect], cut_s: float) -> list[str]:
    """Convert scheduled effects into ``kind@time_s`` shorthand strings."""

    return [f"{effect.kind}@{effect.time:.3f}" for effect in effects]


def _ramp_shorthand(events: list[RampEvent], cut_s: float) -> list[str]:
    """Convert ramp events into ``kind@time_s`` shorthand strings."""

    return [f"{event.kind}@{event.time:.3f}" for event in events]


def _parse_shorthand(shorthand: str) -> tuple[str, float]:
    """Parse ``kind@time_s`` into ``(kind, time_s)``."""

    kind, _, time_text = shorthand.rpartition("@")
    if not kind or not time_text:
        raise EdlError(f"invalid shorthand {shorthand!r}; expected kind@time_s")
    try:
        time_s = float(time_text)
    except ValueError as error:
        raise EdlError(f"invalid shorthand {shorthand!r}: {error}") from error
    return kind, time_s


def _render_cut(
    cut: EdlCut,
    staging: Path,
    index: int,
    *,
    fps: float,
    codec: str,
) -> Path:
    """Render one cut through the trim -> beatfx -> ramp -> lut pipeline."""

    source = Path(cut.shot)
    if not source.is_file():
        raise EdlError(f"no such file: {source}")

    if cut.in_ms >= cut.out_ms:
        raise EdlError("in_ms must be less than out_ms")

    duration_s = (cut.out_ms - cut.in_ms) / 1000
    if duration_s <= 0:
        raise EdlError("in_ms must be less than out_ms")

    cut_path = staging / f"cut-{index}.mp4"
    cut_path.parent.mkdir(parents=True, exist_ok=True)

    # Resolution floor check against the probed source.
    try:
        info = probe_media(source)
    except IngestError as error:
        raise EdlError(str(error)) from error
    if info.width < 1920 or info.height < 1080:
        raise EdlError("below the 1920x1080 minimum floor")

    # 1. Trim clip to [in_ms, out_ms]. Re-encode instead of `-c copy`: stream copy
    #    snaps both boundaries to keyframes, so on long-GOP sources (a re-encoded
    #    mine pool carries a keyframe only at frame 0) the trimmed duration drifts
    #    from the requested window and downstream ramp windows can fall outside
    #    the clip. `-t` is pulled back by half a frame: a plain `-t` includes the
    #    frame landing exactly on the boundary, so every cut gained up to one
    #    surplus frame and the error compounded across a beat-locked edit into
    #    seconds of A/V drift, while `-frames:v` bounds the count correctly but
    #    leaves the concatenated file with sparse PTS gaps. The half-frame epsilon
    #    yields exactly the intended frame count and a clean constant-rate timeline.
    trim_end_s = max(1.0 / fps, duration_s - 0.5 / fps)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{cut.in_ms / 1000:.3f}",
            "-i",
            str(source),
            "-t",
            f"{trim_end_s:.6f}",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "14",
            "-preset",
            "fast",
            "-pix_fmt",
            "yuv420p",
            "-r",
            f"{fps:g}",
            str(cut_path),
        ]
    )

    current = cut_path

    # 2. Apply beatfx effects.
    if cut.beatfx:
        effects = []
        for shorthand in cut.beatfx:
            kind, time_s = _parse_shorthand(shorthand)
            effects.append(ScheduledEffect(time=time_s, kind=kind, params={}))
        try:
            result = apply_effects(
                current,
                destination=current,
                effects=effects,
                fps=fps,
                resolution="1920x1080",
                codec=codec,
            )
        except (BeatFxError, IngestError) as error:
            raise EdlError(str(error)) from error
        current = result.destination

    # 3. Apply beatramp events.
    if cut.ramp:
        events = []
        for shorthand in cut.ramp:
            kind, time_s = _parse_shorthand(shorthand)
            events.append(RampEvent(time=time_s, kind=kind, trigger="beat"))
        try:
            result = apply_ramp_events(
                current,
                events,
                destination=current,
                fps=fps,
                resolution="1920x1080",
                codec=codec,
            )
        except (BeatRampError, IngestError) as error:
            raise EdlError(str(error)) from error
        current = result.destination

    # 4. Apply LUT if color_lut is set (lazy import; skip if unavailable).
    if cut.color_lut:
        try:
            from . import lut  # type: ignore[import-not-found]
        except ImportError:
            import sys

            print(
                f"warning: lut module not available; skipping color_lut {cut.color_lut!r}",
                file=sys.stderr,
            )
        else:
            try:
                lut.apply_lut(current, current, cut.color_lut)
            except Exception as error:
                print(f"warning: color_lut {cut.color_lut!r} skipped: {error}", file=sys.stderr)

    return current


def _run_ffmpeg(argv: list[str]) -> None:
    """Run an ffmpeg command, raising EdlError on failure."""

    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise EdlError("ffmpeg is not installed") from error
    if result.returncode != 0:
        raise EdlError(f"ffmpeg failed ({result.returncode}):\n{result.stderr.strip()[-2000:]}")


def _stream_has_audio(video: Path) -> bool:
    """Return True when *video* carries at least one audio stream."""

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(video),
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise EdlError("ffprobe is not installed") from error
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def render_edl(
    doc: EdlDocument,
    *,
    out: Path,
    fps: float = 60.0,
    codec: str = "h264",
) -> Path:
    """Render every cut in ``doc`` and concat+mix to ``out``."""

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    staging = _STAGING_ROOT / f"edl-{_slug(out.stem)}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=True)

    try:
        rendered: list[Path] = []
        for index, cut in enumerate(doc.cuts):
            rendered.append(_render_cut(cut, staging, index, fps=fps, codec=codec))

        # Concat list file.
        concat_list = staging / "list.txt"
        lines = []
        for cut_path in rendered:
            lines.append(f"file '{cut_path}'")
        concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

        if doc.music:
            # Mix music with amix/adelay at each cut's music_offset_ms.
            # Start the music at the first cut's music offset so it lines up
            # with the edit timeline; -shortest caps output to the video length.
            first_offset_s = doc.cuts[0].music_offset_ms / 1000 if doc.cuts else 0.0
            # Cuts rendered from audio-less sources produce a video-only concat
            # stream; amix would fail with an unbound pad. Probe and map
            # accordingly: amix only when the concat has its own audio.
            concat_has_audio = _stream_has_audio(rendered[0]) if rendered else False
            cmd = [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-ss",
                f"{first_offset_s:.3f}",
                "-i",
                str(doc.music),
            ]
            if concat_has_audio:
                cmd += [
                    "-filter_complex",
                    "[0:a][1:a]amix=inputs=2[aout]",
                    "-map",
                    "0:v",
                    "-map",
                    "[aout]",
                ]
            else:
                cmd += ["-map", "0:v", "-map", "1:a"]
            cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-shortest", str(out)]
            _run_ffmpeg(cmd)
        else:
            _run_ffmpeg(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_list),
                    "-c",
                    "copy",
                    str(out),
                ]
            )

        if not out.is_file():
            raise EdlError(f"ffmpeg reported success but wrote no file: {out}")
        return out
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def preview_edl_cut(
    doc: EdlDocument,
    cut_id: str,
    *,
    out: Path,
    fps: float = 60.0,
) -> Path:
    """Render only the cut with ``cut_id`` to ``out``."""

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    cut = next((c for c in doc.cuts if c.id == cut_id), None)
    if cut is None:
        raise EdlError(f"no cut with id {cut_id!r}")

    staging = _STAGING_ROOT / f"edl-{_slug(out.stem)}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=True)

    try:
        rendered = _render_cut(cut, staging, 0, fps=fps, codec="h264")
        shutil.copy2(rendered, out)
        return out
    finally:
        shutil.rmtree(staging, ignore_errors=True)
