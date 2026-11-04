"""Non-linear velocity ramping with optical flow frame interpolation.

Applies V-curve speed ramps (fast-slow-fast, slow-snap, flash-freeze) to a
clip by slicing it into per-segment speed adjustments, reassembling them with
the FFmpeg concat filter, and interpolating to the target frame rate with
minterpolate (mi_mode=mci, mc_mode=aobmc). When optical flow is too heavy
(low VRAM, OOM, or any ffmpeg failure) the render is automatically retried
with mi_mode=blend and fallback_used is recorded on the result.

Output is rendered at 1920x1080 and the requested frame rate (60 fps by
default).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ingest import probe_media

#: Registered velocity curves.
RAMP_CURVES: tuple[str, ...] = ("fast_slow_fast", "slow_snap", "flash_freeze")

#: Resolution forced on every ramped deliverable.
RAMP_WIDTH = 1920
RAMP_HEIGHT = 1080


class RampError(RuntimeError):
    """Raised when a speed ramp cannot be planned or rendered."""


@dataclass(frozen=True)
class SpeedSegment:
    """One constant-speed slice of a speed ramp.

    ``start_time`` and ``end_time`` are source-clip times in seconds;
    ``speed_factor`` is the playback multiplier applied to that slice
    (2.0 = twice as fast, 0.25 = quarter speed).
    """

    start_time: float
    end_time: float
    speed_factor: float


@dataclass(frozen=True)
class RampResult:
    """The outcome of one speed ramp."""

    source: Path
    destination: Path
    curve: str
    duration: float
    fps: int
    optical_flow: bool
    fallback_used: bool
    size_bytes: int


def calculate_ramp_segments(
    clip_duration: float,
    slow_window: tuple[float, float],
    curve: str = "fast_slow_fast",
    slow_factor: float = 0.25,
    fast_factor: float = 2.5,
) -> list[SpeedSegment]:
    """Split ``clip_duration`` into constant-speed segments for ``curve``.

    Every curve covers the whole clip in up to three slices:

    * ``fast_slow_fast``: ``fast_factor`` outside the window,
      ``slow_factor`` inside it.
    * ``slow_snap``: normal speed (1.0x), ``slow_factor`` inside the
      window, snap back to normal speed.
    * ``flash_freeze``: normal speed, near-freeze (0.1x) inside the
      window, snap back to normal speed.

    Raises:
        RampError: the window is negative, empty, outside the clip, a
            speed factor is not positive, or the curve is unknown.
    """

    try:
        start, end = slow_window
    except (TypeError, ValueError) as error:
        raise RampError(f"slow_window must be a (start, end) pair, got {slow_window!r}") from error

    if start < 0:
        raise RampError(f"slow_window start must not be negative, got {start}")
    if not start < end:
        raise RampError("slow_window start must be strictly less than end")
    if end > clip_duration:
        raise RampError(f"slow_window end {end} is outside clip duration {clip_duration}")
    if slow_factor <= 0:
        raise RampError("slow_factor must be positive")
    if fast_factor <= 0:
        raise RampError("fast_factor must be positive")
    if curve not in RAMP_CURVES:
        known = ", ".join(RAMP_CURVES)
        raise RampError(f"unknown ramp curve {curve!r}; available: {known}")

    if curve == "fast_slow_fast":
        outer, inner = fast_factor, slow_factor
    elif curve == "slow_snap":
        outer, inner = 1.0, slow_factor
    else:  # curve == "flash_freeze"
        outer, inner = 1.0, 0.1

    segments: list[SpeedSegment] = []
    if start > 0:
        segments.append(SpeedSegment(0.0, start, outer))
    segments.append(SpeedSegment(start, end, inner))
    if end < clip_duration:
        segments.append(SpeedSegment(end, clip_duration, outer))
    return segments


def _build_filter_graph(segments: list[SpeedSegment], *, fps: int, optical_flow: bool) -> str:
    """Build the FFmpeg filtergraph that retimes and normalises the clip."""

    links: list[str] = []
    split_outputs = "".join(f"[s{i}]" for i in range(len(segments)))
    links.append(f"[0:v]split={len(segments)}{split_outputs}")

    for i, segment in enumerate(segments):
        links.append(
            f"[s{i}]trim=start={segment.start_time}:{segment.end_time},"
            f"setpts=(PTS-STARTPTS)/{segment.speed_factor}[s{i}p]"
        )

    links.append(
        "".join(f"[s{i}p]" for i in range(len(segments))) + f"concat=n={len(segments)}:v=1:a=0[v]"
    )

    if optical_flow:
        links.append(f"[v]minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1[vi]")
        tail_input = "vi"
    else:
        tail_input = "v"

    links.append(
        f"[{tail_input}]fps={fps},"
        f"scale={RAMP_WIDTH}:{RAMP_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={RAMP_WIDTH}:{RAMP_HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p[out]"
    )
    return ";".join(links)


def _render(
    source: Path,
    destination: Path,
    segments: list[SpeedSegment],
    *,
    fps: int,
    optical_flow: bool,
) -> None:
    """Run one ffmpeg pass; raises RampError when ffmpeg exits non-zero."""

    argv = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-filter_complex",
        _build_filter_graph(segments, fps=fps, optical_flow=optical_flow),
        "-map",
        "[out]",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        str(destination),
    ]
    try:
        result = subprocess.run(argv, capture_output=True, text=True)
    except FileNotFoundError as error:
        raise RampError("ffmpeg is not installed") from error
    if result.returncode != 0:
        detail = result.stderr.strip()[-2000:]
        raise RampError(f"ffmpeg failed for {source}:\n{detail}")


def apply_speed_ramp(
    source: Path,
    destination: Path | None = None,
    *,
    curve: str = "fast_slow_fast",
    slow_window: tuple[float, float],
    slow_factor: float = 0.25,
    fast_factor: float = 2.5,
    optical_flow: bool = True,
    fps: int = 60,
) -> RampResult:
    """Re-time ``source`` along ``curve`` and write it to ``destination``.

    The source is probed via :func:`media_studio.ingest.probe_media`, sliced
    into :func:`calculate_ramp_segments`, reassembled with the concat filter,
    and scaled to 1920x1080 at ``fps`` (default 60).

    When ``optical_flow`` is enabled the first pass uses
    ``minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1``. If that
    pass fails (low VRAM, OOM, or any non-zero ffmpeg exit code) it is
    retried with ``mi_mode=blend`` and ``fallback_used`` is set on the
    result.

    Args:
        source: the clip to re-time.
        destination: where to write; defaults to ``<stem>.ramp.mp4`` beside
            the source.
        curve: one of ``RAMP_CURVES``.
        slow_window: ``(start, end)`` source-clip seconds of the slow part.
        slow_factor: playback multiplier inside the window.
        fast_factor: playback multiplier outside the window
            (``fast_slow_fast`` only).
        optical_flow: attempt motion-compensated interpolation first.
        fps: output frame rate (default 60).

    Returns:
        RampResult describing the rendered file.

    Raises:
        RampError: validation or rendering failed (after the blend fallback).
        IngestError: the source cannot be probed.
    """

    source = Path(source)
    if curve not in RAMP_CURVES:
        known = ", ".join(RAMP_CURVES)
        raise RampError(f"unknown ramp curve {curve!r}; available: {known}")
    if slow_factor <= 0:
        raise RampError("slow_factor must be positive")
    if fast_factor <= 0:
        raise RampError("fast_factor must be positive")

    info = probe_media(source)
    segments = calculate_ramp_segments(
        info.duration, slow_window, curve, slow_factor=slow_factor, fast_factor=fast_factor
    )

    if destination is None:
        destination = source.with_name(f"{source.stem}.ramp.mp4")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fallback_used = False
    if optical_flow:
        try:
            _render(source, destination, segments, fps=fps, optical_flow=True)
        except RampError:
            fallback_used = True
            _render(source, destination, segments, fps=fps, optical_flow=False)
    else:
        _render(source, destination, segments, fps=fps, optical_flow=False)

    out_info = probe_media(destination)
    return RampResult(
        source=source,
        destination=destination,
        curve=curve,
        duration=out_info.duration,
        fps=fps,
        optical_flow=optical_flow,
        fallback_used=fallback_used,
        size_bytes=destination.stat().st_size,
    )
