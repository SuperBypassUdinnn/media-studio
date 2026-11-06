"""Beat-synced velocity ramps (fast-slow-fast, slow-snap, flash-freeze).

Schedules ramp events on musical triggers (downbeats, snares, build peaks)
identified from a beats.json rhythm analysis, then chains the matching
``media_studio.ramp`` functions one per pass so each pass reads the previous
pass's output.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ingest import IngestError, probe_media
from .ramp import (
    RAMP_CURVES,
    apply_speed_ramp,
)

BEATRAMP_KINDS: frozenset[str] = frozenset({"fast_slow_fast", "slow_snap", "flash_freeze"})

TRIGGER_MAP: dict[str, str] = {
    "downbeat": "fast_slow_fast",
    "snare": "slow_snap",
    "build_peak": "flash_freeze",
}

MIN_GAP_S: float = 0.5

RESOLUTION_PATTERN = re.compile(r"^\d{2,5}x\d{2,5}$")
MIN_WIDTH = 1920
MIN_HEIGHT = 1080


class BeatRampError(RuntimeError):
    """Raised when a beat-synced velocity ramp cannot be planned or rendered."""


@dataclass(frozen=True)
class RampEvent:
    """One velocity ramp scheduled at a point in time."""

    time: float
    kind: str
    trigger: str


@dataclass(frozen=True)
class BeatRampResult:
    """The outcome of applying beat-synced velocity ramps."""

    source: Path
    destination: Path
    events: tuple[RampEvent, ...]
    fps: float
    resolution: str
    size_bytes: int


def parse_resolution(resolution: str) -> tuple[int, int]:
    """Parse a WxH resolution string into ``(width, height)``.

    Raises:
        BeatRampError: the string is not WxH.
    """

    if not RESOLUTION_PATTERN.match(resolution):
        raise BeatRampError(f"resolution {resolution!r} must be WxH, for example 1920x1080")
    width_text, _, height_text = resolution.lower().partition("x")
    return int(width_text), int(height_text)


def schedule_ramp_events(
    beats: dict,
    *,
    offset: float,
    duration: float,
    map: dict[str, str] = TRIGGER_MAP,
    max_per_second: float = 1.0,
) -> list[RampEvent]:
    """Schedule ramp events on beat triggers with energy-ranked greedy selection.

    Trigger types are identified from ``beats``:

    * downbeats: ``beats["downbeats"]`` directly.
    * snares: beats in ``beats["beats"]`` that are not downbeats and fall inside
      a "high" section (checked against ``beats["sections"]``).
    * build_peaks: the last beat before each low-to-high section transition.

    Candidates are merged with the energy of the window containing each beat
    time, then greedily taken sorted by energy descending subject to a
    ``>= MIN_GAP_S`` gap, capped at ``int(duration * max_per_second)``. Taken
    events are re-sorted ascending by time.

    Collision tie-break when energies are equal: downbeat wins over snare wins
    over build_peak.
    """

    if duration <= 0:
        raise BeatRampError(f"duration must be positive, got {duration}")
    if max_per_second < 1:
        raise BeatRampError(f"max_per_second must be at least 1, got {max_per_second}")

    rhythm_beats = beats.get("beats") or []
    downbeats = beats.get("downbeats") or []
    energy = beats.get("energy") or []
    sections = beats.get("sections") or []
    window = float(beats.get("window") or 1.0)

    downbeat_set = set(downbeats)

    high_sections = [s for s in sections if s.get("level") == "high"]

    def in_high_section(time: float) -> bool:
        return any(s["start"] <= time < s["end"] for s in high_sections)

    def last_beat_before(time: float) -> float | None:
        before = [b for b in rhythm_beats if b < time]
        return max(before) if before else None

    build_peaks: list[float] = []
    for index in range(1, len(sections)):
        previous = sections[index - 1]
        current = sections[index]
        if previous.get("level") == "low" and current.get("level") == "high":
            peak = last_beat_before(current["start"])
            if peak is not None:
                build_peaks.append(peak)

    snares: list[float] = [b for b in rhythm_beats if b not in downbeat_set and in_high_section(b)]

    window_count = len(energy)

    def window_energy(time: float) -> float:
        if window_count == 0:
            return 0.0
        index = min(max(int(time / window), 0), window_count - 1)
        return float(energy[index])

    PRIORITY = {"downbeat": 0, "snare": 1, "build_peak": 2}

    candidates: list[tuple[float, str, str]] = []
    for time in downbeats:
        candidates.append((time, "downbeat", TRIGGER_MAP.get("downbeat", "fast_slow_fast")))
    for time in snares:
        candidates.append((time, "snare", TRIGGER_MAP.get("snare", "slow_snap")))
    for time in build_peaks:
        candidates.append((time, "build_peak", TRIGGER_MAP.get("build_peak", "flash_freeze")))

    take_limit = int(duration * max_per_second)
    ordered = sorted(candidates, key=lambda c: (-window_energy(c[0]), PRIORITY[c[1]]))
    taken: list[tuple[float, str, str]] = []
    for time, trigger, kind in ordered:
        if len(taken) >= take_limit:
            break
        if all(abs(time - other) >= MIN_GAP_S for other, _, _ in taken):
            taken.append((time, trigger, kind))

    taken.sort(key=lambda c: c[0])

    events: list[RampEvent] = []
    for time, trigger, kind in taken:
        events.append(RampEvent(time=time, kind=map.get(trigger, kind), trigger=trigger))
    return events


def _ramp_function_for(kind: str):
    """Return the ``ramp`` module function that implements ``kind``."""

    if kind not in RAMP_CURVES:
        raise BeatRampError(
            f"unknown ramp kind {kind!r}; available: {', '.join(sorted(RAMP_CURVES))}"
        )
    return apply_speed_ramp


def _video_destination(source: Path, destination: Path | None) -> Path:
    if destination is None:
        return source.with_name(f"{source.stem}.beatramp.mp4")
    if destination.is_dir():
        return destination / f"{source.stem}.beatramp.mp4"
    return destination


def _codec_args(codec: str) -> list[str]:
    if codec == "h264":
        return ["-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p"]
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuv444p10le"]
    raise BeatRampError(
        f"unknown codec {codec!r}; available: {', '.join(sorted({'h264', 'prores'}))}"
    )


def apply_ramp_events(
    source: Path,
    events: list[RampEvent],
    destination: Path | None = None,
    *,
    fps: float = 60.0,
    resolution: str = "1920x1080",
    codec: str = "h264",
) -> BeatRampResult:
    """Apply velocity ramps to ``source`` and write ``destination``.

    Each event calls the matching ramp function in order: event 0 reads
    ``source``, event 1 reads event 0's output, and so on. Intermediates live
    under ``/tmp/opencode/beatramp-<stem>-<pid>/`` and are cleaned in
    ``finally``. The final pass re-encodes to the destination codec. Zero events
    still runs a single copy-encode pass so every input is validated.

    Raises:
        BeatRampError: validation or rendering failure; ``IngestError`` from
            :func:`probe_media` is rewrapped as :class:`BeatRampError`.
    """

    source = Path(source)
    events = list(events)
    width, height = parse_resolution(resolution)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        raise BeatRampError(f"resolution {resolution} is below the 1920x1080 minimum floor")
    if codec not in {"h264", "prores"}:
        raise BeatRampError(f"unknown codec {codec!r}; available: h264, prores")

    try:
        info = probe_media(source)
    except IngestError as error:
        raise BeatRampError(str(error)) from error

    destination = _video_destination(source, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    intermediates = Path("/tmp/opencode") / f"beatramp-{source.stem}-{os.getpid()}"
    intermediates.mkdir(parents=True, exist_ok=True)

    try:
        ordered = sorted(events, key=lambda event: event.time)
        segments = [*ordered, None]  # sentinel: final encode pass
        current = source
        for slot, event in enumerate(segments):
            is_final = event is None
            output = destination if is_final else intermediates / f"pass-{slot:03d}.mp4"

            if not is_final:
                ramp_fn = _ramp_function_for(event.kind)
                ramp_fn(
                    current,
                    output,
                    curve=event.kind,
                    slow_window=(max(0.0, event.time - 0.25), event.time + 0.25),
                    slow_factor=1.0,
                    fast_factor=1.0,
                    fps=int(fps),
                )
            else:
                argv = [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(current),
                ]
                argv += _codec_args(codec)
                if info.has_audio:
                    argv += ["-c:a", "copy"]
                argv.append(str(output))
                try:
                    result = subprocess.run(argv, capture_output=True, text=True)
                except FileNotFoundError as error:
                    raise BeatRampError("ffmpeg is not installed") from error
                if result.returncode != 0:
                    raise BeatRampError(
                        f"ffmpeg failed for {output}:\n{result.stderr.strip()[-2000:]}"
                    )

            current = output

        if not destination.is_file():
            raise BeatRampError(f"ffmpeg reported success but wrote no file: {destination}")

        return BeatRampResult(
            source=source,
            destination=destination,
            events=tuple(ordered),
            fps=float(fps),
            resolution=resolution,
            size_bytes=destination.stat().st_size,
        )
    finally:
        shutil.rmtree(intermediates, ignore_errors=True)
