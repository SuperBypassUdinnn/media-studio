"""Beat-mapped micro-effects engine.

Lineage: the beat-synced-edit cookbook (flash/shake/punch on percussive hits),
extended with the chromatic RGB-split recipe from ``impact.py``. Each effect is
rendered as one sequential ffmpeg pass over the clip so the windows stack:
pass N reads pass N-1's output and re-applies every other effect's filter, so
effects compose deterministically and a near-end window still runs to the end of
the clip (its enable gate simply never opens past the last frame).

Pinned behaviour:

* Resolution: ``WxH`` parsed with the same ``re`` pattern as ``parallax.py``,
  floored at 1920x1080 (message contains "below the 1920x1080 minimum floor").
* Every effect gets a window ``[start, start + duration)`` expressed as
  ``enable='between(t,start,end)'``; defaults per kind:
  shake 0.25 s (amp_px 12, freq 24), punch 0.3 s (zoom 1.08),
  flash 2 frames (``2/fps`` s), rgb 0.2 s (shift_px 6), hue 0.4 s (degrees 20).
* ``apply_effects`` runs one ffmpeg pass per effect under
  ``/tmp/opencode/beatfx-<stem>-<pid>/`` (cleaned in a ``finally``), then one
  final pass to the destination with the requested codec. Zero effects still
  runs a single copy-encode pass so inputs are always validated.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ingest import IngestError, probe_media

BEATFX_KINDS: frozenset[str] = frozenset({"shake", "punch", "flash", "rgb", "hue"})

#: Default effect duration in seconds per kind (flash is converted from frames).
_DEFAULT_DURATION: dict[str, float] = {
    "shake": 0.25,
    "punch": 0.3,
    "flash": 0.0,
    "rgb": 0.2,
    "hue": 0.4,
}

#: Kinds cycled by :func:`auto_schedule` in assignment order.
_AUTO_KINDS: tuple[str, ...] = ("punch", "shake", "flash")

#: Minimum gap (seconds) between consecutive auto-scheduled effect times.
_MIN_GAP_SECONDS = 0.2

#: Codecs apply_effects accepts.
_CODECS: frozenset[str] = frozenset({"h264", "prores"})

RESOLUTION_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}$")
MIN_WIDTH = 1920
MIN_HEIGHT = 1080


class BeatFxError(RuntimeError):
    """Raised when a beat-mapped effect cannot be applied."""


@dataclass(frozen=True)
class ScheduledEffect:
    """One effect scheduled at a point in time."""

    time: float
    kind: str
    params: dict


@dataclass(frozen=True)
class BeatFxResult:
    """The outcome of applying beat-mapped micro-effects."""

    source: Path
    destination: Path
    effects: tuple[ScheduledEffect, ...]
    passes: int
    fps: float
    resolution: str
    size_bytes: int


def parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse a WxH resolution string into ``(width, height)``.

    Raises:
        BeatFxError: the string is not WxH.
    """

    if not RESOLUTION_PATTERN.match(resolution):
        raise BeatFxError(f"resolution {resolution!r} must be WxH, for example 1920x1080")
    width_text, _, height_text = resolution.lower().partition("x")
    return int(width_text), int(height_text)


def _default_duration(kind: str, fps: float) -> float:
    """Resolved duration in seconds for ``kind`` (flash uses 2 frames)."""

    if kind == "flash":
        return 2.0 / fps
    return _DEFAULT_DURATION[kind]


def _shake_filter(start: float, end: float, fps: float, width: int, height: int) -> str:
    """Animated crop jitter gated to ``[start, end)``.

    ``crop`` does not support ffmpeg's ``enable``/timeline option in every
    build, so the gate is expressed inside the x/y expressions instead: the
    offset is zero outside ``[start, end)`` and the animated jitter inside it.
    """

    amp = 12
    freq = 24
    return (
        f"crop=w={width}:h={height}:"
        f"x='if(between(t,{start},{end}),{amp}*sin(2*PI*{freq}*t),0)':"
        f"y='if(between(t,{start},{end}),{amp}*cos(2*PI*{freq}*t),0)'"
    )


def _punch_filter(start: float, end: float, fps: float, width: int, height: int) -> str:
    """zoompan burst gated to ``[start, end)``."""

    start_frame = round(start * fps)
    end_frame = round(end * fps)
    frames = max(1, end_frame - start_frame)
    zoom = 1.08
    return (
        f"zoompan=z='if(between(in,{start_frame},{end_frame}),"
        f"{zoom}*min(1,(in-{start_frame})/{frames}),1)':"
        f"s={width}x{height}:fps={fps}:d=1"
    )


def _flash_filter(start: float, end: float, fps: float, width: int, height: int) -> str:
    """Full-frame white drawbox gated to ``[start, end)``."""

    return (
        f"drawbox=x=0:y=0:w={width}:h={height}:color=white:t=fill:enable='between(t,{start},{end})'"
    )


def _rgb_filter(start: float, end: float, fps: float, width: int, height: int) -> str:
    """Chromatic RGB split (impact.py lineage) gated to ``[start, end)``."""

    shift = 6
    return (
        f"split=3[r][g][b];"
        f"[r]crop=iw-{shift}:ih:{shift}:0[rp];"
        f"[g]crop=iw-{shift}:ih:{shift}:0[gp];"
        f"[b]crop=iw-{shift}:ih:{shift}:0[bp];"
        f"[rp][gp]overlay=x={shift}:y=0:shortest=1:enable='between(t,{start},{end})'[rg];"
        f"[rg][bp]overlay=x=-{shift}:y=0:shortest=1:enable='between(t,{start},{end})'"
    )


def _hue_filter(start: float, end: float, fps: float, width: int, height: int) -> str:
    """Hue rotation gated to ``[start, end)``."""

    degrees = 20
    return f"hue=h={degrees}:enable='between(t,{start},{end})'"


def _kind_filter(kind: str, start: float, end: float, fps: float, width: int, height: int) -> str:
    """Build the filter string for one ``kind`` over ``[start, end)``."""

    builders = {
        "shake": _shake_filter,
        "punch": _punch_filter,
        "flash": _flash_filter,
        "rgb": _rgb_filter,
        "hue": _hue_filter,
    }
    return builders[kind](start, end, fps, width, height)


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:  # pragma: no cover - ffmpeg is a dependency
        raise BeatFxError("ffmpeg is not installed") from error
    if result.returncode != 0:
        raise BeatFxError(f"ffmpeg failed for {argv[-1]}:\n{result.stderr.strip()[-2000:]}")
    return result


def _video_destination(source: Path, destination: Path | None) -> Path:
    """Resolve the output path the same way video_cutout resolves its MOV."""

    if destination is None:
        return source.with_name(f"{source.stem}.beatfx.mp4")
    if destination.is_dir():
        return destination / f"{source.stem}.beatfx.mp4"
    return destination


def _codec_args(codec: str) -> list[str]:
    """ffmpeg codec/pix_fmt args for the final destination pass."""

    if codec == "h264":
        return ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le"]
    raise BeatFxError(f"unknown codec {codec!r}; available: {', '.join(sorted(_CODECS))}")


def parse_schedule(payload: list[dict]) -> list[ScheduledEffect]:
    """Validate a raw schedule (kind/time/params) and sort by time.

    Raises:
        BeatFxError: an unknown kind, a negative time, or a malformed entry.
    """

    effects: list[ScheduledEffect] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            raise BeatFxError(f"schedule entry {index} is not an object")
        kind = entry.get("kind")
        if kind not in BEATFX_KINDS:
            known = ", ".join(sorted(BEATFX_KINDS))
            raise BeatFxError(f"unknown effect kind {kind!r}; available: {known}")
        time = entry.get("time")
        try:
            time = float(time)
        except (TypeError, ValueError) as error:
            raise BeatFxError(f"schedule entry {index} has invalid time {time!r}") from error
        if time < 0:
            raise BeatFxError("effect time must be non-negative")
        params = entry.get("params") or {}
        if not isinstance(params, dict):
            raise BeatFxError(f"schedule entry {index} has invalid params")
        effects.append(ScheduledEffect(time=time, kind=kind, params=dict(params)))
    effects.sort(key=lambda effect: effect.time)
    return effects


def auto_schedule(
    beats: dict,
    *,
    offset: float,
    duration: float,
    max_per_second: int = 2,
) -> list[ScheduledEffect]:
    """Deterministically pick beat times and assign kinds.

    Local beats are ``[b - offset for b in beats if offset <= b < offset +
    duration]``. Each maps to the energy of the window containing it, then the
    highest-energy candidates are greedily taken subject to a ``>= 0.2 s``
    gap, up to ``int(duration * max_per_second)``. Taken times are re-sorted
    and kinds cycle ``[punch, shake, flash]``.

    Raises:
        BeatFxError: no beats fall inside ``[offset, offset + duration)``.
    """

    if duration <= 0:
        raise BeatFxError(f"duration must be positive, got {duration}")
    if max_per_second < 1:
        raise BeatFxError(f"max_per_second must be at least 1, got {max_per_second}")

    rhythm_beats = beats.get("beats") or []
    energy = beats.get("energy") or []
    window = float(beats.get("window") or 1.0)

    local_beats = [b - offset for b in rhythm_beats if offset <= b < offset + duration]
    if not local_beats:
        raise BeatFxError(f"no beats fall inside [{offset:g}, {offset + duration:g})")

    window_count = len(energy)

    def window_energy(time: float) -> float:
        if window_count == 0:
            return 0.0
        index = min(max(int(time / window), 0), window_count - 1)
        return float(energy[index])

    candidates = sorted(local_beats, key=lambda t: window_energy(t), reverse=True)
    take_limit = int(duration * max_per_second)
    taken: list[float] = []
    for time in candidates:
        if len(taken) >= take_limit:
            break
        if all(abs(time - other) >= _MIN_GAP_SECONDS for other in taken):
            taken.append(time)
    taken.sort()

    effects: list[ScheduledEffect] = []
    for position, time in enumerate(taken):
        kind = _AUTO_KINDS[position % len(_AUTO_KINDS)]
        effects.append(ScheduledEffect(time=time, kind=kind, params={}))
    return effects


def apply_effects(
    source: Path,
    destination: Path | None = None,
    effects: list[ScheduledEffect] | None = None,
    *,
    fps: float = 60.0,
    resolution: str = "1920x1080",
    codec: str = "h264",
) -> BeatFxResult:
    """Apply scheduled micro-effects to ``source`` and write ``destination``.

    Runs one ffmpeg pass per effect over sequential intermediates, then one
    final pass with the requested codec. Zero effects still runs a single
    copy-encode pass so every input is validated.

    Raises:
        BeatFxError: validation or ffmpeg failure; ``IngestError`` from
            :func:`probe_media` is rewrapped as :class:`BeatFxError`.
    """

    source = Path(source)
    effects = list(effects or [])
    width, height = parse_resolution(resolution)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise BeatFxError(f"resolution {resolution} is below the 1920x1080 minimum floor")
    if codec not in _CODECS:
        raise BeatFxError(f"unknown codec {codec!r}; available: {', '.join(sorted(_CODECS))}")

    try:
        info = probe_media(source)
    except IngestError as error:
        raise BeatFxError(str(error)) from error

    destination = _video_destination(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    intermediates = Path("/tmp/opencode") / f"beatfx-{source.stem}-{os.getpid()}"
    intermediates.mkdir(parents=True, exist_ok=True)

    passes = 0
    current = source
    try:
        ordered = sorted(effects, key=lambda effect: effect.time)
        segments = [*ordered, None]  # sentinel: final encode pass
        for slot, effect in enumerate(segments):
            is_final = effect is None
            output = destination if is_final else intermediates / f"pass-{slot:03d}.mp4"

            argv = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(current)]

            if not is_final:
                start = effect.time
                duration = effect.params.get("duration", _default_duration(effect.kind, fps))
                end = start + duration
                filter_chain = _kind_filter(effect.kind, start, end, fps, width, height)
                argv += ["-vf", filter_chain]
                argv += ["-c:v", "libx264", "-crf", "12", "-preset", "fast"]
            else:
                argv += _codec_args(codec)

            if info.has_audio:
                argv += ["-c:a", "copy"]

            argv.append(str(output))
            _run(argv)
            passes += 1
            current = output

        if not destination.is_file():
            raise BeatFxError(f"ffmpeg reported success but wrote no file: {destination}")

        return BeatFxResult(
            source=source,
            destination=destination,
            effects=tuple(ordered),
            passes=passes,
            fps=float(fps),
            resolution=resolution,
            size_bytes=destination.stat().st_size,
        )
    finally:
        shutil.rmtree(intermediates, ignore_errors=True)
