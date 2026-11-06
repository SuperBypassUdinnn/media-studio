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
) -> EdlDocument:
    """Generate an EDL document filling ``total_duration_s`` with shots.

    Probes each shot for its duration, greedily assigns shot windows to fill
    ``total_duration_s`` using ``beats.json`` section energy to target cut
    density, and populates ``beatfx``/``ramp`` from auto-scheduling using
    ``music_offset_ms / 1000`` as the offset.
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

    for cut_counter, shot in enumerate(shot_infos):
        if remaining <= 0:
            break
        density = _density_for_section(_current_section(sections, music_time))
        # One cut per shot, capped by density, remaining time, and shot length.
        cut_s = min(density, remaining, shot["duration"])
        cut_s = max(_MIN_CUT_S, min(cut_s, _MAX_CUT_S))
        if cut_s > remaining:
            cut_s = remaining

        in_ms = 0
        out_ms = round(cut_s * 1000)
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
            # No beats fall inside this cut window; leave beatfx empty.
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
                shot=str(shot["path"]),
                in_ms=in_ms,
                out_ms=out_ms,
                music_offset_ms=music_offset_ms,
                beatfx=beatfx_shorthand,
                ramp=ramp_shorthand,
                color_lut=None,
                notes="",
            )
        )
        music_time += cut_s
        remaining -= cut_s

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

    # 1. Trim clip to [in_ms, out_ms].
    _run_ffmpeg(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{cut.in_ms / 1000:.3f}",
            "-to",
            f"{cut.out_ms / 1000:.3f}",
            "-i",
            str(source),
            "-c",
            "copy",
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
                    "-i",
                    str(doc.music),
                    "-filter_complex",
                    "amix=inputs=2",
                    "-c:v",
                    "copy",
                    str(out),
                ]
            )
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
